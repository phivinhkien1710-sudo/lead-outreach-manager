import unittest

from lead_outreach_manager.imports.company_profile_imports import select_primary_contact_point


class TestSelectPrimaryContactPoint(unittest.TestCase):
	def test_empty_list_returns_all_none(self):
		self.assertEqual(select_primary_contact_point([]), (None, None, None, False))

	def test_email_preferred_over_phone_and_contact_form(self):
		rows = [
			{"contact_type": "contact_form", "value": "https://x.com/contact", "confidence": 1.0},
			{"contact_type": "phone", "value": "+65 6123 4567", "confidence": 0.7},
			{"contact_type": "email", "value": "info@x.com", "confidence": 0.9},
		]
		email, phone, primary_type, has_email = select_primary_contact_point(rows)
		self.assertEqual(email, "info@x.com")
		self.assertEqual(phone, "+65 6123 4567")
		self.assertEqual(primary_type, "Email")
		self.assertTrue(has_email)

	def test_phone_preferred_over_contact_form_when_no_email(self):
		rows = [
			{"contact_type": "contact_form", "value": "https://x.com/contact", "confidence": 1.0},
			{"contact_type": "phone", "value": "+65 6123 4567", "confidence": 0.7},
		]
		email, phone, primary_type, has_email = select_primary_contact_point(rows)
		self.assertIsNone(email)
		self.assertEqual(phone, "+65 6123 4567")
		self.assertEqual(primary_type, "Phone")
		self.assertFalse(has_email)

	def test_contact_form_only(self):
		rows = [{"contact_type": "contact_form", "value": "https://x.com/contact", "confidence": 1.0}]
		email, phone, primary_type, has_email = select_primary_contact_point(rows)
		self.assertIsNone(email)
		self.assertIsNone(phone)
		self.assertEqual(primary_type, "Contact Form")
		self.assertFalse(has_email)

	def test_confidence_does_not_override_type_priority(self):
		# A low-confidence email still outranks a high-confidence phone/contact-form
		# for primary_contact_type — type ordering wins first, confidence is only
		# a tiebreak within the same type.
		rows = [
			{"contact_type": "contact_form", "value": "https://x.com/contact", "confidence": 1.0},
			{"contact_type": "email", "value": "info@x.com", "confidence": 0.1},
		]
		_, _, primary_type, has_email = select_primary_contact_point(rows)
		self.assertEqual(primary_type, "Email")
		self.assertTrue(has_email)
