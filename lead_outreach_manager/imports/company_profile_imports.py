"""Idempotent SQLite -> Frappe importer for the deterministic Singapore
industrial-zone lead pipeline (/Users/phikien/lead-intelligence-pipeline).

Only imports "usable" leads (domain_status='verified' AND
contact_status='has_generic_contact') — companies with a real, found generic
contact. Safe to re-run repeatedly (e.g. daily) as the source pipeline's own
background batch job promotes more companies to usable over time:
Company Profile is keyed on uen (idempotent upsert, mirrors
receivable_risk_manager/imports/invoice_imports.py's pattern), contact_points
is rebuilt wholesale each run (pure reference data), and candidate_names is
merge-appended — never wiped — so a human's confirmed-name review work
always survives a re-import.

The source database is opened read-only since its own background scraper job
may still be writing to it concurrently.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import frappe

DEFAULT_SQLITE_PATH = "/Users/phikien/lead-intelligence-pipeline/databases/singapore_industrial_leads.db"
DEFAULT_BATCH_SIZE = 500

USABLE_LEADS_SQL = """
	SELECT * FROM companies
	WHERE domain_status = 'verified' AND contact_status = 'has_generic_contact'
	ORDER BY id
"""

CONTACT_TYPE_LABELS = {"email": "Email", "phone": "Phone", "contact_form": "Contact Form"}
CONTACT_TYPE_PRIORITY = {"email": 0, "phone": 1, "contact_form": 2}

# Frappe's default max_length for a "Data" fieldtype column (primary_email,
# primary_phone, contact_points.value are all untyped Data fields).
DATA_FIELD_MAX_LENGTH = 140


def _to_datetime(value):
	"""The source SQLite timestamps are ISO 8601 with a 'T' separator and a
	'+00:00' offset (e.g. '2026-07-11T14:53:21+00:00') — MariaDB's DATETIME
	columns reject that format outright (Incorrect datetime value). Frappe's
	own get_datetime_str normalizes it to a plain DB-storable string."""
	if not value:
		return None
	return frappe.utils.get_datetime_str(value)


def _truncate(value):
	"""Scraped strings routinely exceed the 140-char default max_length of
	untyped Data fields (long tokenized URLs, garbled page text, etc.) —
	truncate rather than let profile.save() abort the whole import row."""
	if value is None:
		return None
	return str(value)[:DATA_FIELD_MAX_LENGTH]


def import_usable_leads(
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

		profile, created = get_or_create_company_profile(conn, row)
		stats["profiles_created" if created else "profiles_updated"] += 1

		stats["candidate_names_added"] += sync_candidate_names(conn, profile, row["id"])

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


def get_or_create_company_profile(conn, row):
	"""Returns (Company Profile doc, created?). Saves once."""
	uen = row["uen"]
	if not uen:
		frappe.throw("Cannot import a company row without a UEN")

	existing_name = frappe.db.exists("Company Profile", _truncate(uen))
	if existing_name:
		profile = frappe.get_doc("Company Profile", existing_name)
		created = False
	else:
		profile = frappe.new_doc("Company Profile")
		profile.uen = _truncate(uen)
		created = True

	profile.update(
		{
			"entity_name": _truncate(row["entity_name"]),
			"entity_status_description": _truncate(row["entity_status_description"]),
			"no_of_officers": row["no_of_officers"],
			"primary_ssic_code": _truncate(row["primary_ssic_code"]),
			"primary_ssic_description": _truncate(row["primary_ssic_description"]),
			"industry_tier": _truncate(row["industry_tier"]),
			"priority_tier": _truncate(row["priority_tier"]),
			"street_name": _truncate(row["street_name"]),
			"building_name": _truncate(row["building_name"]),
			"postal_code": _truncate(row["postal_code"]),
			"postal_sector": _truncate(row["postal_sector"]),
			"geo_match_method": _truncate(row["geo_match_method"]),
			"geo_match_zone": _truncate(row["geo_match_zone"]),
			"geo_confidence": row["geo_confidence"],
			"qualified": row["qualified"],
			"website": _truncate(row["website"]),
			"domain": _truncate(row["domain"]),
			"domain_match_score": row["domain_match_score"],
			"domain_status": _truncate(row["domain_status"]),
			"crawl_status": _truncate(row["crawl_status"]),
			"contact_status": _truncate(row["contact_status"]),
			"pipeline_stage": _truncate(row["pipeline_stage"]),
			"source_company_id": row["id"],
			"source_inserted_at": _to_datetime(row["inserted_at"]),
			"source_updated_at": _to_datetime(row["updated_at"]),
			"last_imported_on": frappe.utils.now_datetime(),
		}
	)

	contact_point_rows = conn.execute(
		"""SELECT contact_type, value, is_generic, confidence, source_url, discovered_at
		   FROM contact_points WHERE company_id = ?""",
		(row["id"],),
	).fetchall()

	primary_email, primary_phone, primary_contact_type, has_email_contact = select_primary_contact_point(
		contact_point_rows
	)
	profile.has_email_contact = 1 if has_email_contact else 0
	profile.primary_contact_type = primary_contact_type
	profile.primary_email = primary_email
	profile.primary_phone = primary_phone

	profile.set("contact_points", [])
	for cp in contact_point_rows:
		profile.append(
			"contact_points",
			{
				"contact_type": CONTACT_TYPE_LABELS.get(cp["contact_type"], cp["contact_type"]),
				"value": _truncate(cp["value"]),
				"is_generic": cp["is_generic"],
				"confidence": cp["confidence"],
				"source_url": _truncate(cp["source_url"]),
				"discovered_at": _to_datetime(cp["discovered_at"]),
			},
		)

	profile.save(ignore_permissions=True)
	return profile, created


def select_primary_contact_point(contact_point_rows):
	"""rows: sqlite3.Row or plain dicts with contact_type/value/confidence
	keys (only bracket indexing used, so either works — kept dependency-free
	for direct unit testing). Preference order for primary_contact_type:
	email > phone > contact_form, ties broken by confidence descending.
	Returns (primary_email, primary_phone, primary_contact_type, has_email_contact).

	Scraped contact points are sometimes garbage (e.g. obfuscated mailto
	links that didn't parse cleanly, like '_@-b.uj', or unrelated page text
	mistaken for a phone number) — primary_email/primary_phone are Data
	fields on Company Profile, one Email-validated and both length-limited
	to DATA_FIELD_MAX_LENGTH, so a bad value here would abort the whole
	import row. Unusable values are treated as if nothing was found for
	that contact type, falling back to the next type in priority order."""
	if not contact_point_rows:
		return None, None, None, False

	def is_usable(r):
		value = r["value"] or ""
		if len(value) > DATA_FIELD_MAX_LENGTH:
			return False
		if r["contact_type"] == "email":
			return bool(frappe.utils.validate_email_address(value, throw=False))
		return True

	usable_rows = [r for r in contact_point_rows if is_usable(r)]
	if not usable_rows:
		return None, None, None, False

	ranked = sorted(
		usable_rows,
		key=lambda r: (CONTACT_TYPE_PRIORITY.get(r["contact_type"], 99), -(r["confidence"] or 0)),
	)
	best_type = ranked[0]["contact_type"]

	email = next((r["value"] for r in usable_rows if r["contact_type"] == "email"), None)
	phone = next((r["value"] for r in usable_rows if r["contact_type"] == "phone"), None)

	return email, phone, CONTACT_TYPE_LABELS.get(best_type, best_type), email is not None


def sync_candidate_names(conn, profile, source_company_id):
	"""Merge-appends new candidate_names rows onto the profile, matching the
	source SQLite table's own UNIQUE(company_id, name_text, source_document_id)
	key. Never removes or touches an existing row, so a human's `confirmed`
	state always survives a re-import. Saves if anything changed."""
	rows = conn.execute(
		"""SELECT name_text, title_text, source_url, extraction_method, source_document_id, discovered_at
		   FROM candidate_names WHERE company_id = ?""",
		(source_company_id,),
	).fetchall()

	existing_keys = {(r.name_text, r.source_document_id) for r in profile.candidate_names}
	added = 0
	for cn in rows:
		# Key on the truncated name_text (matches what's actually stored,
		# see _truncate) so re-imports of a truncated row are recognized as
		# already-staged instead of re-appended every run.
		key = (_truncate(cn["name_text"]), cn["source_document_id"])
		if key in existing_keys:
			continue
		profile.append(
			"candidate_names",
			{
				"name_text": _truncate(cn["name_text"]),
				"title_text": _truncate(cn["title_text"]),
				"source_url": _truncate(cn["source_url"]),
				"extraction_method": _truncate(cn["extraction_method"]),
				"source_document_id": cn["source_document_id"],
				"discovered_at": _to_datetime(cn["discovered_at"]),
			},
		)
		added += 1

	if added:
		profile.save(ignore_permissions=True)
	return added
