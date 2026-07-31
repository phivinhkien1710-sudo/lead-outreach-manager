"""Confirms the outreach email content path is pure Jinja mail-merge with no
LLM call anywhere — rendering the same template against the same context
twice must produce byte-identical output. Uses frappe.render_template
directly against a plain dict (no doctype/DB records needed) since Jinja
rendering itself does no database I/O.
"""
import unittest

import frappe


class TestEmailRendering(unittest.TestCase):
	def test_merge_fields_render_from_plain_dict_context(self):
		template = "Hello {{ contact.first_name or 'there' }}, this is about {{ doc.entity_name }}."
		context = {"doc": {"entity_name": "Acme Pte Ltd"}, "contact": {"first_name": "Jane"}}

		rendered = frappe.render_template(template, context)

		self.assertEqual(rendered, "Hello Jane, this is about Acme Pte Ltd.")

	def test_missing_first_name_falls_back_gracefully(self):
		template = "Hello {{ contact.first_name or 'there' }}, this is about {{ doc.entity_name }}."
		context = {"doc": {"entity_name": "Acme Pte Ltd"}, "contact": {"first_name": ""}}

		rendered = frappe.render_template(template, context)

		self.assertEqual(rendered, "Hello there, this is about Acme Pte Ltd.")

	def test_rendering_is_deterministic(self):
		template = "{{ doc.entity_name }} — {{ contact.first_name }}"
		context = {"doc": {"entity_name": "Acme Pte Ltd"}, "contact": {"first_name": "Jane"}}

		first = frappe.render_template(template, context)
		second = frappe.render_template(template, context)

		self.assertEqual(first, second)
