import sqlite3
import tempfile
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

from lead_outreach_manager.imports.vietnam_lead_imports import import_vietnam_leads, split_emails


class TestSplitEmails(FrappeTestCase):
	"""Pure-function tests — no fixture DB needed."""

	def test_splits_dedupes_and_validates(self):
		value = "a@x.com; b@x.com ; a@x.com;not-an-email;"
		self.assertEqual(split_emails(value), ["a@x.com", "b@x.com"])

	def test_single_email_no_semicolon(self):
		self.assertEqual(split_emails("solo@x.com"), ["solo@x.com"])

	def test_blank_or_none_returns_empty(self):
		self.assertEqual(split_emails(""), [])
		self.assertEqual(split_emails(None), [])


class TestVietnamLeadImport(FrappeTestCase):
	def setUp(self):
		# Matches get_or_create_vietnam_profile's f"VN-{row['id']}" for the
		# fixture row's hardcoded id=1 below.
		self.uen = "VN-1"
		self._cleanup(self.uen)  # defensive: survive a prior run's incomplete tearDown
		self.sqlite_path = self._build_fixture_db()

	def tearDown(self):
		Path(self.sqlite_path).unlink(missing_ok=True)
		self._cleanup(self.uen)
		frappe.db.commit()  # FrappeTestCase doesn't auto-commit/rollback per test

	def test_import_creates_profile_contact_and_unconfirmed_candidate(self):
		stats = import_vietnam_leads(sqlite_path=self.sqlite_path)
		self.assertEqual(stats["profiles_created"], 1)
		self.assertEqual(stats["contacts_created"], 1)
		self.assertEqual(stats["candidate_names_added"], 1)

		profile = frappe.get_doc("Company Profile", self.uen)
		self.assertEqual(profile.entity_name, "Test Vietnam Co")
		self.assertEqual(profile.country, "Vietnam")
		# First of the ';'-separated emails wins as primary, per source order.
		self.assertEqual(profile.primary_email, "jane@testvn.example")
		self.assertTrue(profile.has_email_contact)
		self.assertEqual(len(profile.contact_points), 3)  # 2 distinct emails (deduped from 3) + 1 phone
		self.assertEqual(len(profile.candidate_names), 1)
		self.assertEqual(profile.candidate_names[0].name_text, "Jane Nguyen")
		self.assertFalse(profile.candidate_names[0].confirmed)

		from lead_outreach_manager.services.contacts import find_linked_contact

		self.assertIsNotNone(find_linked_contact(profile.name))

	def test_reimport_is_idempotent(self):
		import_vietnam_leads(sqlite_path=self.sqlite_path)
		stats_second = import_vietnam_leads(sqlite_path=self.sqlite_path)

		self.assertEqual(stats_second["profiles_created"], 0)
		self.assertEqual(stats_second["profiles_updated"], 1)
		self.assertEqual(stats_second["candidate_names_added"], 0)

		profile = frappe.get_doc("Company Profile", self.uen)
		self.assertEqual(len(profile.candidate_names), 1)

	def test_confirmed_candidate_name_survives_reimport(self):
		import_vietnam_leads(sqlite_path=self.sqlite_path)
		profile = frappe.get_doc("Company Profile", self.uen)
		row_name = profile.candidate_names[0].name

		from lead_outreach_manager.services.candidate_names import confirm_candidate_name

		confirm_candidate_name(profile.name, row_name)

		import_vietnam_leads(sqlite_path=self.sqlite_path)

		profile_after = frappe.get_doc("Company Profile", self.uen)
		self.assertEqual(len(profile_after.candidate_names), 1)
		self.assertTrue(profile_after.candidate_names[0].confirmed)

	def test_rows_without_domain_or_name_are_skipped(self):
		conn = sqlite3.connect(self.sqlite_path)
		conn.execute(
			"""INSERT INTO leads (
				id, region, source, source_id, company_name, website, email, phone,
				representative_name, position, source_url, inserted_at, updated_at, domain
			) VALUES (2, 'vietnam', 'test', '2', 'No Domain Co', '', '', '', 'Someone', '', '', ?, ?, '')""",
			(self._now(), self._now()),
		)
		conn.commit()
		conn.close()

		stats = import_vietnam_leads(sqlite_path=self.sqlite_path)
		self.assertEqual(stats["rows_seen"], 1)  # only the original fixture row qualifies

	def _now(self):
		return frappe.utils.now_datetime().isoformat()

	def _cleanup(self, uen):
		from lead_outreach_manager.services.contacts import find_linked_contact

		profile_name = frappe.db.exists("Company Profile", uen)
		if profile_name:
			contact_name = find_linked_contact(profile_name)
			if contact_name:
				frappe.delete_doc("Contact", contact_name, force=True, ignore_permissions=True)
			frappe.delete_doc("Company Profile", profile_name, force=True, ignore_permissions=True)

	def _build_fixture_db(self):
		fixture_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
		fixture_file.close()
		conn = sqlite3.connect(fixture_file.name)
		conn.executescript(
			"""
			CREATE TABLE leads (
				id INTEGER PRIMARY KEY, region TEXT, source TEXT, source_id TEXT,
				company_name TEXT, website TEXT, email TEXT, phone TEXT,
				representative_name TEXT, position TEXT, description TEXT, industry TEXT,
				source_url TEXT, enrichment_status TEXT, missing_fields_json TEXT,
				confidence REAL, raw_json TEXT, inserted_at TEXT, updated_at TEXT,
				normalized_company_name TEXT, domain TEXT, address TEXT, hq_country TEXT,
				raw_source_path TEXT, last_enriched_at TEXT
			);
			"""
		)
		now = frappe.utils.now_datetime().isoformat()
		conn.execute(
			"""INSERT INTO leads (
				id, region, source, source_id, company_name, website, email, phone,
				representative_name, position, confidence, inserted_at, updated_at,
				domain, hq_country
			) VALUES (1, 'vietnam', 'test', '1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
			(
				"Test Vietnam Co",
				"https://testvn.example",
				"jane@testvn.example; jane.nguyen@testvn.example; jane@testvn.example",
				"090 000 0000",
				"Jane Nguyen",
				"Founder",
				0.7,
				now,
				now,
				"testvn.example",
				"Vietnam",
			),
		)
		conn.commit()
		conn.close()
		return fixture_file.name
