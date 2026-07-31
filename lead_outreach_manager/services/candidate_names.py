"""Confirming a candidate name personalizes the company's one generic Contact
in place — it does not create a second Contact — so there's never ambiguity
about which of several possibly-confirmed names an outreach email would use.
candidate_names rows are a known-noisy, regex-extracted bonus signal from the
source pipeline (roughly 60-70% marketing copy, not real names, even after an
upstream cleanup pass), so a row is only confirmed by a human clicking Confirm
Name, or by the classification funnel (services/candidate_classification.py)
under its stricter rules. The two automatic paths differ from the human one in
exactly one way: they never redirect the outreach recipient to a *guessed*
address — only the human click does that (Email Match may redirect to the real
scraped address that corroborated the name).
"""
from __future__ import annotations

import frappe


def confirm_candidate_name(company_profile, row_name):
	profile = frappe.get_doc("Company Profile", company_profile)
	row = next((r for r in profile.candidate_names if r.name == row_name), None)
	if not row:
		frappe.throw(f"Candidate name row not found on {company_profile}: {row_name}")

	if row.confirmed:
		return {"confirmed": True, "already_confirmed": True, "contact": row.contact}

	result = apply_confirmation(profile, row, source="Human", redirect_to_top_guess=True)
	profile.save(ignore_permissions=True)
	return result


def apply_confirmation(profile, row, *, source, redirect_to_top_guess=False, redirect_email=None):
	"""Shared confirmation core for the human path and both automatic funnel
	paths. Personalizes the generic Contact and stores email guesses on the row
	(informational, all paths), but redirects the outreach recipient only when
	asked: `redirect_email` (Email Match — a real scraped address) wins over
	`redirect_to_top_guess` (Human — today's guessed-address behavior); with
	neither, the Contact's primary email stays the generic scraped one.
	Does NOT save the profile — the caller saves."""
	from lead_outreach_manager.services.contacts import get_or_create_generic_contact
	from lead_outreach_manager.services.email_guessing import apply_top_guess_to_contact, guess_emails

	contact = get_or_create_generic_contact(profile)
	apply_name_to_contact(contact, row.name_text, row.title_text)

	first_name, last_name = split_name(row.name_text)
	guesses = guess_emails(first_name, last_name, profile.domain)
	_apply_guesses_to_row(row, guesses)

	if redirect_email:
		apply_top_guess_to_contact(contact, redirect_email)
	elif redirect_to_top_guess and guesses:
		apply_top_guess_to_contact(contact, guesses[0]["email"])

	row.confirmed = 1
	row.confirmed_by = frappe.session.user
	row.confirmed_on = frappe.utils.now_datetime()
	row.contact = contact.name
	row.confirmation_source = source

	return {"confirmed": True, "contact": contact.name, "guessed_emails": [g["email"] for g in guesses]}


def _apply_guesses_to_row(row, guesses):
	for i in range(6):
		guess = guesses[i] if i < len(guesses) else None
		row.set(f"guessed_email_{i + 1}", guess["email"] if guess else None)
		row.set(f"guessed_pattern_{i + 1}", guess["pattern"] if guess else None)


def apply_name_to_contact(contact, name_text, title_text=None):
	first_name, last_name = split_name(name_text)
	contact.first_name = first_name
	contact.last_name = last_name
	if title_text:
		contact.designation = title_text
	contact.save(ignore_permissions=True)
	return contact


HONORIFICS = {"mr", "mrs", "ms", "mdm", "dr", "miss", "prof", "sir", "madam"}
HONORIFIC_FIRST_NAME_SQL_LIST = "'" + "','".join(sorted(HONORIFICS)) + "'"


