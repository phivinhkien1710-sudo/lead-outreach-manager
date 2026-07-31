import unittest

from lead_outreach_manager.services.email_verification import (
	VerificationAPIError,
	VerificationParseError,
	band_verification_result,
	chunked,
	parse_verification_response,
	pick_best_attempt,
)


class TestBandVerificationResult(unittest.TestCase):
	def test_ok_is_verified(self):
		self.assertEqual(band_verification_result("ok"), "verified")

	def test_catch_all_and_unknown_variants(self):
		self.assertEqual(band_verification_result("catch_all"), "catch_all")
		self.assertEqual(band_verification_result("unknown"), "catch_all")
		self.assertEqual(band_verification_result("unverified"), "catch_all")

	def test_invalid_and_disposable_are_not_deliverable(self):
		self.assertEqual(band_verification_result("invalid"), "not_deliverable")
		self.assertEqual(band_verification_result("disposable"), "not_deliverable")

	def test_case_insensitive_and_whitespace_tolerant(self):
		self.assertEqual(band_verification_result(" OK "), "verified")

	def test_unrecognized_result_is_none(self):
		self.assertIsNone(band_verification_result("some_future_value"))
		self.assertIsNone(band_verification_result(""))
		self.assertIsNone(band_verification_result(None))


class TestParseVerificationResponse(unittest.TestCase):
	def test_valid_payload_passes_through(self):
		payload = {"email": "jane@x.example", "result": "ok", "credits": 100}
		self.assertEqual(parse_verification_response(payload), payload)

	def test_error_field_raises_api_error(self):
		with self.assertRaises(VerificationAPIError):
			parse_verification_response({"error": "invalid api key"})

	def test_missing_result_raises_parse_error(self):
		with self.assertRaises(VerificationParseError):
			parse_verification_response({"email": "jane@x.example"})

	def test_non_dict_raises_parse_error(self):
		with self.assertRaises(VerificationParseError):
			parse_verification_response(["not", "a", "dict"])


class TestPickBestAttempt(unittest.TestCase):
	def test_empty_attempts_returns_none(self):
		self.assertIsNone(pick_best_attempt([]))

	def test_first_verified_wins_even_if_not_first_attempt(self):
		attempts = [
			{"field": "guessed_email_1", "email": "a@x.example", "result": "invalid", "bucket": "not_deliverable"},
			{"field": "guessed_email_2", "email": "b@x.example", "result": "ok", "bucket": "verified"},
		]
		self.assertEqual(pick_best_attempt(attempts)["email"], "b@x.example")

	def test_no_verified_prefers_catch_all_over_not_deliverable(self):
		attempts = [
			{"field": "guessed_email_1", "email": "a@x.example", "result": "invalid", "bucket": "not_deliverable"},
			{"field": "guessed_email_2", "email": "b@x.example", "result": "catch_all", "bucket": "catch_all"},
		]
		self.assertEqual(pick_best_attempt(attempts)["email"], "b@x.example")

	def test_single_not_deliverable_attempt_is_returned(self):
		attempts = [{"field": "guessed_email_1", "email": "a@x.example", "result": "invalid", "bucket": "not_deliverable"}]
		self.assertEqual(pick_best_attempt(attempts)["email"], "a@x.example")


class TestChunked(unittest.TestCase):
	def test_delegates_to_candidate_classification_chunked(self):
		self.assertEqual(list(chunked([1, 2, 3, 4, 5], 2)), [[1, 2], [3, 4], [5]])
