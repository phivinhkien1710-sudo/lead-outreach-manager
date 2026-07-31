import frappe
from frappe.tests.utils import FrappeTestCase

from lead_outreach_manager.services.contacts import get_or_create_generic_contact
from lead_outreach_manager.services.outreach_emails import create_outreach_email

TEST_PREFIX = "TEST-LOM-GATING"


class TestOutreachGating(FrappeTestCase):
	def setUp(self):
		self.uen = f"{TEST_PREFIX}-UEN-1"
		self.template_name = f"{TEST_PREFIX}-TEMPLATE"
		self._cleanup_stale()  # defensive: survive a prior run's incomplete tearDown
		self.profile = self._create_profile()
		self.contact = get_or_create_generic_contact(self.profile)
		self.template = self._create_template()

	def tearDown(self):
		# The gate now has a passing case, so a test here can create a real
		# Outreach Email + Communication on this shared-data site — clean them
		# up the same way test_outreach_email_flow does, or they leak.
		self._delete_generated_outreach(self.profile.name)
		frappe.delete_doc("Email Template", self.template.name, force=True, ignore_permissions=True)
		frappe.delete_doc("Contact", self.contact.name, force=True, ignore_permissions=True)
		frappe.delete_doc("Company Profile", self.profile.name, force=True, ignore_permissions=True)
		frappe.db.commit()  # FrappeTestCase doesn't auto-commit/rollback per test

	def _delete_generated_outreach(self, profile_name):
		for row in frappe.get_all(
			"Outreach Email", filters={"company_profile": profile_name}, fields=["name", "communication"]
		):
			if row.communication:
				frappe.delete_doc("Communication", row.communication, force=True, ignore_permissions=True)
			frappe.delete_doc("Outreach Email", row.name, force=True, ignore_permissions=True)
		frappe.db.delete("Email Queue", {"reference_name": profile_name})

	def test_do_not_contact_blocks_generation(self):
		self.profile.do_not_contact = 1
		self.profile.save(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			create_outreach_email(self.profile.name, self.contact.name, self.template.name)

	def test_unsubscribed_contact_blocks_generation(self):
		self.contact.unsubscribed = 1
		self.contact.save(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			create_outreach_email(self.profile.name, self.contact.name, self.template.name)

	def test_no_email_contact_blocks_generation(self):
		self.profile.has_email_contact = 0
		self.profile.save(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			create_outreach_email(self.profile.name, self.contact.name, self.template.name)

	def test_no_email_contact_still_blocks_on_unverified_guess(self):
		"""has_email_contact=0 plus an address that was never verified stays
		blocked — a human Confirm Name puts the top *guess* on Contact.email_id
		by product decision, and that alone must not open the gate."""
		self.profile.has_email_contact = 0
		self.profile.save(ignore_permissions=True)
		self._add_candidate_row(verification_status="Catch-All", verified_email=self.contact.email_id)

		with self.assertRaises(frappe.ValidationError):
			create_outreach_email(self.profile.name, self.contact.name, self.template.name)

	def test_verified_personal_email_allows_generation_without_generic_contact(self):
		"""The gap this covers: profiles imported with only a phone/contact-form
		keep has_email_contact=0 forever, but verification can later confirm a
		deliverable personal address and promote it onto the Contact."""
		self.profile.has_email_contact = 0
		self.profile.save(ignore_permissions=True)
		self._add_candidate_row(verification_status="Verified", verified_email=self.contact.email_id)

		result = create_outreach_email(self.profile.name, self.contact.name, self.template.name)

		outreach = frappe.get_doc("Outreach Email", result["outreach_email"])
		self.assertEqual(outreach.recipient_email, self.contact.email_id)

	def _add_candidate_row(self, verification_status, verified_email):
		profile = frappe.get_doc("Company Profile", self.profile.name)
		profile.append(
			"candidate_names",
			{
				"name_text": "Verified Person",
				"title_text": "Director",
				"source_document_id": 1,
				"confirmed": 1,
				"contact": self.contact.name,
				"verification_status": verification_status,
				"verified_email": verified_email,
			},
		)
		profile.save(ignore_permissions=True)

	def _cleanup_stale(self):
		from lead_outreach_manager.services.contacts import find_linked_contact

		profile_name = frappe.db.exists("Company Profile", self.uen)
		if profile_name:
			self._delete_generated_outreach(profile_name)
			contact_name = find_linked_contact(profile_name)
			if contact_name:
				frappe.delete_doc("Contact", contact_name, force=True, ignore_permissions=True)
			frappe.delete_doc("Company Profile", profile_name, force=True, ignore_permissions=True)
		frappe.delete_doc("Email Template", self.template_name, force=True, ignore_permissions=True)

	def _create_profile(self):
		profile = frappe.new_doc("Company Profile")
		profile.uen = self.uen
		profile.entity_name = "Test Gating Co Pte Ltd"
		profile.domain_status = "verified"
		profile.contact_status = "has_generic_contact"
		profile.has_email_contact = 1
		profile.primary_email = "info@testgating.example"
		profile.do_not_contact = 0
		profile.insert(ignore_permissions=True)
		return profile

	def _create_template(self):
		template = frappe.new_doc("Email Template")
		template.name = self.template_name
		template.subject = "Introducing our services to {{ doc.entity_name }}"
		template.response = "Hello {{ contact.first_name or 'there' }}."
		template.insert(ignore_permissions=True)
		return template
