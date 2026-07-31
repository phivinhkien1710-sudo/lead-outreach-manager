import frappe
from frappe.tests.utils import FrappeTestCase

from lead_outreach_manager.services.candidate_names import confirm_candidate_name
from lead_outreach_manager.services.contacts import get_or_create_generic_contact

TEST_PREFIX = "TEST-LOM-CONFIRM"


class TestCandidateNameConfirmation(FrappeTestCase):
	def setUp(self):
		self.uen = f"{TEST_PREFIX}-UEN-1"
		self._cleanup_stale(self.uen)  # defensive: survive a prior run's incomplete tearDown
		self.profile = self._create_profile()
		self.contact = get_or_create_generic_contact(self.profile)

	def tearDown(self):
		frappe.delete_doc("Contact", self.contact.name, force=True, ignore_permissions=True)
		frappe.delete_doc("Company Profile", self.profile.name, force=True, ignore_permissions=True)
		# FrappeTestCase does not auto-commit or auto-rollback per test — cleanup
		# only sticks if explicitly committed here.
		frappe.db.commit()

	def test_confirming_updates_the_linked_contact(self):
		profile = frappe.get_doc("Company Profile", self.profile.name)
		row_name = profile.candidate_names[0].name

		result = confirm_candidate_name(profile.name, row_name)

		self.assertTrue(result["confirmed"])
		contact = frappe.get_doc("Contact", result["contact"])
		self.assertEqual(contact.first_name, "Jane")
		self.assertEqual(contact.last_name, "Tan")
		self.assertEqual(contact.designation, "Director")

		profile_after = frappe.get_doc("Company Profile", self.profile.name)
		self.assertTrue(profile_after.candidate_names[0].confirmed)
		self.assertEqual(profile_after.candidate_names[0].contact, contact.name)
		self.assertEqual(profile_after.candidate_names[0].confirmed_by, frappe.session.user)
		self.assertEqual(profile_after.candidate_names[0].confirmation_source, "Human")

	def test_confirming_guesses_emails_and_repoints_contact_primary_email(self):
		profile = frappe.get_doc("Company Profile", self.profile.name)
		row_name = profile.candidate_names[0].name

		result = confirm_candidate_name(profile.name, row_name)

		self.assertEqual(
			result["guessed_emails"],
			[
				"jane.tan@testconfirm.example",
				"jane@testconfirm.example",
				"jtan@testconfirm.example",
				"janetan@testconfirm.example",
				"jane_tan@testconfirm.example",
				"j.tan@testconfirm.example",
			],
		)

		row = frappe.get_doc("Company Profile", self.profile.name).candidate_names[0]
		self.assertEqual(row.guessed_email_1, "jane.tan@testconfirm.example")
		self.assertEqual(row.guessed_pattern_1, "first.last")
		self.assertEqual(row.guessed_email_2, "jane@testconfirm.example")
		self.assertEqual(row.guessed_email_3, "jtan@testconfirm.example")
		self.assertEqual(row.guessed_email_4, "janetan@testconfirm.example")
		self.assertEqual(row.guessed_email_5, "jane_tan@testconfirm.example")
		self.assertEqual(row.guessed_email_6, "j.tan@testconfirm.example")

		# The guessed top email becomes primary — outreach's recipient lookup
		# (contact.email_id) now resolves to it instead of the generic scraped
		# contact_points email set up in _create_profile.
		contact = frappe.get_doc("Contact", result["contact"])
		self.assertEqual(contact.email_id, "jane.tan@testconfirm.example")
		all_emails = {row.email_id for row in contact.email_ids}
		self.assertIn("info@testconfirm.example", all_emails)

	def test_confirming_twice_is_a_no_op(self):
		profile = frappe.get_doc("Company Profile", self.profile.name)
		row_name = profile.candidate_names[0].name

		confirm_candidate_name(profile.name, row_name)
		result = confirm_candidate_name(profile.name, row_name)

		self.assertTrue(result.get("already_confirmed"))
		row = frappe.get_doc("Company Profile", self.profile.name).candidate_names[0]
		self.assertEqual(row.confirmation_source, "Human")

	def _cleanup_stale(self, uen):
		from lead_outreach_manager.services.contacts import find_linked_contact

		profile_name = frappe.db.exists("Company Profile", uen)
		if profile_name:
			contact_name = find_linked_contact(profile_name)
			if contact_name:
				frappe.delete_doc("Contact", contact_name, force=True, ignore_permissions=True)
			frappe.delete_doc("Company Profile", profile_name, force=True, ignore_permissions=True)

	def _create_profile(self):
		profile = frappe.new_doc("Company Profile")
		profile.uen = self.uen
		profile.entity_name = "Test Confirm Co Pte Ltd"
		profile.domain = "testconfirm.example"
		profile.domain_status = "verified"
		profile.contact_status = "has_generic_contact"
		profile.has_email_contact = 1
		profile.primary_email = "info@testconfirm.example"
		profile.do_not_contact = 0
		profile.append(
			"candidate_names",
			{
				"name_text": "Jane Tan",
				"title_text": "Director",
				"source_url": "https://testconfirm.example/about",
				"extraction_method": "regex_proximity_v1",
				"source_document_id": 1,
			},
		)
		profile.insert(ignore_permissions=True)
		return profile
