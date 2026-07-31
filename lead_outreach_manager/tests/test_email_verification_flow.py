from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from lead_outreach_manager.services.candidate_names import apply_confirmation
from lead_outreach_manager.services.contacts import find_linked_contact
from lead_outreach_manager.services.email_verification import (
	VerificationAPIError,
	manually_promote_verified_email,
	run_background_verification,
)
from lead_outreach_manager.services import email_verification as verification_module

TEST_PREFIX = "TEST-LOM-VERIFY"

CALL_API_PATCH_TARGET = "lead_outreach_manager.services.email_verification.call_verification_api"


def make_fake_api(result_by_email, calls=None):
	"""Returns a call_verification_api stand-in keyed by exact email address.
	An unlisted email defaults to "unknown" (a real MillionVerifier value);
	"__error__" simulates a provider/network failure instead of a result."""

	def fake_api(email, *, api_key, timeout_seconds):
		if calls is not None:
			calls.append(email)
		result = result_by_email.get(email, "unknown")
		if result == "__error__":
			raise VerificationAPIError("simulated verification API failure")
		return {"email": email, "result": result}

	return fake_api


class TestEmailVerificationFlow(FrappeTestCase):
	def setUp(self):
		self.profiles = []
		self.runs = []
		self._cleanup_stale()  # defensive: survive a prior run's incomplete tearDown

	def tearDown(self):
		for run in self.runs:
			frappe.delete_doc("Email Verification Run", run, force=True, ignore_permissions=True)
		for profile in self.profiles:
			contact_name = find_linked_contact(profile.name)
			if contact_name:
				frappe.delete_doc("Contact", contact_name, force=True, ignore_permissions=True)
			frappe.delete_doc("Company Profile", profile.name, force=True, ignore_permissions=True)
		# FrappeTestCase does not auto-commit or auto-rollback per test — cleanup
		# only sticks if explicitly committed here.
		frappe.db.commit()

	# ------------------------------------------------------------------
	# Scenarios
	# ------------------------------------------------------------------

	def test_ok_on_first_guess_promotes_contact_and_marks_verified(self):
		profile = self._create_confirmed_candidate(0, "Jane Tan")
		row = profile.candidate_names[0]
		guess1 = row.guessed_email_1

		self._run_verification(make_fake_api({guess1: "ok"}))

		row_after = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertEqual(row_after.verification_status, "Verified")
		self.assertEqual(row_after.verified_email, guess1)
		self.assertEqual(row_after.verification_result, "ok")
		contact = frappe.get_doc("Contact", row_after.contact)
		self.assertEqual(contact.email_id, guess1)

	def test_ok_on_second_guess_when_first_is_invalid(self):
		profile = self._create_confirmed_candidate(0, "Jane Tan")
		row = profile.candidate_names[0]
		guess1, guess2 = row.guessed_email_1, row.guessed_email_2

		calls = []
		self._run_verification(make_fake_api({guess1: "invalid", guess2: "ok"}, calls))

		row_after = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertEqual(row_after.verification_status, "Verified")
		self.assertEqual(row_after.verified_email, guess2)
		# Stopped at the first "ok" — guess3 was never tried.
		self.assertEqual(calls, [guess1, guess2])
		contact = frappe.get_doc("Contact", row_after.contact)
		self.assertEqual(contact.email_id, guess2)

	def test_all_guesses_invalid_marks_not_deliverable_without_promotion(self):
		profile = self._create_confirmed_candidate(0, "Jane Tan")
		row = profile.candidate_names[0]
		guesses = [
			row.guessed_email_1,
			row.guessed_email_2,
			row.guessed_email_3,
			row.guessed_email_4,
			row.guessed_email_5,
			row.guessed_email_6,
		]
		contact_before = frappe.get_doc("Contact", row.contact).email_id

		self._run_verification(make_fake_api({g: "invalid" for g in guesses}))

		row_after = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertEqual(row_after.verification_status, "Not Deliverable")
		contact = frappe.get_doc("Contact", row_after.contact)
		self.assertEqual(contact.email_id, contact_before)

	def test_catch_all_result_does_not_promote(self):
		profile = self._create_confirmed_candidate(0, "Jane Tan")
		row = profile.candidate_names[0]
		guesses = [
			row.guessed_email_1,
			row.guessed_email_2,
			row.guessed_email_3,
			row.guessed_email_4,
			row.guessed_email_5,
			row.guessed_email_6,
		]
		contact_before = frappe.get_doc("Contact", row.contact).email_id

		self._run_verification(make_fake_api({g: "catch_all" for g in guesses}))

		row_after = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertEqual(row_after.verification_status, "Catch-All")
		contact = frappe.get_doc("Contact", row_after.contact)
		self.assertEqual(contact.email_id, contact_before)

	def test_api_error_marks_row_error_and_is_retried_next_run(self):
		profile = self._create_confirmed_candidate(0, "Jane Tan")
		row = profile.candidate_names[0]
		guess1 = row.guessed_email_1

		run_name = self._run_verification(make_fake_api({guess1: "__error__"}))

		row_after = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertEqual(row_after.verification_status, "Error")
		run = frappe.get_doc("Email Verification Run", run_name)
		self.assertEqual(run.status, "Completed With Errors")
		self.assertGreaterEqual(run.error_rows, 1)

		# Error rows are re-selected by the next run and can now succeed.
		self._run_verification(make_fake_api({guess1: "ok"}))
		row_final = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertEqual(row_final.verification_status, "Verified")

	def test_second_run_does_not_reverify_already_verified_rows(self):
		profile = self._create_confirmed_candidate(0, "Jane Tan")
		row = profile.candidate_names[0]
		guess1 = row.guessed_email_1

		self._run_verification(make_fake_api({guess1: "ok"}))

		calls = []
		self._run_verification(make_fake_api({}, calls))
		self.assertEqual(calls, [])

	def test_manual_promote_on_catch_all_row(self):
		profile = self._create_confirmed_candidate(0, "Jane Tan")
		row = profile.candidate_names[0]
		guesses = [
			row.guessed_email_1,
			row.guessed_email_2,
			row.guessed_email_3,
			row.guessed_email_4,
			row.guessed_email_5,
			row.guessed_email_6,
		]

		self._run_verification(make_fake_api({g: "catch_all" for g in guesses}))

		row_after = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertEqual(row_after.verification_status, "Catch-All")

		result = manually_promote_verified_email(profile.name, row_after.name)
		self.assertTrue(result["promoted"])

		row_final = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertEqual(row_final.verification_status, "Verified")
		contact = frappe.get_doc("Contact", row_final.contact)
		self.assertEqual(contact.email_id, row_final.verified_email)

	def test_manual_promote_rejects_non_catch_all_row(self):
		profile = self._create_confirmed_candidate(0, "Jane Tan")
		row = profile.candidate_names[0]
		guesses = [
			row.guessed_email_1,
			row.guessed_email_2,
			row.guessed_email_3,
			row.guessed_email_4,
			row.guessed_email_5,
			row.guessed_email_6,
		]

		self._run_verification(make_fake_api({g: "invalid" for g in guesses}))
		row_after = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertEqual(row_after.verification_status, "Not Deliverable")

		with self.assertRaises(frappe.ValidationError):
			manually_promote_verified_email(profile.name, row_after.name)

	# ------------------------------------------------------------------
	# Helpers
	# ------------------------------------------------------------------

	def _run_verification(self, fake_api, limit_rows=None):
		"""This site's DB is shared with real data (hundreds of confirmed
		candidates with guesses) — _get_unverified_rows has no per-test scoping
		by design (a real backfill must see the whole site), so without a
		test-side filter here every test run would also verify unrelated real
		rows using this test's fake API. Scope target selection down to just
		this test's own profiles."""
		own_profile_names = {p.name for p in self.profiles}
		real_get_unverified_rows = verification_module._get_unverified_rows

		def scoped_get_unverified_rows(limit_rows=None):
			rows = [row for row in real_get_unverified_rows(limit_rows=None) if row["parent"] in own_profile_names]
			return rows[:limit_rows] if limit_rows else rows

		run = frappe.new_doc("Email Verification Run")
		run.run_trigger = "Manual"
		run.limit_rows = limit_rows
		run.insert(ignore_permissions=True)
		self.runs.append(run.name)
		with mock.patch(CALL_API_PATCH_TARGET, side_effect=fake_api), mock.patch.object(
			verification_module, "_get_unverified_rows", side_effect=scoped_get_unverified_rows
		):
			run_background_verification(run.name)
		return run.name

	def _domain(self, index):
		return f"testverify{index}.example"

	def _create_confirmed_candidate(self, index, name_text):
		profile = frappe.new_doc("Company Profile")
		profile.uen = f"{TEST_PREFIX}-UEN-{index}"
		profile.entity_name = f"Test Verify Co {index} Pte Ltd"
		profile.domain = self._domain(index)
		profile.domain_status = "verified"
		profile.contact_status = "has_generic_contact"
		profile.has_email_contact = 1
		profile.primary_email = f"info@{self._domain(index)}"
		profile.do_not_contact = 0
		profile.append(
			"candidate_names",
			{"name_text": name_text, "title_text": "Director", "source_document_id": 1},
		)
		profile.insert(ignore_permissions=True)
		self.profiles.append(profile)

		row = profile.candidate_names[0]
		apply_confirmation(profile, row, source="Auto - LLM")
		row.classification_status = "Auto Confirmed"
		row.classification_method = "LLM"
		profile.save(ignore_permissions=True)
		return frappe.get_doc("Company Profile", profile.name)

	def _cleanup_stale(self):
		# Deliberately does NOT sweep every Email Verification Run or
		# Company Candidate Name site-wide — this site's DB is shared with
		# real data, and an unscoped cleanup sweep has already deleted real
		# tracking docs once this session (see test_outreach_generation.py /
		# test_outreach_batch_scheduling.py history). tearDown() already
		# deletes exactly what each test creates; a leftover from a crashed
		# prior run is harmless orphaned clutter, not worth risking real data.
		for index in range(3):
			uen = f"{TEST_PREFIX}-UEN-{index}"
			profile_name = frappe.db.exists("Company Profile", uen)
			if not profile_name:
				continue
			contact_name = find_linked_contact(profile_name)
			if contact_name:
				frappe.delete_doc("Contact", contact_name, force=True, ignore_permissions=True)
			frappe.delete_doc("Company Profile", profile_name, force=True, ignore_permissions=True)
