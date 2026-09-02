import frappe
from frappe.tests.utils import FrappeTestCase

from lead_outreach_manager.services.contacts import get_or_create_generic_contact
from lead_outreach_manager.services.outreach_batches import run_background_batch_scheduling

TEST_PREFIX = "TEST-LOM-BATCH"


class TestOutreachBatchScheduling(FrappeTestCase):
	def setUp(self):
		# See test_outreach_email_flow.py — this fresh site has no configured
		# outgoing Email Account, so schedule_send()'s comm.send_email() call
		# needs emails muted to resolve a (synthetic) outgoing account at all.
		frappe.flags.mute_emails = True
		self.profiles = []
		self.contacts = []
		self.outreach_emails = []
		self.communications = []
		self.import_runs = []

		self._cleanup_stale()  # defensive: survive a prior run's incomplete tearDown

		for index in range(3):
			profile = self._create_profile(index)
			contact = get_or_create_generic_contact(profile)
			comm_name = self._create_draft_communication(profile, contact)
			outreach = self._create_ready_outreach_email(profile, contact, comm_name)

			self.profiles.append(profile)
			self.contacts.append(contact)
			self.communications.append(comm_name)
			self.outreach_emails.append(outreach)

		# Third company opts out after its Outreach Email was already drafted —
		# schedule_send must catch this at scheduling time, not just at generation time.
		self.profiles[2].do_not_contact = 1
		self.profiles[2].save(ignore_permissions=True)

		self.batch = self._create_batch()

	def tearDown(self):
		frappe.flags.mute_emails = False
		frappe.delete_doc("Outreach Batch", self.batch.name, force=True, ignore_permissions=True)
		for outreach in self.outreach_emails:
			frappe.delete_doc("Outreach Email", outreach.name, force=True, ignore_permissions=True)
		for comm_name in self.communications:
			frappe.delete_doc("Communication", comm_name, force=True, ignore_permissions=True)
		for contact in self.contacts:
			frappe.delete_doc("Contact", contact.name, force=True, ignore_permissions=True)
		for profile in self.profiles:
			frappe.delete_doc("Company Profile", profile.name, force=True, ignore_permissions=True)
		for import_run in self.import_runs:
			frappe.delete_doc("Lead CSV Import Run", import_run, force=True, ignore_permissions=True)
		frappe.delete_doc("Email Template", f"{TEST_PREFIX}-TEMPLATE", force=True, ignore_permissions=True)
		frappe.db.commit()  # FrappeTestCase doesn't auto-commit/rollback per test

	def test_batch_staggers_and_skips_opted_out(self):
		run_background_batch_scheduling(self.batch.name)

		batch = frappe.get_doc("Outreach Batch", self.batch.name)
		self.assertEqual(batch.total_targets, 3)
		self.assertEqual(batch.scheduled_count, 2)
		self.assertEqual(batch.skipped_count, 1)
		self.assertIn(batch.status, ("Completed", "Completed With Errors"))

		refreshed = [frappe.get_doc("Outreach Email", o.name) for o in self.outreach_emails]
		self.assertEqual(refreshed[0].status, "Scheduled")
		self.assertEqual(refreshed[1].status, "Scheduled")
		self.assertEqual(refreshed[2].status, "Cancelled")

		gap_seconds = (
			frappe.utils.get_datetime(refreshed[1].send_after) - frappe.utils.get_datetime(refreshed[0].send_after)
		).total_seconds()
		expected_gap = 3600 / batch.rate_limit_per_hour
		self.assertAlmostEqual(gap_seconds, expected_gap, delta=1)

	def test_import_batch_schedules_only_matching_drafts(self):
		import_run = frappe.get_doc({
			"doctype": "Lead CSV Import Run", "country": "Singapore"
		}).insert(ignore_permissions=True)
		self.import_runs.append(import_run.name)
		self.outreach_emails[0].import_run = import_run.name
		self.outreach_emails[0].save(ignore_permissions=True)
		self.batch.import_run = import_run.name
		self.batch.save(ignore_permissions=True)

		run_background_batch_scheduling(self.batch.name)

		batch = frappe.get_doc("Outreach Batch", self.batch.name)
		self.assertEqual(batch.total_targets, 1)
		self.assertEqual(batch.scheduled_count, 1)
		self.assertEqual(frappe.db.get_value("Outreach Email", self.outreach_emails[0].name, "status"), "Scheduled")
		self.assertEqual(frappe.db.get_value("Outreach Email", self.outreach_emails[1].name, "status"), "Ready to Send")

	def _cleanup_stale(self):
		from lead_outreach_manager.services.contacts import find_linked_contact

		# Deliberately does NOT sweep every Outreach Batch site-wide — this
		# site's DB is shared with real data, and "this test is the only
		# place that creates one" stopped being true the moment real Outreach
		# Batch usage started. tearDown() already deletes exactly the batch
		# this test creates; a leftover from a crashed prior run is harmless
		# orphaned clutter, not worth risking real data to auto-clean.
		for index in range(3):
			uen = f"{TEST_PREFIX}-UEN-{index}"
			profile_name = frappe.db.exists("Company Profile", uen)
			if not profile_name:
				continue
			for row in frappe.get_all(
				"Outreach Email", filters={"company_profile": profile_name}, fields=["name", "communication"],
			):
				if row.communication:
					frappe.delete_doc("Communication", row.communication, force=True, ignore_permissions=True)
				frappe.delete_doc("Outreach Email", row.name, force=True, ignore_permissions=True)
			contact_name = find_linked_contact(profile_name)
			if contact_name:
				frappe.delete_doc("Contact", contact_name, force=True, ignore_permissions=True)
			frappe.delete_doc("Company Profile", profile_name, force=True, ignore_permissions=True)
		frappe.delete_doc("Email Template", f"{TEST_PREFIX}-TEMPLATE", force=True, ignore_permissions=True)

	def _create_profile(self, index):
		profile = frappe.new_doc("Company Profile")
		profile.uen = f"{TEST_PREFIX}-UEN-{index}"
		profile.entity_name = f"Test Batch Co {index} Pte Ltd"
		profile.domain_status = "verified"
		profile.contact_status = "has_generic_contact"
		profile.has_email_contact = 1
		profile.primary_email = f"info{index}@testbatch.example"
		profile.do_not_contact = 0
		# This site's DB holds real Outreach Email records too, and
		# _get_eligible_targets has no test-side scoping by design (a real
		# batch must see every eligible email) — filter the batch down to
		# just this test's own profiles via the industry_tier filter it
		# already supports, rather than reaching into unrelated real targets.
		profile.industry_tier = TEST_PREFIX
		profile.insert(ignore_permissions=True)
		return profile

	def _create_draft_communication(self, profile, contact):
		from frappe.core.doctype.communication.email import make

		result = make(
			doctype="Company Profile",
			name=profile.name,
			subject=f"Hello {profile.entity_name}",
			content="Test content",
			recipients=[contact.email_id],
			send_email=False,
		)
		return result["name"]

	def _create_ready_outreach_email(self, profile, contact, communication_name):
		outreach = frappe.new_doc("Outreach Email")
		outreach.status = "Ready to Send"
		outreach.company_profile = profile.name
		outreach.contact = contact.name
		outreach.email_template = self._get_or_create_shared_template()
		outreach.communication = communication_name
		outreach.recipient_email = contact.email_id
		outreach.subject = f"Hello {profile.entity_name}"
		outreach.insert(ignore_permissions=True)
		return outreach

	def _get_or_create_shared_template(self):
		template_name = f"{TEST_PREFIX}-TEMPLATE"
		if not frappe.db.exists("Email Template", template_name):
			template = frappe.new_doc("Email Template")
			template.name = template_name
			template.subject = "Hello {{ doc.entity_name }}"
			template.response = "Hi {{ contact.first_name or 'there' }}."
			template.insert(ignore_permissions=True)
		return template_name

	def _create_batch(self):
		batch = frappe.new_doc("Outreach Batch")
		batch.status = "Draft"
		batch.filter_company_industry_tier = TEST_PREFIX
		batch.rate_limit_per_hour = 60
		batch.business_hours_only = 0
		batch.start_after = frappe.utils.now_datetime()
		batch.insert(ignore_permissions=True)
		return batch
