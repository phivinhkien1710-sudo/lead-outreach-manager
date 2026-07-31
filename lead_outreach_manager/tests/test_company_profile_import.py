import sqlite3
import tempfile
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

from lead_outreach_manager.imports.company_profile_imports import import_usable_leads

TEST_PREFIX = "TEST-LOM-IMPORT"


class TestCompanyProfileImport(FrappeTestCase):
	def setUp(self):
		self.uen = f"{TEST_PREFIX}-UEN-1"
		self._cleanup(self.uen)  # defensive: survive a prior run's incomplete tearDown
		self.sqlite_path = self._build_fixture_db()

	def tearDown(self):
		Path(self.sqlite_path).unlink(missing_ok=True)
		self._cleanup(self.uen)
		frappe.db.commit()  # FrappeTestCase doesn't auto-commit/rollback per test

	def test_import_is_idempotent_and_creates_generic_contact(self):
		stats_first = import_usable_leads(sqlite_path=self.sqlite_path)
		self.assertEqual(stats_first["profiles_created"], 1)
		self.assertEqual(stats_first["contacts_created"], 1)
		self.assertEqual(stats_first["candidate_names_added"], 1)

		profile = frappe.get_doc("Company Profile", self.uen)
		self.assertEqual(profile.entity_name, "Test Import Co Pte Ltd")
		self.assertTrue(profile.has_email_contact)
		self.assertEqual(profile.primary_email, "info@testimport.example")
		self.assertEqual(len(profile.candidate_names), 1)
		self.assertFalse(profile.candidate_names[0].confirmed)

		from lead_outreach_manager.services.contacts import find_linked_contact

		contact_name = find_linked_contact(profile.name)
		self.assertIsNotNone(contact_name)

		# Re-running must not duplicate anything.
		stats_second = import_usable_leads(sqlite_path=self.sqlite_path)
		self.assertEqual(stats_second["profiles_created"], 0)
		self.assertEqual(stats_second["profiles_updated"], 1)
		self.assertEqual(stats_second["candidate_names_added"], 0)

		profile_after = frappe.get_doc("Company Profile", self.uen)
		self.assertEqual(len(profile_after.candidate_names), 1)

	def test_confirmed_candidate_name_survives_reimport(self):
		import_usable_leads(sqlite_path=self.sqlite_path)

		profile = frappe.get_doc("Company Profile", self.uen)
		row_name = profile.candidate_names[0].name

		from lead_outreach_manager.services.candidate_names import confirm_candidate_name

		confirm_candidate_name(profile.name, row_name)

		import_usable_leads(sqlite_path=self.sqlite_path)

		profile_after = frappe.get_doc("Company Profile", self.uen)
		self.assertEqual(len(profile_after.candidate_names), 1)
		self.assertTrue(profile_after.candidate_names[0].confirmed)
		self.assertEqual(profile_after.candidate_names[0].name_text, "Jane Tan")

	def _build_fixture_db(self):
		fixture_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
		fixture_file.close()
		conn = sqlite3.connect(fixture_file.name)
		conn.executescript(
			"""
			CREATE TABLE companies (
				id INTEGER PRIMARY KEY, uen TEXT, entity_name TEXT,
				entity_status_description TEXT, no_of_officers INTEGER,
				primary_ssic_code TEXT, primary_ssic_description TEXT,
				industry_tier TEXT, priority_tier TEXT,
				street_name TEXT, building_name TEXT, postal_code TEXT, postal_sector TEXT,
				geo_match_method TEXT, geo_match_zone TEXT, geo_confidence REAL,
				qualified INTEGER, website TEXT, domain TEXT, domain_match_score REAL,
				domain_status TEXT, crawl_status TEXT, contact_status TEXT, pipeline_stage TEXT,
				inserted_at TEXT, updated_at TEXT
			);
			CREATE TABLE contact_points (
				id INTEGER PRIMARY KEY, company_id INTEGER, contact_type TEXT, value TEXT,
				is_generic INTEGER, confidence REAL, source_url TEXT, discovered_at TEXT
			);
			CREATE TABLE candidate_names (
				id INTEGER PRIMARY KEY, company_id INTEGER, name_text TEXT, title_text TEXT,
				source_url TEXT, extraction_method TEXT, source_document_id INTEGER, discovered_at TEXT
			);
			"""
		)
		conn.execute(
			"""INSERT INTO companies (
				id, uen, entity_name, entity_status_description, no_of_officers,
				primary_ssic_code, primary_ssic_description, industry_tier, priority_tier,
				street_name, building_name, postal_code, postal_sector,
				geo_match_method, geo_match_zone, geo_confidence,
				qualified, website, domain, domain_match_score,
				domain_status, crawl_status, contact_status, pipeline_stage,
				inserted_at, updated_at
			) VALUES (1, ?, 'Test Import Co Pte Ltd', 'Live', 2,
				'25', 'manufacturing desc', 'manufacturing', 'A_verified_contact',
				'Test St', 'Test Bldg', '123456', '12',
				'street_keyword', 'jurong', 1.0,
				1, 'https://testimport.example', 'testimport.example', 0.9,
				'verified', 'crawled', 'has_generic_contact', 'crawled',
				'2026-07-01T00:00:00', '2026-07-01T00:00:00')""",
			(self.uen,),
		)
		conn.execute(
			"""INSERT INTO contact_points (id, company_id, contact_type, value, is_generic, confidence, source_url, discovered_at)
			   VALUES (1, 1, 'email', 'info@testimport.example', 1, 0.9, 'https://testimport.example/contact', '2026-07-01T00:00:00')"""
		)
		conn.execute(
			"""INSERT INTO candidate_names (id, company_id, name_text, title_text, source_url, extraction_method, source_document_id, discovered_at)
			   VALUES (1, 1, 'Jane Tan', 'Director', 'https://testimport.example/about', 'regex_proximity_v1', 1, '2026-07-01T00:00:00')"""
		)
		conn.commit()
		conn.close()
		return fixture_file.name

	def _cleanup(self, uen):
		from lead_outreach_manager.services.contacts import find_linked_contact

		profile_name = frappe.db.exists("Company Profile", uen)
		if profile_name:
			contact_name = find_linked_contact(profile_name)
			if contact_name:
				frappe.delete_doc("Contact", contact_name, force=True, ignore_permissions=True)
			frappe.delete_doc("Company Profile", profile_name, force=True, ignore_permissions=True)
