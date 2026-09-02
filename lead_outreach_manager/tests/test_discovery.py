from unittest import TestCase, mock

from lead_outreach_manager.services.discovery import (
	candidate_domains,
	extract_candidates,
	extract_contacts,
	is_public_ip,
	name_match_score,
	public_domain_resolves,
)


class TestDiscovery(TestCase):
	def test_candidate_domains_are_region_aware(self):
		domains = candidate_domains("Acme Manufacturing Pte Ltd", "Singapore")
		self.assertIn("acmemanufacturing.com.sg", domains)
		self.assertNotIn("acmemanufacturing.com.vn", domains)

	def test_name_match_score_ignores_generic_region_words(self):
		self.assertEqual(name_match_score("Acme Singapore Pte Ltd", "Welcome to ACME"), 1)

	def test_private_network_targets_are_blocked(self):
		self.assertFalse(is_public_ip("127.0.0.1"))
		self.assertFalse(is_public_ip("10.0.0.8"))
		self.assertTrue(is_public_ip("1.1.1.1"))
		with mock.patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("192.168.1.5", 443))]):
			self.assertFalse(public_domain_resolves("internal.example"))

	def test_extracts_contacts_and_title_adjacent_candidates(self):
		html = '<a href="mailto:jane@acme.example">Email</a><p>Jane Tan - Managing Director</p>'
		pages = [("https://acme.example/contact", "jane@acme.example Jane Tan - Managing Director", html)]
		contacts = extract_contacts(pages, "acme.example")
		candidates = extract_candidates(pages)
		self.assertTrue(any(row["value"] == "jane@acme.example" for row in contacts))
		self.assertTrue(any(row["name_text"] == "Jane Tan" for row in candidates))
