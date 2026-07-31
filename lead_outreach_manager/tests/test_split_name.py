import unittest

from lead_outreach_manager.services.candidate_names import split_name


class TestSplitName(unittest.TestCase):
	def test_plain_two_word_name(self):
		self.assertEqual(split_name("Jane Tan"), ("Jane", "Tan"))

	def test_single_word_name(self):
		self.assertEqual(split_name("Cher"), ("Cher", ""))

	def test_multi_word_last_name(self):
		self.assertEqual(split_name("Corenna Liu Mei Ying"), ("Corenna", "Liu Mei Ying"))

	def test_empty_and_none(self):
		self.assertEqual(split_name(""), ("", ""))
		self.assertEqual(split_name(None), ("", ""))

	def test_strips_leading_honorific(self):
		self.assertEqual(split_name("Mr Sam Chee Keong"), ("Sam", "Chee Keong"))
		self.assertEqual(split_name("Mrs Kuan Teik Guan"), ("Kuan", "Teik Guan"))
		self.assertEqual(split_name("Dr Sunny Goh"), ("Sunny", "Goh"))
		self.assertEqual(split_name("Mdm Tan"), ("Tan", ""))

	def test_honorific_is_case_insensitive(self):
		self.assertEqual(split_name("MR John Lim"), ("John", "Lim"))
		self.assertEqual(split_name("dr Jane Wong"), ("Jane", "Wong"))

	def test_honorific_with_trailing_period(self):
		self.assertEqual(split_name("Mr. Piyush Gupta"), ("Piyush", "Gupta"))
		self.assertEqual(split_name("Dr. Sunny Goh"), ("Sunny", "Goh"))

	def test_honorific_only_yields_empty(self):
		# Degenerate scraped input with nothing after the title.
		self.assertEqual(split_name("Mr"), ("", ""))
		self.assertEqual(split_name("Mr."), ("", ""))

	def test_does_not_strip_a_real_name_that_merely_contains_an_honorific_substring(self):
		# "Miss" is an honorific token, but "Misty" must not be treated as one —
		# the check is on the whole first token, not a substring match.
		self.assertEqual(split_name("Misty Copeland"), ("Misty", "Copeland"))

	def test_stacked_honorifics_all_stripped(self):
		self.assertEqual(split_name("Dr Mr Tan Ah Kow"), ("Tan", "Ah Kow"))
