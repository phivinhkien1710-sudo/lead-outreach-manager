import frappe
from unittest import mock
from frappe.tests.utils import FrappeTestCase
from frappe.utils.file_manager import save_file

from lead_outreach_manager.services.contacts import find_linked_contact
from lead_outreach_manager.services.csv_imports import run_import, validate_import_run
from lead_outreach_manager.services.discovery import DiscoveryResult


class TestLeadCSVImports(FrappeTestCase):
	PROFILE_KEY = "TEST-LOM-CSV-001"
	DISCOVERY_PROFILE_KEY = "TEST-LOM-CSV-DISCOVERY"

	def setUp(self):
		self.run_names = []
		self.file_names = []
		self._cleanup_profile()

	def tearDown(self):
		self._cleanup_profile()
		for name in self.file_names:
			if frappe.db.exists("File", name):
				frappe.delete_doc("File", name, force=True, ignore_permissions=True)
		for name in self.run_names:
			if frappe.db.exists("Lead CSV Import Run", name):
				frappe.delete_doc("Lead CSV Import Run", name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_validate_import_and_idempotent_reimport(self):
		run = self._make_run(
			"country,uen,company_name,domain,candidate_name,position,email,industry_tier\n"
			"Singapore,TEST-LOM-CSV-001,CSV Test Company,csv-test.example,Jane Tan,Director,"
			"jane@csv-test.example,Industrial\n"
		)

		validation = validate_import_run(run.name)
		self.assertEqual(validation["valid_rows"], 1)
		self.assertEqual(validation["invalid_rows"], 0)

		result = run_import(run.name)
		self.assertEqual(result["profiles_created"], 1)
		self.assertEqual(result["candidate_names_added"], 1)
		profile = frappe.get_doc("Company Profile", self.PROFILE_KEY)
		self.assertEqual(profile.country, "Singapore")
		self.assertEqual(profile.domain, "csv-test.example")
		self.assertEqual(profile.industry_tier, "Industrial")
		self.assertEqual(profile.primary_email, "jane@csv-test.example")
		self.assertEqual(len(profile.candidate_names), 1)
		self.assertEqual(
			frappe.db.count(
				"Lead Import Batch Member",
				{"import_run": run.name, "company_profile": profile.name},
			),
			1,
		)
		self.assertEqual(frappe.db.get_value("Lead CSV Import Run", run.name, "batch_companies"), 1)

		profile.candidate_names[0].confirmed = 1
		profile.save(ignore_permissions=True)
		second = run_import(run.name)
		self.assertEqual(second["profiles_updated"], 1)
		self.assertEqual(second["candidate_names_added"], 0)
		profile.reload()
		self.assertEqual(len(profile.candidate_names), 1)
		self.assertEqual(profile.candidate_names[0].confirmed, 1)
		self.assertEqual(
			frappe.db.count(
				"Lead Import Batch Member",
				{"import_run": run.name, "company_profile": profile.name},
			),
			1,
		)

	def test_alias_columns_and_invalid_rows(self):
		run = self._make_run(
			"entity_name,domain,representative_name,email\n"
			"Valid Vietnam Co,https://valid-vn.example/about,Nguyen Van An,an@valid-vn.example\n"
			"Missing Candidate,invalid.example,,info@invalid.example\n",
			country="Vietnam",
		)
		validation = validate_import_run(run.name)
		self.assertEqual(validation["total_rows"], 2)
		self.assertEqual(validation["valid_rows"], 1)
		self.assertEqual(validation["invalid_rows"], 1)

	def test_friendly_headers_validate_for_discovery_import(self):
		run = self._make_run(
			"Company name,Postal Code\nDemo Company PTE LTD,049315\nSecond Demo Company,018956\n",
			discovery=True,
		)
		run.limit_rows = 1
		run.save(ignore_permissions=True)
		validation = validate_import_run(run.name)
		self.assertEqual(validation["valid_rows"], 2)
		self.assertEqual(validation["invalid_rows"], 0)
		self.assertEqual(validation["selected_rows"], 1)

	def test_import_discovers_missing_domain_contacts_and_candidates(self):
		run = self._make_run(
			"uen,company_name\nTEST-LOM-CSV-DISCOVERY,Discovery Test Company\n",
			discovery=True,
		)
		discovered = DiscoveryResult(
			domain="discovered.example",
			website="https://discovered.example",
			domain_status="verified",
			crawl_status="crawled",
			domain_match_score=1,
			contact_points=[{"contact_type": "Email", "value": "info@discovered.example", "is_generic": 1, "confidence": 0.9, "source_url": "https://discovered.example/contact"}],
			candidates=[{"name_text": "Alice Wong", "title_text": "Director", "source_url": "https://discovered.example/team"}],
		)
		with mock.patch("lead_outreach_manager.services.discovery.discover_rows", return_value=([discovered], [])):
			result = run_import(run.name)
		self.assertEqual(result["domains_discovered"], 1)
		self.assertEqual(result["contacts_discovered"], 1)
		self.assertEqual(result["candidates_discovered"], 1)
		profile = frappe.get_doc("Company Profile", self.DISCOVERY_PROFILE_KEY)
		self.assertEqual(profile.domain, "discovered.example")
		self.assertEqual(profile.primary_email, "info@discovered.example")
		self.assertEqual(profile.candidate_names[0].name_text, "Alice Wong")

	def _make_run(self, content, country="Singapore", discovery=False):
		run = frappe.get_doc({
			"doctype": "Lead CSV Import Run",
			"country": country,
			"run_discovery_on_import": 1 if discovery else 0,
		}).insert(ignore_permissions=True)
		self.run_names.append(run.name)
		file_doc = save_file(
			f"{run.name}.csv", content.encode(), "Lead CSV Import Run", run.name, is_private=1,
		)
		self.file_names.append(file_doc.name)
		run.csv_file = file_doc.file_url
		run.save(ignore_permissions=True)
		return run

	def _cleanup_profile(self):
		for profile_name in (self.PROFILE_KEY, self.DISCOVERY_PROFILE_KEY):
			for member in frappe.get_all(
				"Lead Import Batch Member", filters={"company_profile": profile_name}, pluck="name"
			):
				frappe.delete_doc("Lead Import Batch Member", member, force=True, ignore_permissions=True)
			if not frappe.db.exists("Company Profile", profile_name):
				continue
			contact = find_linked_contact(profile_name)
			if contact:
				frappe.delete_doc("Contact", contact, force=True, ignore_permissions=True)
			frappe.delete_doc("Company Profile", profile_name, force=True, ignore_permissions=True)
