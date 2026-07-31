"""Attaches Contacts to Company Profiles via Frappe's standard Dynamic Link
mechanism (the same one Lead/Prospect/Customer already use) rather than a
custom join doctype, and keeps the one auto-created "generic" Contact per
company in sync with its current best contact_points values.

Exactly one Contact is auto-created per Company Profile at import time, from
the already-verified generic email/phone — no personal name is set here.
Personalizing it (first_name/last_name/designation) only ever happens through
services.candidate_names.apply_confirmed_name_to_contact(), once a human has
explicitly confirmed a candidate name. This avoids ever addressing someone by
a name that's actually scraped marketing copy.
"""
from __future__ import annotations

import frappe


def get_or_create_generic_contact(profile):
	"""Ensures exactly one Contact exists for this Company Profile, carrying
	its current primary_email/primary_phone. Safe to call repeatedly (e.g. on
	every importer re-run) — updates in place rather than duplicating."""
	contact_name = find_linked_contact(profile.name)
	if contact_name:
		contact = frappe.get_doc("Contact", contact_name)
	else:
		contact = frappe.new_doc("Contact")

	contact.company_name = profile.entity_name
	_set_primary_email(contact, profile.primary_email)
	_set_primary_phone(contact, profile.primary_phone)
	contact.save(ignore_permissions=True)

	attach_contact_to_profile(contact, profile)
	return contact


def find_linked_contact(profile_name):
	rows = frappe.get_all(
		"Dynamic Link",
		filters={
			"link_doctype": "Company Profile",
			"link_name": profile_name,
			"parenttype": "Contact",
		},
		fields=["parent"],
		limit=1,
	)
	return rows[0].parent if rows else None


def attach_contact_to_profile(contact, profile):
	already_linked = any(
		link.link_doctype == "Company Profile" and link.link_name == profile.name
		for link in contact.links
	)
	if already_linked:
		return

	contact.append(
		"links",
		{"link_doctype": "Company Profile", "link_name": profile.name, "link_title": profile.entity_name},
	)
	contact.save(ignore_permissions=True)


def _set_primary_email(contact, email):
	if not email:
		return
	existing = [row.email_id for row in contact.email_ids]
	if email in existing:
		return
	contact.append("email_ids", {"email_id": email, "is_primary": 1 if not existing else 0})


def _set_primary_phone(contact, phone):
	if not phone:
		return
	# The source pipeline's PHONE_RE is shape-based and known to let some
	# garbage through (e.g. "3-1 7 8 4-1 5 6 2 1-1") — Contact.phone_nos'
	# Phone-typed field validates strictly and would abort the whole save.
	# The raw value still lives in Company Contact Point (plain, unvalidated
	# Data) for reference; it's just not propagated into Contact if invalid.
	if not frappe.utils.validate_phone_number(phone, throw=False):
		return
	existing = [row.phone for row in contact.phone_nos]
	if phone in existing:
		return
	contact.append("phone_nos", {"phone": phone, "is_primary_phone": 1 if not existing else 0})
