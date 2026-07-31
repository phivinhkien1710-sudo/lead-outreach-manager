import unittest

from lead_outreach_manager.services.email_guessing import guess_emails


class TestGuessEmails(unittest.TestCase):
	def test_ranked_guesses_for_full_name(self):
		guesses = guess_emails("Jane", "Tan", "testconfirm.example")
		self.assertEqual(
			guesses,
			[
				{"pattern": "first.last", "email": "jane.tan@testconfirm.example"},
				{"pattern": "first", "email": "jane@testconfirm.example"},
				{"pattern": "flast", "email": "jtan@testconfirm.example"},
				{"pattern": "firstlast", "email": "janetan@testconfirm.example"},
				{"pattern": "first_last", "email": "jane_tan@testconfirm.example"},
				{"pattern": "f.last", "email": "j.tan@testconfirm.example"},
			],
		)

	def test_respects_limit(self):
		guesses = guess_emails("Jane", "Tan", "testconfirm.example", limit=1)
		self.assertEqual(guesses, [{"pattern": "first.last", "email": "jane.tan@testconfirm.example"}])

	def test_no_last_name_only_yields_first_pattern(self):
		guesses = guess_emails("Cher", "", "testconfirm.example")
		self.assertEqual(guesses, [{"pattern": "first", "email": "cher@testconfirm.example"}])

	def test_no_domain_returns_empty(self):
		self.assertEqual(guess_emails("Jane", "Tan", ""), [])

	def test_no_first_name_returns_empty(self):
		self.assertEqual(guess_emails("", "Tan", "testconfirm.example"), [])

	def test_strips_non_letters_and_lowercases(self):
		guesses = guess_emails("O'Brien-Jane", "Tan", "TestConfirm.example", limit=1)
		self.assertEqual(guesses[0]["email"], "obrienjane.tan@testconfirm.example")
