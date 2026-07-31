import frappe
from frappe.tests.utils import FrappeTestCase

from lead_outreach_manager.services.candidate_names import apply_confirmation
from lead_outreach_manager.services.contacts import get_or_create_generic_contact
from lead_outreach_manager.services.outreach_emails import (
	approve_outreach_email,
	create_outreach_email,
	schedule_send,
)

TEST_PREFIX = "TEST-LOM-FLOW"


class TestOutreachEmailFlow(FrappeTestCase):
	def setUp(self):
		# This fresh site has no configured outgoing Email Account (a real
		# precondition documented in the README) — muting emails is Frappe's
		# own built-in mechanism for this, and makes EmailAccount.find_outgoing()
		# resolve to a synthetic account instead of returning None, which is
		# what actually lets Communication.send_email() write an Email Queue row.
		frappe.flags.mute_emails = True
		self.uen = f"{TEST_PREFIX}-UEN-1"
		self.template_name = f"{TEST_PREFIX}-TEMPLATE"
		self._cleanup_stale()  # defensive: survive a prior run's incomplete tearDown
		self.profile = self._create_profile()
		self.contact = get_or_create_generic_contact(self.profile)
		self.template = self._create_template()

	def tearDown(self):
		frappe.flags.mute_emails = False
		for row in frappe.get_all("Outreach Email", filters={"company_profile": self.profile.name}, fields=["name", "communication"]):
			if row.communication:
				frappe.delete_doc("Communication", row.communication, force=True, ignore_permissions=True)
			frappe.delete_doc("Outreach Email", row.name, force=True, ignore_permissions=True)
		frappe.db.delete("Email Queue", {"reference_name": self.profile.name})
		frappe.delete_doc("Email Template", self.template.name, force=True, ignore_permissions=True)
		frappe.delete_doc("Contact", self.contact.name, force=True, ignore_permissions=True)
		frappe.delete_doc("Company Profile", self.profile.name, force=True, ignore_permissions=True)
		frappe.db.commit()  # FrappeTestCase doesn't auto-commit/rollback per test

	def test_full_generate_approve_schedule_happy_path(self):
		result = create_outreach_email(self.profile.name, self.contact.name, self.template.name)
		outreach = frappe.get_doc("Outreach Email", result["outreach_email"])
		self.assertEqual(outreach.status, "Draft")

		comm = frappe.get_doc("Communication", result["communication"])
		self.assertEqual(comm.sent_or_received, "Sent")
		self.assertIn("Test Flow Co Pte Ltd", comm.subject)

		approve_outreach_email(outreach.name)
		outreach_after_approve = frappe.get_doc("Outreach Email", outreach.name)
		self.assertEqual(outreach_after_approve.status, "Ready to Send")
		self.assertEqual(outreach_after_approve.approved_by, frappe.session.user)

		send_after = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=30)
		schedule_send(outreach.name, send_after=send_after)

		outreach_final = frappe.get_doc("Outreach Email", outreach.name)
		self.assertEqual(outreach_final.status, "Scheduled")

		queue_row = frappe.db.get_value(
			"Email Queue", {"communication": result["communication"]}, ["status", "send_after"], as_dict=True,
		)
		self.assertIsNotNone(queue_row)
		self.assertEqual(queue_row.status, "Not Sent")

	def test_generated_email_snapshots_guessed_addresses_but_recipient_stays_generic(self):
		self.profile.domain = "testflow.example"
		self.profile.append(
			"candidate_names",
			{"name_text": "Jane Tan", "title_text": "Director", "source_document_id": 1},
		)
		self.profile.save(ignore_permissions=True)
		row = self.profile.candidate_names[0]
		apply_confirmation(self.profile, row, source="Auto - LLM")  # no redirect — matches the LLM auto path
		self.profile.save(ignore_permissions=True)

		result = create_outreach_email(self.profile.name, self.contact.name, self.template.name)
		outreach = frappe.get_doc("Outreach Email", result["outreach_email"])

		self.assertEqual(outreach.guessed_email_1, "jane.tan@testflow.example")
		self.assertEqual(outreach.guessed_pattern_1, "first.last")
		self.assertEqual(outreach.guessed_email_2, "jane@testflow.example")
		self.assertEqual(outreach.guessed_email_3, "jtan@testflow.example")
		# Informational only — the recipient must stay the generic address,
		# same safety rule as everywhere else in the app.
		self.assertEqual(outreach.recipient_email, "info@testflow.example")

	def _cleanup_stale(self):
		from lead_outreach_manager.services.contacts import find_linked_contact

		profile_name = frappe.db.exists("Company Profile", self.uen)
		if profile_name:
			for row in frappe.get_all(
				"Outreach Email", filters={"company_profile": profile_name}, fields=["name", "communication"],
			):
				if row.communication:
					frappe.delete_doc("Communication", row.communication, force=True, ignore_permissions=True)
				frappe.delete_doc("Outreach Email", row.name, force=True, ignore_permissions=True)
			frappe.db.delete("Email Queue", {"reference_name": profile_name})
			contact_name = find_linked_contact(profile_name)
			if contact_name:
				frappe.delete_doc("Contact", contact_name, force=True, ignore_permissions=True)
			frappe.delete_doc("Company Profile", profile_name, force=True, ignore_permissions=True)
		frappe.delete_doc("Email Template", self.template_name, force=True, ignore_permissions=True)

	def _create_profile(self):
		profile = frappe.new_doc("Company Profile")
		profile.uen = self.uen
		profile.entity_name = "Test Flow Co Pte Ltd"
		profile.domain_status = "verified"
		profile.contact_status = "has_generic_contact"
		profile.has_email_contact = 1
		profile.primary_email = "info@testflow.example"
		profile.do_not_contact = 0
		profile.insert(ignore_permissions=True)
		return profile

	def _create_template(self):
		template = frappe.new_doc("Email Template")
		template.name = self.template_name
		template.subject = "Introducing our services to {{ doc.entity_name }}"
		template.response = "Hello {{ contact.first_name or 'there' }}, this is about {{ doc.entity_name }}."
		template.insert(ignore_permissions=True)
		return template
