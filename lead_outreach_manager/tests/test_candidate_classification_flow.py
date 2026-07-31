import json
import re
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from lead_outreach_manager.services.candidate_classification import (
	ClassificationCLIError,
	enqueue_post_import_classification,
	run_background_classification,
)
from lead_outreach_manager.services import candidate_classification as classification_module
from lead_outreach_manager.services.candidate_names import confirm_candidate_name
from lead_outreach_manager.services.contacts import find_linked_contact, get_or_create_generic_contact

TEST_PREFIX = "000-TEST-LOM-CLASSIFY"
LEGACY_TEST_PREFIX = "TEST-LOM-CLASSIFY"

CLI_PATCH_TARGET = "lead_outreach_manager.services.candidate_classification.run_claude_cli"
CODEX_CLI_PATCH_TARGET = "lead_outreach_manager.services.candidate_classification.run_codex_cli"
DISCOVER_CODEX_PATCH_TARGET = "lead_outreach_manager.services.candidate_classification.discover_codex_cli"

# Matches the numbered candidate lines build_classification_prompt emits:
#   1. name: "Jane Tan" | title: ...
PROMPT_LINE_RE = re.compile(r'^(\d+)\. name: "(.*?)"', re.MULTILINE)


def make_fake_cli(verdict_by_name, seen_prompts=None):
	"""Returns a run_claude_cli stand-in that answers every candidate in the
	prompt: names in verdict_by_name get their (verdict, confidence), anything
	else (including stray rows on the test site) gets uncertain/0.5 — so tests
	don't depend on chunk composition."""

	def fake_cli(prompt, **kwargs):
		if seen_prompts is not None:
			seen_prompts.append(prompt)
		verdicts = []
		for index, name in PROMPT_LINE_RE.findall(prompt):
			verdict, confidence = verdict_by_name.get(name, ("uncertain", 0.5))
			verdicts.append({"index": int(index), "verdict": verdict, "confidence": confidence})
		return json.dumps({"type": "result", "is_error": False, "result": json.dumps(verdicts)})

	return fake_cli


def make_fake_codex_cli(verdict_by_name):
	"""Like make_fake_cli, but returns raw text with no claude-style JSON
	envelope — matching what run_codex_cli returns (the --output-last-message
	file's contents directly)."""

	def fake_cli(prompt, **kwargs):
		verdicts = []
		for index, name in PROMPT_LINE_RE.findall(prompt):
			verdict, confidence = verdict_by_name.get(name, ("uncertain", 0.5))
			verdicts.append({"index": int(index), "verdict": verdict, "confidence": confidence})
		return json.dumps(verdicts)

	return fake_cli


