"""Idempotent SQLite -> Frappe importer for the Vietnam lead pipeline
(/Users/phikien/lead-intelligence-pipeline/databases/vietnam_leads.db).

Vietnam's source is a single flat `leads` table (the shared region-agnostic
schema in src/lead_intelligence/common/storage.py — also what the old,
retired Singapore funnel used) rather than the Singapore industrial
pipeline's normalized companies/contact_points/candidate_names/
company_documents that company_profile_imports.py reads. This importer maps
that flat shape onto the same target doctypes so both regions flow through
one Company Profile / Contact / Candidate Name model.

Two deliberate departures from company_profile_imports.py, both product
decisions made when this importer was built:

1. No UEN equivalent exists for Vietnam (its natural key is
   region/source/source_id, not a government entity number), so
   Company Profile.uen is synthesized as f"VN-{row['id']}" — leads.id is a
   stable autoincrement PK, and the "VN-" prefix can't collide with a real
   Singapore UEN.

2. The source `email` column is trusted as-is, the same way Singapore's
   scraped contact_points are — even though the source pipeline's own
   raw_json distinguishes `found_email` (scraped) from `guessed_email`
   (unverified pattern guess), and a majority of `email` values here are the
   latter. Trusting it means has_email_contact ends up set, and these
   profiles become outreach-eligible, without going through
   services/email_verification.py first. Re-verifying a promoted address is
   still possible later the normal way (it's just an address on the
   Contact, not a special case), but nothing forces it for these rows the
   way it's forced for a fresh guess off a newly confirmed candidate name.

Only imports rows with both a domain and a representative_name — the
minimum needed to build a Candidate Name row. Safe to re-run: Company
Profile is keyed on uen (idempotent upsert), contact_points is rebuilt
wholesale each run (pure reference data, mirrors company_profile_imports.py),
and candidate_names is merge-appended — never wiped — so a human's
confirmed-name review work always survives a re-import.

The source database is opened read-only since the pipeline's own background
jobs may still be writing to it concurrently.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import frappe

DEFAULT_SQLITE_PATH = "/Users/phikien/lead-intelligence-pipeline/databases/vietnam_leads.db"
DEFAULT_BATCH_SIZE = 500

# Frappe's default max_length for a "Data" fieldtype column.
DATA_FIELD_MAX_LENGTH = 140

USABLE_LEADS_SQL = """
	SELECT * FROM leads
	WHERE domain IS NOT NULL AND domain != ''
	  AND representative_name IS NOT NULL AND representative_name != ''
	ORDER BY id
"""


def _to_datetime(value):
	"""Source timestamps are ISO 8601 with a 'T' separator and a '+00:00'
	offset — MariaDB's DATETIME columns reject that format outright."""
	if not value:
		return None
	return frappe.utils.get_datetime_str(value)


def _truncate(value):
	if value is None:
		return None
	return str(value)[:DATA_FIELD_MAX_LENGTH]


def split_emails(value: str) -> list[str]:
	"""Vietnam's `email` column can hold several ';'-separated addresses for
	the same person (e.g. multiple guess-pattern variants) rather than
	Singapore's one-row-per-contact-point shape. Splits, validates, dedupes,
	and preserves source order — the first is what becomes primary_email.
	Pure function, deliberately dependency-light so it's directly
	unit-testable (only frappe.utils.validate_email_address is used, no doc
	access)."""
	if not value:
		return []
	seen = set()
	emails = []
	for part in value.split(";"):
		email = part.strip()
		if not email or email in seen:
			continue
		if not frappe.utils.validate_email_address(email, throw=False):
			continue
		seen.add(email)
		emails.append(email)
	return emails


