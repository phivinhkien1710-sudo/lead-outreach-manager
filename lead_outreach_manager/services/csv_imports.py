"""Desk-uploaded, idempotent CSV import for process-ready lead records.

This is intentionally an ingestion adapter, not a discovery crawler. Every
row must already include a company, domain, and candidate person. Optional
contact fields make a profile immediately outreach-eligible; otherwise the
candidate can still proceed through classification and email guessing.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from urllib.parse import urlparse

import frappe

QUEUEABLE_STATUSES = ("Validated", "Completed With Errors")
IMPORT_UPDATE_EVENT = "lead_csv_import_update"
COMMIT_EVERY = 100
MAX_ERRORS = 20
DATA_MAX_LENGTH = 140

COMPANY_COLUMNS = ("company_name", "entity_name")
CANDIDATE_COLUMNS = ("candidate_name", "representative_name", "name_text")
SOURCE_ID_COLUMNS = ("source_id", "id")
TITLE_COLUMNS = ("position", "title", "title_text")


def validate_import_run(run_name):
	run = frappe.get_doc("Lead CSV Import Run", run_name)
	rows, errors, total_rows, valid_row_count = read_and_validate(run)
	run.total_rows = total_rows
	run.valid_rows = valid_row_count
	run.invalid_rows = total_rows - valid_row_count
	run.error_summary = "\n".join(errors[:MAX_ERRORS])
	run.status = "Validated" if valid_row_count else "Validation Failed"
	run.save(ignore_permissions=True)
	return {"total_rows": total_rows, "valid_rows": valid_row_count, "invalid_rows": run.invalid_rows, "selected_rows": len(rows), "errors": errors[:MAX_ERRORS]}


def queue_import_run(run_name):
	run = frappe.get_doc("Lead CSV Import Run", run_name)
	if run.status not in QUEUEABLE_STATUSES:
		frappe.throw(f"Validate the CSV before importing (current status: {run.status}).")
	run.status = "Queued"
	run.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.enqueue(
		run_background_import,
		queue="long",
		timeout=6000,
		job_id=f"lead-csv-import-{run.name}",
		deduplicate=True,
		lead_csv_import_run_name=run.name,
	)
	return {"status": "Queued", "queued": True}


def run_background_import(lead_csv_import_run_name):
	return run_import(lead_csv_import_run_name)


def run_import(run_name):
	run = frappe.get_doc("Lead CSV Import Run", run_name)
	run.status = "Importing"
	run.started_on = frappe.utils.now_datetime()
	run.completed_on = None
	run.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		rows, validation_errors, total_rows, valid_row_count = read_and_validate(run)
		stats = {
			"profiles_created": 0,
			"profiles_updated": 0,
			"contacts_created": 0,
			"contacts_updated": 0,
			"candidate_names_added": 0,
			"domains_discovered": 0,
			"profiles_crawled": 0,
			"contacts_discovered": 0,
			"candidates_discovered": 0,
			"discovery_failed": 0,
			"batch_companies": 0,
		}
		import_errors = []
		if run.run_discovery_on_import:
			from lead_outreach_manager.services.discovery import discover_rows

			discovery_results, discovery_errors = discover_rows(
				rows,
				workers=frappe.utils.cint(run.discovery_workers) or 4,
				timeout=frappe.utils.cint(run.discovery_timeout) or 12,
				max_pages=frappe.utils.cint(run.discovery_max_pages) or 6,
			)
			import_errors.extend(discovery_errors)
			for item, result in zip(rows, discovery_results):
				apply_discovery(item, result, stats)
		for index, item in enumerate(rows, start=1):
			savepoint = f"lead_csv_row_{index}"
			frappe.db.savepoint(savepoint)
			row_stats = {key: 0 for key in stats}
			try:
				import_row(item, row_stats, import_run=run.name)
				for key, value in row_stats.items():
					stats[key] += value
			except Exception as exc:
				frappe.db.rollback(save_point=savepoint)
				import_errors.append(f"Row {item['row_number']}: {exc}")
			if index % COMMIT_EVERY == 0:
				frappe.db.commit()

		frappe.db.commit()
		stats["batch_companies"] = frappe.db.count(
			"Lead Import Batch Member", {"import_run": run.name}
		)
		run.reload()
		run.total_rows = total_rows
		run.valid_rows = valid_row_count
		run.invalid_rows = total_rows - valid_row_count
		for key, value in stats.items():
			run.set(key, value)
		all_errors = [*validation_errors, *import_errors]
		run.error_summary = "\n".join(all_errors[:MAX_ERRORS])
		run.status = "Completed With Errors" if all_errors else "Completed"
		run.completed_on = frappe.utils.now_datetime()
		run.save(ignore_permissions=True)
		frappe.db.commit()

		if stats["candidate_names_added"]:
			try:
				from lead_outreach_manager.services.candidate_classification import enqueue_post_import_classification

				enqueue_post_import_classification(import_run=run.name)
			except Exception:
				frappe.log_error(title="Post-CSV classification enqueue failed", message=frappe.get_traceback())

		notify(run)
		return {**stats, "status": run.status, "invalid_rows": run.invalid_rows}
	except Exception:
		run.reload()
		run.status = "Failed"
		run.completed_on = frappe.utils.now_datetime()
		run.error_summary = "Import failed. Check Error Log for technical details."
		run.save(ignore_permissions=True)
		frappe.log_error(title=f"Lead CSV Import failed: {run.name}", message=frappe.get_traceback())
		frappe.db.commit()
		notify(run)
		raise


def read_and_validate(run):
	content = get_attached_csv_content(run)
	reader = csv.DictReader(io.StringIO(content))
	headers = {normalize_header(header) for header in (reader.fieldnames or []) if clean(header)}
	header_errors = validate_headers(headers, discovery_enabled=bool(run.run_discovery_on_import))
	if header_errors:
		return [], header_errors, 0, 0

	valid_rows = []
	valid_row_count = 0
	errors = []
	total_rows = 0
	limit = frappe.utils.cint(run.limit_rows) or None
	for row_number, raw in enumerate(reader, start=2):
		total_rows += 1
		item, row_errors = normalize_row(
			raw, row_number, run.country, discovery_enabled=bool(run.run_discovery_on_import)
		)
		if row_errors:
			errors.extend(row_errors)
		else:
			valid_row_count += 1
			if not limit or len(valid_rows) < limit:
				valid_rows.append(item)
	return valid_rows, errors, total_rows, valid_row_count


def validate_headers(headers, discovery_enabled=True):
	errors = []
	if not any(column in headers for column in COMPANY_COLUMNS):
		errors.append("Missing company column: use company_name or entity_name.")
	if not discovery_enabled and "domain" not in headers:
		errors.append("Missing required column: domain.")
	if not discovery_enabled and not any(column in headers for column in CANDIDATE_COLUMNS):
		errors.append("Missing candidate column: use candidate_name, representative_name, or name_text.")
	return errors


def normalize_row(raw, row_number, default_country, discovery_enabled=True):
	row = {normalize_header(key): clean(value) for key, value in raw.items() if clean(key)}
	company_name = first_value(row, COMPANY_COLUMNS)
	candidate_name = first_value(row, CANDIDATE_COLUMNS)
	domain = normalize_domain(row.get("domain"))
	country = normalize_country(row.get("country") or default_country)
	errors = []
	if not company_name:
		errors.append(f"Row {row_number}: missing company name")
	if not domain and not discovery_enabled:
		errors.append(f"Row {row_number}: missing or invalid domain")
	if not candidate_name and not discovery_enabled:
		errors.append(f"Row {row_number}: missing candidate name")
	if not country:
		errors.append(f"Row {row_number}: country must be Singapore or Vietnam")

	emails = split_emails(row.get("email"))
	if row.get("email") and not emails:
		errors.append(f"Row {row_number}: email contains no valid address")
	return {
		"row_number": row_number,
		"country": country,
		"company_name": truncate(company_name),
		"candidate_name": truncate(candidate_name),
		"candidates": ([{"name_text": truncate(candidate_name), "title_text": truncate(first_value(row, TITLE_COLUMNS)), "source_url": truncate(row.get("source_url"))}] if candidate_name else []),
		"domain": truncate(domain),
		"uen": truncate(row.get("uen")),
		"source_id": first_value(row, SOURCE_ID_COLUMNS),
		"website": truncate(row.get("website")),
		"emails": emails,
		"phone": truncate(row.get("phone")),
		"title": truncate(first_value(row, TITLE_COLUMNS)),
		"industry_tier": truncate(row.get("industry_tier")),
		"priority_tier": truncate(row.get("priority_tier")),
		"postal_code": truncate(row.get("postal_code")),
		"source_url": truncate(row.get("source_url")),
		"contact_points": [
			*[
				{"contact_type": "Email", "value": email, "is_generic": 0, "confidence": 1, "source_url": truncate(row.get("source_url"))}
				for email in emails
			],
			*([
				{"contact_type": "Phone", "value": truncate(row.get("phone")), "is_generic": 0, "confidence": 1, "source_url": truncate(row.get("source_url"))}
			] if truncate(row.get("phone")) else []),
		],
		"domain_status": "verified" if domain else "pending_domain_discovery",
		"crawl_status": "pending",
		"domain_match_score": None,
	}, errors


def apply_discovery(item, result, stats):
	had_domain = bool(item.get("domain"))
	if result.domain:
		item["domain"] = truncate(result.domain)
		item["website"] = truncate(result.website) or item.get("website")
	item["domain_status"] = result.domain_status
	item["crawl_status"] = result.crawl_status
	item["domain_match_score"] = result.domain_match_score
	if result.domain_status == "verified" and not had_domain:
		stats["domains_discovered"] += 1
	if result.crawl_status == "crawled":
		stats["profiles_crawled"] += 1
	else:
		stats["discovery_failed"] += 1

	existing_contacts = {(point["contact_type"], point["value"]) for point in item["contact_points"]}
	for point in result.contact_points:
		key = (point["contact_type"], point["value"])
		if key not in existing_contacts:
			item["contact_points"].append(point)
			existing_contacts.add(key)
			stats["contacts_discovered"] += 1

	existing_names = {candidate["name_text"] for candidate in item["candidates"]}
	for candidate in result.candidates:
		if candidate["name_text"] not in existing_names:
			item["candidates"].append(candidate)
			existing_names.add(candidate["name_text"])
			stats["candidates_discovered"] += 1


def import_row(item, stats, import_run=None):
	profile_key = get_profile_key(item)
	existing = frappe.db.exists("Company Profile", profile_key)
	profile = frappe.get_doc("Company Profile", existing) if existing else frappe.new_doc("Company Profile")
	if not existing:
		profile.uen = profile_key
	profile.entity_name = item["company_name"]
	profile.country = item["country"]
	profile.domain = item["domain"]
	profile.domain_status = item["domain_status"]
	profile.crawl_status = item["crawl_status"]
	profile.domain_match_score = item["domain_match_score"]
	profile.pipeline_stage = "discovered_csv" if item["crawl_status"] == "crawled" else "csv_imported"
	if item["website"]:
		profile.website = item["website"]
	if item["industry_tier"]:
		profile.industry_tier = item["industry_tier"]
	if item["priority_tier"]:
		profile.priority_tier = item["priority_tier"]
	if item["postal_code"]:
		profile.postal_code = item["postal_code"]
	if str(item["source_id"] or "").isdigit():
		profile.source_company_id = int(item["source_id"])

	merge_contact_points(profile, item)
	profile.last_imported_on = frappe.utils.now_datetime()
	profile.save(ignore_permissions=True)
	stats["profiles_updated" if existing else "profiles_created"] += 1

	existing_names = {candidate.name_text for candidate in profile.candidate_names}
	for candidate in item["candidates"]:
		if candidate["name_text"] in existing_names:
			continue
		profile.append("candidate_names", {
			"name_text": candidate["name_text"],
			"title_text": candidate.get("title_text"),
			"source_url": candidate.get("source_url"),
			"extraction_method": "desk_discovery_csv",
			"discovered_at": frappe.utils.now_datetime(),
		})
		existing_names.add(candidate["name_text"])
		stats["candidate_names_added"] += 1
	profile.save(ignore_permissions=True)

	if profile.primary_email or profile.primary_phone:
		from lead_outreach_manager.services.contacts import find_linked_contact, get_or_create_generic_contact

		had_contact = bool(find_linked_contact(profile.name))
		get_or_create_generic_contact(profile)
		stats["contacts_updated" if had_contact else "contacts_created"] += 1

	if import_run:
		ensure_batch_membership(import_run, profile.name, item.get("row_number"))


def ensure_batch_membership(import_run, company_profile, source_row_number=None):
	"""Record immutable many-to-many batch membership without duplicating rows."""
	identity = f"{import_run}|{company_profile}"
	member_name = f"LBM-{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:20].upper()}"
	existing = frappe.db.get_value(
		"Lead Import Batch Member",
		{"import_run": import_run, "company_profile": company_profile},
		"name",
	)
	if existing:
		return existing
	member = frappe.get_doc({
		"doctype": "Lead Import Batch Member",
		"membership_key": member_name,
		"import_run": import_run,
		"company_profile": company_profile,
		"source_row_number": source_row_number,
		"imported_on": frappe.utils.now_datetime(),
	})
	member.insert(ignore_permissions=True)
	return member.name


def merge_contact_points(profile, item):
	existing = {(row.contact_type, row.value) for row in profile.contact_points}
	for point in item["contact_points"]:
		key = (point["contact_type"], truncate(point["value"]))
		if key not in existing:
			profile.append("contact_points", {**point, "value": key[1], "source_url": truncate(point.get("source_url"))})
			existing.add(key)
	emails = [point["value"] for point in item["contact_points"] if point["contact_type"] == "Email"]
	phones = [point["value"] for point in item["contact_points"] if point["contact_type"] == "Phone"]
	if emails:
		profile.primary_email = emails[0]
		profile.has_email_contact = 1
		profile.primary_contact_type = "Email"
		profile.contact_status = "has_generic_contact"
	if phones:
		profile.primary_phone = phones[0]
	if not emails and phones:
		profile.primary_contact_type = profile.primary_contact_type or "Phone"
		profile.contact_status = profile.contact_status or "has_contact"


def get_profile_key(item):
	if item["uen"]:
		return item["uen"]
	if item["country"] == "Vietnam" and str(item["source_id"] or "").isdigit():
		return f"VN-{item['source_id']}"
	prefix = "SG" if item["country"] == "Singapore" else "VN"
	# Domain is deliberately excluded: discovery may fail on one run and
	# succeed on the next, but both runs must resolve to the same profile.
	identity = f"{item['country']}|{item['source_id'] or ''}|{item['company_name'].lower()}"
	digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16].upper()
	return f"{prefix}-CSV-{digest}"


def get_attached_csv_content(run):
	if not run.csv_file or not run.csv_file.lower().split("?", 1)[0].endswith(".csv"):
		frappe.throw("Attach a .csv file before validating.")
	file_name = frappe.db.get_value("File", {"file_url": run.csv_file}, "name")
	if not file_name:
		frappe.throw("Attached CSV File record was not found.")
	content = frappe.get_doc("File", file_name).get_content()
	if isinstance(content, bytes):
		content = content.decode("utf-8-sig")
	else:
		content = content.lstrip("\ufeff")
	return content


def split_emails(value):
	emails = []
	seen = set()
	for part in re.split(r"[;,]", value or ""):
		email = clean(part)
		if email and email not in seen and frappe.utils.validate_email_address(email, throw=False):
			seen.add(email)
			emails.append(email)
	return emails


def normalize_domain(value):
	value = clean(value).lower()
	if not value:
		return None
	parsed = urlparse(value if "://" in value else f"//{value}")
	domain = (parsed.hostname or "").strip(".")
	return domain if "." in domain and " " not in domain else None


def normalize_country(value):
	value = clean(value).lower()
	if value in ("singapore", "sg"):
		return "Singapore"
	if value in ("vietnam", "viet nam", "vn"):
		return "Vietnam"
	return None


def first_value(row, columns):
	return next((row.get(column) for column in columns if row.get(column)), None)


def clean(value):
	return str(value or "").strip()


def normalize_header(value):
	"""Accept friendly CSV headers such as 'Company name' and 'Postal Code'."""
	return re.sub(r"[^a-z0-9]+", "_", clean(value).lower()).strip("_")


def truncate(value):
	return clean(value)[:DATA_MAX_LENGTH] or None


def notify(run):
	frappe.publish_realtime(IMPORT_UPDATE_EVENT, {"run_name": run.name, "status": run.status}, doctype=run.doctype, docname=run.name)
