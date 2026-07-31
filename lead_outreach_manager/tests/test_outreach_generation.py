from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from lead_outreach_manager.services.candidate_names import apply_confirmation
from lead_outreach_manager.services.contacts import find_linked_contact, get_or_create_generic_contact
from lead_outreach_manager.services import outreach_generation as generation_module
from lead_outreach_manager.services.outreach_generation import run_background_generation

TEST_PREFIX = "TEST-LOM-GENERATE"


class TestOutreachGeneration(FrappeTestCase):
	def setUp(self):
		self.profiles = []
		self.runs = []
		self.outreach_emails = []
		self.communications = []
		self._cleanup_stale()  # defensive: survive a prior run's incomplete tearDown
		self.template_name = self._get_or_create_shared_template()

	def tearDown(self):
		for run in self.runs:
			frappe.delete_doc("Outreach Generation Run", run, force=True, ignore_permissions=True)
		for outreach in frappe.get_all(
			"Outreach Email", filters={"company_profile": ["in", [p.name for p in self.profiles]]}, fields=["name", "communication"]
		):
			if outreach.communication:
				frappe.delete_doc("Communication", outreach.communication, force=True, ignore_permissions=True)
			frappe.delete_doc("Outreach Email", outreach.name, force=True, ignore_permissions=True)
		for profile in self.profiles:
			contact_name = find_linked_contact(profile.name)
			if contact_name:
				frappe.delete_doc("Contact", contact_name, force=True, ignore_permissions=True)
			frappe.delete_doc("Company Profile", profile.name, force=True, ignore_permissions=True)
		frappe.delete_doc("Email Template", self.template_name, force=True, ignore_permissions=True)
		# FrappeTestCase does not auto-commit or auto-rollback per test — cleanup
		# only sticks if explicitly committed here.
		frappe.db.commit()

	def test_generates_for_auto_confirmed_contacts(self):
		self._auto_confirm(0, "Jane Tan")
		self._auto_confirm(1, "Alice Wong")

		run_name = self._run_generation()

		run = frappe.get_doc("Outreach Generation Run", run_name)
		self.assertEqual(run.total_targets, 2)
		self.assertEqual(run.generated_count, 2)
		self.assertEqual(run.skipped_count, 0)
		self.assertIn(run.status, ("Completed", "Completed With Errors"))

		for profile in self.profiles:
			outreach = frappe.get_all("Outreach Email", filters={"company_profile": profile.name}, fields=["name", "status"])
			self.assertEqual(len(outreach), 1)
			self.assertEqual(outreach[0].status, "Draft")

	def test_do_not_contact_is_skipped_not_failed(self):
		profile = self._auto_confirm(0, "Jane Tan")
		profile.do_not_contact = 1
		profile.save(ignore_permissions=True)

		run_name = self._run_generation()

		run = frappe.get_doc("Outreach Generation Run", run_name)
		self.assertEqual(run.generated_count, 0)
		self.assertEqual(run.skipped_count, 1)
		self.assertEqual(run.error_count, 0)
		self.assertEqual(frappe.db.count("Outreach Email", {"company_profile": profile.name}), 0)

	def test_human_confirmed_candidates_are_not_targeted(self):
		profile = self._create_profile(0, "Jane Tan")
		row = profile.candidate_names[0]
		result = apply_confirmation(profile, row, source="Human", redirect_to_top_guess=True)
		# Human path deliberately never sets classification_status — that
		# field belongs to the automated funnel only.
		profile.save(ignore_permissions=True)
		self.assertTrue(result["confirmed"])

		run_name = self._run_generation()

		run = frappe.get_doc("Outreach Generation Run", run_name)
		self.assertEqual(run.total_targets, 0)
		self.assertEqual(frappe.db.count("Outreach Email", {"company_profile": profile.name}), 0)

	def test_second_run_does_not_duplicate(self):
		self._auto_confirm(0, "Jane Tan")
		self._run_generation()
		first_count = frappe.db.count("Outreach Email", {"company_profile": self.profiles[0].name})

		run_name = self._run_generation()

		run = frappe.get_doc("Outreach Generation Run", run_name)
		self.assertEqual(run.total_targets, 0)
		self.assertEqual(
			frappe.db.count("Outreach Email", {"company_profile": self.profiles[0].name}), first_count
		)

	def test_limit_rows_caps_the_run(self):
		self._auto_confirm(0, "Jane Tan")
		self._auto_confirm(1, "Alice Wong")

		run_name = self._run_generation(limit_rows=1)

		run = frappe.get_doc("Outreach Generation Run", run_name)
		self.assertEqual(run.total_targets, 1)
		self.assertEqual(run.generated_count, 1)

	# ------------------------------------------------------------------
	# Helpers
	# ------------------------------------------------------------------

	def _run_generation(self, limit_rows=None):
		"""This site's DB is shared with real data — currently including a live
		classification backfill producing real Auto Confirmed rows — so
		without scoping here, run_background_generation's target query would
		also pick up and generate real Outreach Email/Communication drafts
		against real companies. Scope target selection to this test's own
		profiles only."""
		own_profile_names = {p.name for p in self.profiles}
		real_get_targets = generation_module._get_auto_confirmed_targets

		def scoped_get_targets(limit_rows=None):
			targets = [
				t for t in real_get_targets(limit_rows=None) if t["company_profile"] in own_profile_names
			]
			return targets[:limit_rows] if limit_rows else targets

		run = frappe.new_doc("Outreach Generation Run")
		run.email_template = self.template_name
		run.limit_rows = limit_rows
		run.insert(ignore_permissions=True)
		self.runs.append(run.name)
		with mock.patch.object(generation_module, "_get_auto_confirmed_targets", side_effect=scoped_get_targets):
			run_background_generation(run.name)
		return run.name

	def _auto_confirm(self, index, name_text):
		profile = self._create_profile(index, name_text)
		row = profile.candidate_names[0]
		apply_confirmation(profile, row, source="Auto - LLM")
		row.classification_status = "Auto Confirmed"
		row.classification_method = "LLM"
		profile.save(ignore_permissions=True)
		return profile

	def _domain(self, index):
		return f"testgenerate{index}.example"

	def _create_profile(self, index, name_text):
		profile = frappe.new_doc("Company Profile")
		profile.uen = f"{TEST_PREFIX}-UEN-{index}"
		profile.entity_name = f"Test Generate Co {index} Pte Ltd"
		profile.domain = self._domain(index)
		profile.domain_status = "verified"
		profile.contact_status = "has_generic_contact"
		profile.has_email_contact = 1
		profile.primary_email = f"info@{self._domain(index)}"
		profile.do_not_contact = 0
		profile.append(
			"candidate_names",
			{
				"name_text": name_text,
				"title_text": "Director",
				"source_url": f"https://{self._domain(index)}/about",
				"extraction_method": "regex_proximity_v1",
				"source_document_id": 1,
			},
		)
		profile.insert(ignore_permissions=True)
		self.profiles.append(profile)
		return profile

	def _get_or_create_shared_template(self):
		template_name = f"{TEST_PREFIX}-TEMPLATE"
		if not frappe.db.exists("Email Template", template_name):
			template = frappe.new_doc("Email Template")
			template.name = template_name
			template.subject = "Hello {{ doc.entity_name }}"
			template.response = "Hi {{ contact.first_name or 'there' }}."
			template.insert(ignore_permissions=True)
		return template_name

	def _cleanup_stale(self):
		# Deliberately does NOT sweep every Outreach Generation Run site-wide
		# — this exact unscoped pattern already deleted real run-tracking
		# docs once (the ones behind your real 994 generated drafts).
		# tearDown() already deletes exactly the runs this test creates; a
		# leftover from a crashed prior run is harmless orphaned clutter.
		for index in range(2):
			uen = f"{TEST_PREFIX}-UEN-{index}"
			profile_name = frappe.db.exists("Company Profile", uen)
			if not profile_name:
				continue
			for row in frappe.get_all(
				"Outreach Email", filters={"company_profile": profile_name}, fields=["name", "communication"]
			):
				if row.communication:
					frappe.delete_doc("Communication", row.communication, force=True, ignore_permissions=True)
				frappe.delete_doc("Outreach Email", row.name, force=True, ignore_permissions=True)
			contact_name = find_linked_contact(profile_name)
			if contact_name:
				frappe.delete_doc("Contact", contact_name, force=True, ignore_permissions=True)
			frappe.delete_doc("Company Profile", profile_name, force=True, ignore_permissions=True)
		frappe.delete_doc("Email Template", f"{TEST_PREFIX}-TEMPLATE", force=True, ignore_permissions=True)