def backfill_honorific_split_names():
	"""One-off cleanup for Contacts personalized before split_name stripped
	honorifics (e.g. "Mr Sam Chee Keong" -> first_name="Mr"). Only touches
	Contacts whose CURRENT first_name is still literally an honorific token —
	never blind-reapplies split_name to everyone, which could clobber a
	manual edit unrelated to this bug. Re-derives from the same name_text
	that originally confirmed the row, and refreshes the row's informational
	guessed_email_*/guessed_pattern_* fields for consistency — never touches
	any recipient email (no affected row was human-confirmed as of this
	writing, so no guess was ever applied as a real send target; this
	function wouldn't touch it even if one had been, since guesses are only
	ever *applied* by apply_confirmation's own redirect logic, not here).
	bench execute lead_outreach_manager.services.candidate_names.backfill_honorific_split_names
	"""
	from lead_outreach_manager.services.email_guessing import guess_emails

	rows = frappe.db.sql(
		f"""
		SELECT ccn.name AS row_name, ccn.name_text, ccn.contact, ccn.parent
		FROM `tabCompany Candidate Name` ccn
		JOIN `tabContact` c ON c.name = ccn.contact
		WHERE ccn.confirmed = 1
		  AND LOWER(TRIM(TRAILING '.' FROM c.first_name)) IN ({HONORIFIC_FIRST_NAME_SQL_LIST})
		""",
		as_dict=True,
	)

	fixed = 0
	for row in rows:
		first_name, last_name = split_name(row.name_text)
		domain = frappe.db.get_value("Company Profile", row.parent, "domain")

		frappe.db.set_value("Contact", row.contact, {"first_name": first_name, "last_name": last_name})

		guesses = guess_emails(first_name, last_name, domain)
		updates = {}
		for i in range(6):
			guess = guesses[i] if i < len(guesses) else None
			updates[f"guessed_email_{i + 1}"] = guess["email"] if guess else None
			updates[f"guessed_pattern_{i + 1}"] = guess["pattern"] if guess else None
		frappe.db.set_value("Company Candidate Name", row.row_name, updates)
		fixed += 1

	frappe.db.commit()
	return {"fixed": fixed}


def backfill_missing_guesses():
	"""One-off backfill for candidates confirmed before guess_emails grew from
	3 to 6 patterns (see services/email_guessing.py) — guessed_email_4-6 are
	NULL for every row confirmed under the old code, since guesses are only
	ever generated once, at confirmation time (apply_confirmation), not
	re-derived later. Re-derives all 6 from the same name_text + Company
	Profile.domain that originally confirmed the row and fills in what's
	missing. Purely informational fields; never touches any recipient email —
	guesses are only ever *applied* to a Contact by apply_confirmation's own
	redirect logic, not here.
	bench execute lead_outreach_manager.services.candidate_names.backfill_missing_guesses
	"""
	from lead_outreach_manager.services.email_guessing import guess_emails

	rows = frappe.db.sql(
		"""
		SELECT ccn.name AS row_name, ccn.name_text, ccn.parent
		FROM `tabCompany Candidate Name` ccn
		WHERE ccn.confirmed = 1
		  AND IFNULL(ccn.guessed_email_1, '') != ''
		  AND IFNULL(ccn.guessed_email_4, '') = ''
		""",
		as_dict=True,
	)

	fixed = 0
	for row in rows:
		first_name, last_name = split_name(row.name_text)
		domain = frappe.db.get_value("Company Profile", row.parent, "domain")

		guesses = guess_emails(first_name, last_name, domain)
		updates = {}
		for i in range(6):
			guess = guesses[i] if i < len(guesses) else None
			updates[f"guessed_email_{i + 1}"] = guess["email"] if guess else None
			updates[f"guessed_pattern_{i + 1}"] = guess["pattern"] if guess else None
		frappe.db.set_value("Company Candidate Name", row.row_name, updates)
		fixed += 1

	frappe.db.commit()
	return {"fixed": fixed, "total_checked": len(rows)}


def split_name(name_text: str) -> tuple[str, str]:
	"""Pure string logic, deliberately dependency-free so it's testable
	without a Frappe DB. Strips a leading honorific first (case-insensitive,
	with or without a trailing period) — scraped text like "Mr Sam Chee Keong"
	would otherwise split into first_name="Mr", last_name="Sam Chee Keong"."""
	parts = (name_text or "").split()
	while parts and parts[0].strip(".").lower() in HONORIFICS:
		parts = parts[1:]
	if not parts:
		return "", ""
	if len(parts) == 1:
		return parts[0], ""
	return parts[0], " ".join(parts[1:])
