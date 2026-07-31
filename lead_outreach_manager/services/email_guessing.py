"""Best-effort personal email guessing for a confirmed candidate name, using
the company's already-verified domain (Company Profile.domain) and common
first/last-name email conventions.

Unlike the rest of this app's contact data, these are unverified guesses, not
scraped/confirmed facts — by product decision they're still used automatically
as the outreach recipient (services.candidate_names.confirm_candidate_name
pushes the top guess onto the linked Contact as its primary email).
"""
from __future__ import annotations

import re

_NON_ALPHA_RE = re.compile(r"[^a-z]")


def _clean(name_part: str) -> str:
	"""Lowercases and strips anything that isn't a plain ASCII letter
	(hyphens, apostrophes, diacritics-as-typed) so the result is a valid
	local-part fragment."""
	return _NON_ALPHA_RE.sub("", (name_part or "").lower())


def guess_emails(first_name: str, last_name: str, domain: str, limit: int = 6) -> list[dict]:
	"""Returns up to `limit` ranked {pattern, email} guesses, most-common
	convention first. Pure function, deliberately dependency-free so it's
	directly unit-testable. Returns [] if there's not enough to guess from
	(no domain, or no usable first name)."""
	first = _clean(first_name)
	last = _clean(last_name)
	domain = (domain or "").strip().lower()
	if not first or not domain:
		return []

	candidate_patterns = [
		("first.last", f"{first}.{last}" if last else None),
		("first", first),
		("flast", f"{first[0]}{last}" if last else None),
		("firstlast", f"{first}{last}" if last else None),
		("first_last", f"{first}_{last}" if last else None),
		("f.last", f"{first[0]}.{last}" if last else None),
	]

	seen_locals = set()
	guesses = []
	for pattern, local_part in candidate_patterns:
		if not local_part or local_part in seen_locals:
			continue
		seen_locals.add(local_part)
		guesses.append({"pattern": pattern, "email": f"{local_part}@{domain}"})
		if len(guesses) >= limit:
			break

	return guesses


def apply_top_guess_to_contact(contact, email):
	"""Makes `email` the Contact's primary email — Contact.validate() derives
	Contact.email_id from whichever email_ids row has is_primary=1, and
	services.outreach_emails.create_outreach_email reads contact.email_id as
	its recipient, so this alone is enough to redirect outreach to the
	guessed personal address. Demotes any existing primary (e.g. the generic
	contact_points email) rather than removing it."""
	existing_row = next((row for row in contact.email_ids if row.email_id == email), None)
	for row in contact.email_ids:
		row.is_primary = 0
	if existing_row:
		existing_row.is_primary = 1
	else:
		contact.append("email_ids", {"email_id": email, "is_primary": 1})
	contact.save(ignore_permissions=True)