def import_vietnam_leads(
	sqlite_path: str = DEFAULT_SQLITE_PATH, batch_size: int = DEFAULT_BATCH_SIZE, limit: int | None = None,
) -> dict:
	path = Path(sqlite_path).expanduser()
	if not path.exists():
		frappe.throw(f"SQLite database not found: {path}")

	conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
	conn.row_factory = sqlite3.Row

	sql = USABLE_LEADS_SQL
	if limit:
		sql += f" LIMIT {int(limit)}"

	stats = {
		"rows_seen": 0,
		"profiles_created": 0,
		"profiles_updated": 0,
		"contacts_created": 0,
		"contacts_updated": 0,
		"candidate_names_added": 0,
	}

	for row in conn.execute(sql):
		stats["rows_seen"] += 1

		profile, created = get_or_create_vietnam_profile(row)
		stats["profiles_created" if created else "profiles_updated"] += 1

		stats["candidate_names_added"] += sync_vietnam_candidate_name(profile, row)

		if profile.primary_email or profile.primary_phone:
			from lead_outreach_manager.services.contacts import find_linked_contact, get_or_create_generic_contact

			had_contact_already = bool(find_linked_contact(profile.name))
			get_or_create_generic_contact(profile)
			stats["contacts_updated" if had_contact_already else "contacts_created"] += 1

		if stats["rows_seen"] % batch_size == 0:
			frappe.db.commit()

	conn.close()
	frappe.db.commit()

	if stats["candidate_names_added"]:
		# Opt-in via Outreach Settings (auto_classify_after_import); a failure
		# to enqueue must never fail an otherwise-successful import.
		try:
			from lead_outreach_manager.services.candidate_classification import (
				enqueue_post_import_classification,
			)

			enqueue_post_import_classification()
		except Exception:
			frappe.log_error(
				title="Post-import classification enqueue failed", message=frappe.get_traceback()
			)

	return stats


def get_or_create_vietnam_profile(row):
	"""Returns (Company Profile doc, created?). Saves once.
	`row` needs the full `leads` row shape — id, company_name, website,
	domain, email, phone, source_url, inserted_at, updated_at at minimum."""
	uen = f"VN-{row['id']}"

	existing_name = frappe.db.exists("Company Profile", uen)
	if existing_name:
		profile = frappe.get_doc("Company Profile", existing_name)
		created = False
	else:
		profile = frappe.new_doc("Company Profile")
		profile.uen = uen
		created = True

	profile.update(
		{
			"entity_name": _truncate(row["company_name"]),
			"country": "Vietnam",
			"website": _truncate(row["website"]),
			"domain": _truncate(row["domain"]),
			"source_company_id": row["id"],
			"source_inserted_at": _to_datetime(row["inserted_at"]),
			"source_updated_at": _to_datetime(row["updated_at"]),
			"last_imported_on": frappe.utils.now_datetime(),
		}
	)

	emails = split_emails(row["email"])
	phone = _truncate(row["phone"]) or None

	profile.has_email_contact = 1 if emails else 0
	profile.primary_contact_type = "Email" if emails else ("Phone" if phone else None)
	profile.primary_email = emails[0] if emails else None
	profile.primary_phone = phone

	profile.set("contact_points", [])
	for email in emails:
		profile.append(
			"contact_points",
			{
				"contact_type": "Email",
				"value": email,
				# Tied to a specific person (representative_name), not a
				# generic company inbox — same convention candidate_names'
				# own LLM-guessed contacts use (see candidate_classification.py).
				"is_generic": 0,
				"confidence": row["confidence"],
				"source_url": _truncate(row["source_url"]),
				"discovered_at": _to_datetime(row["inserted_at"]),
			},
		)
	if phone:
		profile.append(
			"contact_points",
			{
				"contact_type": "Phone",
				"value": phone,
				"is_generic": 0,
				"confidence": row["confidence"],
				"source_url": _truncate(row["source_url"]),
				"discovered_at": _to_datetime(row["inserted_at"]),
			},
		)

	profile.save(ignore_permissions=True)
	return profile, created


def sync_vietnam_candidate_name(profile, row) -> int:
	"""One candidate per lead row — Vietnam's pipeline already normalizes to
	a single representative person per company, unlike Singapore's
	multi-page-scrape candidate_names. Keyed on name_text alone (Vietnam has
	no per-document source id), so a re-import recognizes an
	already-staged row instead of re-appending it every run — same
	never-wipe guarantee as company_profile_imports.sync_candidate_names, so
	a human's `confirmed` state always survives a re-import."""
	name_text = _truncate(row["representative_name"])
	if any(cn.name_text == name_text for cn in profile.candidate_names):
		return 0

	profile.append(
		"candidate_names",
		{
			"name_text": name_text,
			"title_text": _truncate(row["position"]),
			"source_url": _truncate(row["source_url"]),
			"extraction_method": "vietnam_pipeline_import",
			"discovered_at": _to_datetime(row["inserted_at"]),
		},
	)
	profile.save(ignore_permissions=True)
	return 1