class TestCandidateClassificationFlow(FrappeTestCase):
	def setUp(self):
		self.profiles = []
		self.runs = []
		self._cleanup_stale()  # defensive: survive a prior run's incomplete tearDown

	def tearDown(self):
		for run in self.runs:
			frappe.delete_doc("Candidate Classification Run", run, force=True, ignore_permissions=True)
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

	def test_recurring_name_is_blacklisted_and_never_sent_to_the_llm(self):
		boilerplate = "Premium Growth Partners"
		for index in range(3):
			self._create_profile(index, candidate_names=[boilerplate])

		seen_prompts = []
		self._run_classification(make_fake_cli({}, seen_prompts))

		for profile in self.profiles:
			row = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
			self.assertEqual(row.classification_status, "Auto Rejected")
			self.assertEqual(row.classification_method, "Blacklist")
			self.assertFalse(row.confirmed)
		for prompt in seen_prompts:
			self.assertNotIn(boilerplate, prompt)

	def test_scraped_email_match_confirms_and_redirects_to_the_real_address(self):
		profile = self._create_profile(
			0,
			candidate_names=["Jane Tan"],
			extra_contact_points=[
				{"contact_type": "Email", "value": f"jane.tan@{self._domain(0)}", "is_generic": 0}
			],
		)

		self._run_classification(make_fake_cli({}))

		row = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertTrue(row.confirmed)
		self.assertEqual(row.confirmation_source, "Auto - Email Match")
		self.assertEqual(row.classification_status, "Auto Confirmed")
		self.assertEqual(row.classification_method, "Email Match")
		self.assertEqual(row.matched_contact_email, f"jane.tan@{self._domain(0)}")

		contact = frappe.get_doc("Contact", row.contact)
		self.assertEqual(contact.first_name, "Jane")
		# Redirect target is the REAL scraped address, not a guess.
		self.assertEqual(contact.email_id, f"jane.tan@{self._domain(0)}")

	def test_llm_confirm_personalizes_but_never_redirects_the_recipient(self):
		profile = self._create_profile(0, candidate_names=["Alice Wong"])
		contact_before = get_or_create_generic_contact(profile)
		generic_email = contact_before.email_id

		self._run_classification(make_fake_cli({"Alice Wong": ("person", 0.95)}))

		row = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertTrue(row.confirmed)
		self.assertEqual(row.confirmation_source, "Auto - LLM")
		self.assertEqual(row.classification_status, "Auto Confirmed")
		self.assertEqual(row.classification_method, "LLM")
		self.assertEqual(row.llm_verdict, "person")
		self.assertAlmostEqual(row.llm_confidence, 0.95, places=2)
		# Guesses are stored on the row for the reviewer's benefit...
		self.assertEqual(row.guessed_email_1, f"alice.wong@{self._domain(0)}")

		# ...but the recipient stays the generic scraped address — the guessed
		# redirect is exclusive to the human Confirm Name click.
		contact = frappe.get_doc("Contact", row.contact)
		self.assertEqual(contact.first_name, "Alice")
		self.assertEqual(contact.email_id, generic_email)

	def test_reject_and_uncertain_bands(self):
		profile = self._create_profile(
			0, candidate_names=["Best Deals Warehouse", "Golden Dragon"]
		)

		self._run_classification(make_fake_cli({"Best Deals Warehouse": ("not_person", 0.9)}))

		rows = frappe.get_doc("Company Profile", profile.name).candidate_names
		self.assertEqual(rows[0].classification_status, "Auto Rejected")
		self.assertEqual(rows[0].classification_method, "LLM")
		self.assertFalse(rows[0].confirmed)
		# "Golden Dragon" got the default uncertain/0.5 verdict.
		self.assertEqual(rows[1].classification_status, "Needs Review")
		self.assertFalse(rows[1].confirmed)

	def test_second_run_is_a_no_op_for_classified_rows(self):
		profile = self._create_profile(0, candidate_names=["Alice Wong"])
		self._run_classification(make_fake_cli({"Alice Wong": ("person", 0.95)}))
		row_before = frappe.get_doc("Company Profile", profile.name).candidate_names[0].as_dict()

		seen_prompts = []
		self._run_classification(make_fake_cli({}, seen_prompts))

		row_after = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertEqual(row_after.classification_status, row_before["classification_status"])
		self.assertEqual(row_after.confirmed_on, row_before["confirmed_on"])
		for prompt in seen_prompts:
			self.assertNotIn("Alice Wong", prompt)

	def test_cli_failure_marks_rows_error_and_a_later_run_retries_them(self):
		profile = self._create_profile(0, candidate_names=["Bob Lee"])

		def broken_cli(prompt, **kwargs):
			raise ClassificationCLIError("usage limit reached")

		run_name = self._run_classification(broken_cli)

		row = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertEqual(row.classification_status, "Error")
		self.assertFalse(row.confirmed)
		run = frappe.get_doc("Candidate Classification Run", run_name)
		self.assertEqual(run.status, "Completed With Errors")
		self.assertGreaterEqual(run.chunks_failed, 1)

		# Error rows are re-selected by the next run and can now succeed.
		self._run_classification(make_fake_cli({"Bob Lee": ("person", 0.95)}))
		row = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertEqual(row.classification_status, "Auto Confirmed")
		self.assertTrue(row.confirmed)

	def test_codex_fallback_serves_the_chunk_when_claude_fails(self):
		profile = self._create_profile(0, candidate_names=["Carol Ng"])

		def broken_claude(prompt, **kwargs):
			raise ClassificationCLIError("usage limit reached")

		run_name = self._run_classification(
			broken_claude,
			fake_codex_cli=make_fake_codex_cli({"Carol Ng": ("person", 0.9)}),
			discover_codex=lambda: "/fake/codex",
		)

		row = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertTrue(row.confirmed)
		self.assertEqual(row.classification_status, "Auto Confirmed")
		self.assertEqual(row.llm_provider, "codex")
		self.assertEqual(row.llm_verdict, "person")
		run = frappe.get_doc("Candidate Classification Run", run_name)
		self.assertEqual(run.status, "Completed")
		self.assertEqual(run.chunks_via_fallback, 1)
		self.assertEqual(run.chunks_failed, 0)

	def test_no_fallback_configured_behaves_exactly_as_before(self):
		# discover_codex -> None (the _run_classification default) means
		# settings.fallback_cli_path resolves to nothing, so a claude failure
		# still lands the row in Error without ever calling run_codex_cli —
		# same behavior as before the fallback existed.
		profile = self._create_profile(0, candidate_names=["Dave Koh"])

		def broken_claude(prompt, **kwargs):
			raise ClassificationCLIError("usage limit reached")

		run_name = self._run_classification(broken_claude)

		row = frappe.get_doc("Company Profile", profile.name).candidate_names[0]
		self.assertEqual(row.classification_status, "Error")
		run = frappe.get_doc("Candidate Classification Run", run_name)
		self.assertEqual(run.chunks_via_fallback, 0)

	def test_profile_with_a_human_confirm_downgrades_llm_confirms_to_review(self):
		profile = self._create_profile(0, candidate_names=["Jane Tan", "Alice Wong"])
		confirm_candidate_name(profile.name, profile.candidate_names[0].name)

		self._run_classification(make_fake_cli({"Alice Wong": ("person", 0.95)}))

		rows = frappe.get_doc("Company Profile", profile.name).candidate_names
		self.assertEqual(rows[0].confirmation_source, "Human")
		# Human decisions win: no auto-confirm may re-personalize this Contact.
		self.assertFalse(rows[1].confirmed)
		self.assertEqual(rows[1].classification_status, "Needs Review")
		contact = frappe.get_doc("Contact", rows[0].contact)
		self.assertEqual(contact.first_name, "Jane")

	def test_post_import_enqueue_is_a_no_op_unless_opted_in(self):
		# auto_classify_after_import is unset/0 on this site's Outreach Settings.
		self.assertIsNone(enqueue_post_import_classification())

	# ------------------------------------------------------------------
	# Helpers
	# ------------------------------------------------------------------

	def _run_classification(self, fake_cli, *, fake_codex_cli=None, discover_codex=lambda: None):
		"""This site's DB is shared with real imported data (tens of thousands
		of Company Candidate Name rows) — run_background_classification's
		selection query has no per-test scoping by design (a real backfill must
		see the whole site), so without a test-side filter here every test run
		would also classify unrelated real rows using this test's fake CLI.
		Scope target selection down to just this test's own profiles.

		Also neutralizes real Codex discovery by default (discover_codex ->
		None): this dev machine genuinely has a Codex.app install, so without
		this a claude-failure test would silently shell out to the real codex
		binary as its fallback instead of staying deterministic. Pass a fake
		discover_codex + fake_codex_cli explicitly to exercise the fallback
		path itself."""
		own_profile_names = {p.name for p in self.profiles}
		real_get_unclassified_rows = classification_module._get_unclassified_rows

		def scoped_get_unclassified_rows(limit_rows=None):
			return [
				row for row in real_get_unclassified_rows(limit_rows=None) if row["parent"] in own_profile_names
			]

		run = frappe.new_doc("Candidate Classification Run")
		run.run_trigger = "Manual"
		run.insert(ignore_permissions=True)
		self.runs.append(run.name)
		with mock.patch(CLI_PATCH_TARGET, side_effect=fake_cli), mock.patch.object(
			classification_module, "_get_unclassified_rows", side_effect=scoped_get_unclassified_rows
		), mock.patch(DISCOVER_CODEX_PATCH_TARGET, side_effect=discover_codex), mock.patch(
			CODEX_CLI_PATCH_TARGET, side_effect=fake_codex_cli
		):
			run_background_classification(run.name)
		return run.name

	def _domain(self, index):
		return f"testclassify{index}.example"

	def _create_profile(self, index, candidate_names=(), extra_contact_points=()):
		profile = frappe.new_doc("Company Profile")
		profile.uen = f"{TEST_PREFIX}-UEN-{index}"
		profile.entity_name = f"Test Classify Co {index} Pte Ltd"
		profile.domain = self._domain(index)
		profile.domain_status = "verified"
		profile.contact_status = "has_generic_contact"
		profile.has_email_contact = 1
		profile.primary_email = f"info@{self._domain(index)}"
		profile.do_not_contact = 0
		profile.append(
			"contact_points",
			{"contact_type": "Email", "value": f"info@{self._domain(index)}", "is_generic": 1},
		)
		for contact_point in extra_contact_points:
			profile.append("contact_points", contact_point)
		for position, name_text in enumerate(candidate_names, start=1):
			profile.append(
				"candidate_names",
				{
					"name_text": name_text,
					"title_text": "Director",
					"source_url": f"https://{self._domain(index)}/about",
					"extraction_method": "regex_proximity_v1",
					"source_document_id": position,
				},
			)
		profile.insert(ignore_permissions=True)
		self.profiles.append(profile)
		return profile

	def _cleanup_stale(self):
		# Deliberately does NOT sweep stray "Candidate Classification Run"
		# docs by run_trigger — this site's DB is shared with real data, and
		# an unscoped filter here previously deleted a real, in-progress
		# backfill's tracking record mid-run. tearDown() already deletes
		# exactly the runs each test creates (via self.runs); any leftover
		# doc from a crashed prior test run is harmless orphaned clutter, not
		# something worth risking real data to clean up automatically.
		for prefix in (TEST_PREFIX, LEGACY_TEST_PREFIX):
			for index in range(3):
				uen = f"{prefix}-UEN-{index}"
				profile_name = frappe.db.exists("Company Profile", uen)
				if not profile_name:
					continue
				contact_name = find_linked_contact(profile_name)
				if contact_name:
					frappe.delete_doc("Contact", contact_name, force=True, ignore_permissions=True)
				frappe.delete_doc("Company Profile", profile_name, force=True, ignore_permissions=True)
