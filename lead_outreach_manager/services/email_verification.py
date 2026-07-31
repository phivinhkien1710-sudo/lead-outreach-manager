"""Deliverability check for guessed emails on already-confirmed Company
Candidate Name rows, and promotion of a verified guess to the outreach
recipient.

Company Candidate Name rows already carry up to 6 unverified guesses
(guessed_email_1-6, see services/email_guessing.py) once a candidate is
confirmed (Human or the automatic funnel in candidate_classification.py).
Nothing has ever confirmed whether a guess is actually real, and this app's
core safety rule is that an unverified guess is never auto-applied as the
outreach recipient. This module extends that trust the same way Pass B
(scraped-email corroboration) does — just backed by a real deliverability
check (the MillionVerifier API) instead of a local name/local-part match.

For each confirmed row with at least one guess, guessed_email_1 is tried
first, then _2, and so on through _6, stopping at the first result the
provider reports as "ok" (deliverable) -> the row's Contact is promoted to
that address via email_guessing.apply_top_guess_to_contact, the same
primitive the human Confirm Name path already uses. A "catch_all" result
(the domain accepts everything) does NOT confirm this specific address, so it is never
auto-promoted — it lands in the Email Verification Review Queue report for a
human to promote manually via the "Use This Email" button.

Runs are tracked by the Email Verification Run doctype and executed on the
long queue — mirrors candidate_classification.py's job pattern (status-tracked
doc -> frappe.enqueue -> per-item accounting -> publish_realtime). Re-runnable
by design: only confirmed rows with a guess whose verification_status is
empty or Error are ever selected.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace

import requests

VERIFICATION_RUN_UPDATE_EVENT = "email_verification_update"
QUEUEABLE_STATUSES = ("Draft",)
MAX_CONSECUTIVE_CHUNK_FAILURES = 3
ERROR_SUMMARY_MAX_ENTRIES = 20
MILLIONVERIFIER_ENDPOINT = "https://api.millionverifier.com/api/v3/"

# Rows within a chunk are verified concurrently (each row's own guess_1..6
# attempts stay sequential/early-stopping within its worker thread — only the
# network-bound wait is parallelized across rows). 8 concurrent requests
# tripped MillionVerifier's connection handling in practice (SSL EOF errors,
# resets, timeouts on EVR-00017) — 4 is a safer ceiling.
MAX_CONCURRENT_ROWS = 4

GUESS_FIELDS = tuple(f"guessed_email_{i}" for i in range(1, 7))

# MillionVerifier `result` values -> our banded bucket. Anything not listed
# here (a provider-side result value we don't recognize) is treated as
# "catch_all" by the caller — conservative: don't trust it enough to
# auto-promote, but don't discard the lead either.
RESULT_BUCKETS = {
	"ok": "verified",
	"catch_all": "catch_all",
	"unknown": "catch_all",
	"unverified": "catch_all",
	"invalid": "not_deliverable",
	"disposable": "not_deliverable",
}

# Preference order when no guess for a row verifies — most promising lead
# first, so a human reviewing the row sees the best available result.
BUCKET_PREFERENCE = ("catch_all", "not_deliverable")

BUCKET_TO_STATUS = {
	"verified": "Verified",
	"catch_all": "Catch-All",
	"not_deliverable": "Not Deliverable",
}

COUNTER_FIELDS = (
	"total_rows",
	"verified_ok",
	"catch_all",
	"not_deliverable",
	"error_rows",
	"credits_used",
	"chunks_total",
	"chunks_failed",
)


class VerificationParseError(Exception):
	pass


class VerificationAPIError(Exception):
	pass


# ---------------------------------------------------------------------------
# Pure functions — deliberately zero Frappe dependency so they're directly
# unit-testable (same convention as candidate_classification.py).
# ---------------------------------------------------------------------------


def band_verification_result(result: str) -> str | None:
	return RESULT_BUCKETS.get((result or "").strip().lower())


def parse_verification_response(payload) -> dict:
	"""Validates a MillionVerifier JSON response. `error` set means the
	provider itself rejected the call (bad API key, no credits left, etc) —
	distinct from a normal verification result, so it raises the API-error
	type rather than the parse-error type."""
	if not isinstance(payload, dict):
		raise VerificationParseError("verification API response is not a JSON object")
	if payload.get("error"):
		raise VerificationAPIError(f"verification API reported an error: {payload['error']}")
	if not payload.get("result"):
		raise VerificationParseError("verification API response has no 'result' field")
	return payload


def pick_best_attempt(attempts: list) -> dict | None:
	"""attempts: [{"field", "email", "result", "bucket"}, ...] in the order
	they were tried. Returns the first "verified" attempt if any; otherwise
	the most promising per BUCKET_PREFERENCE; None if attempts is empty."""
	if not attempts:
		return None
	for attempt in attempts:
		if attempt["bucket"] == "verified":
			return attempt
	return min(
		attempts,
		key=lambda a: BUCKET_PREFERENCE.index(a["bucket"]) if a["bucket"] in BUCKET_PREFERENCE else len(BUCKET_PREFERENCE),
	)


def chunked(seq, size):
	from lead_outreach_manager.services.candidate_classification import chunked as _chunked

	return _chunked(seq, size)


# ---------------------------------------------------------------------------
# HTTP boundary — the one function integration tests monkeypatch.
# ---------------------------------------------------------------------------


def call_verification_api(email, *, api_key, timeout_seconds) -> dict:
	try:
		response = requests.get(
			MILLIONVERIFIER_ENDPOINT,
			params={"api": api_key, "email": email, "timeout": timeout_seconds},
			timeout=timeout_seconds + 5,
		)
	except requests.exceptions.Timeout as exc:
		raise VerificationAPIError(f"verification API timed out after {timeout_seconds}s") from exc
	except requests.exceptions.RequestException as exc:
		raise VerificationAPIError(f"verification API request failed: {exc}") from exc

	if response.status_code != 200:
		raise VerificationAPIError(
			f"verification API returned HTTP {response.status_code}: {response.text[:500]}"
		)
	try:
		payload = response.json()
	except ValueError as exc:
		raise VerificationParseError(f"verification API response is not JSON: {exc}") from exc
	return parse_verification_response(payload)


def verify_candidate_row(row, settings) -> dict:
	"""`row` needs guessed_email_1 through _6 (a frappe.db.sql as_dict row or plain
	dict). Tries each populated guess in order, stopping at the first "ok".
	Propagates VerificationAPIError from call_verification_api unchanged —
	the caller decides how to record a failed row; a partial set of attempts
	made before the failure is intentionally discarded rather than recorded,
	so a retried row starts clean. Returns {"attempts", "winner", "credits_used"}."""
	attempts = []
	credits_used = 0
	for field in GUESS_FIELDS:
		email = row.get(field)
		if not email:
			continue
		payload = call_verification_api(email, api_key=settings.api_key, timeout_seconds=settings.timeout)
		credits_used += 1
		bucket = band_verification_result(payload.get("result")) or "catch_all"
		attempts.append({"field": field, "email": email, "result": payload.get("result"), "bucket": bucket})
		if bucket == "verified":
			break
	return {"attempts": attempts, "winner": pick_best_attempt(attempts), "credits_used": credits_used}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def get_verification_settings() -> SimpleNamespace:
	frappe = get_frappe()
	settings = frappe.get_single("Outreach Settings")
	return SimpleNamespace(
		auto_verify_after_classification=bool(settings.get("auto_verify_after_classification")),
		api_key=settings.get_password("email_verification_api_key", raise_exception=False) or "",
		timeout=frappe.utils.cint(settings.get("email_verification_timeout")) or 20,
		chunk_size=frappe.utils.cint(settings.get("email_verification_chunk_size")) or 50,
	)


def enqueue_backfill(limit_rows=None):
	"""bench execute lead_outreach_manager.services.email_verification.enqueue_backfill
	[--kwargs "{'limit_rows': 50}"] — always allowed regardless of the
	auto_verify_after_classification setting (an explicit human action).
	Re-runnable at will; each run only picks up confirmed rows with a guess
	whose verification_status is empty or Error."""
	frappe = get_frappe()
	limit_rows = frappe.utils.cint(limit_rows) or None
	return create_and_queue_run("Backfill", limit_rows=limit_rows)


def enqueue_post_classification_verification():
	"""Called at the end of a classification run that produced new confirmed
	candidates — no-op unless the operator opted in via Outreach Settings."""
	if not get_verification_settings().auto_verify_after_classification:
		return None
	return create_and_queue_run("Post Classification")


def create_and_queue_run(trigger, limit_rows=None):
	frappe = get_frappe()
	run = frappe.new_doc("Email Verification Run")
	run.run_trigger = trigger
	run.limit_rows = limit_rows
	run.insert(ignore_permissions=True)
	queue_verification_run(run.name)
	return run.name


def queue_verification_run(run_name):
	frappe = get_frappe()
	run = frappe.get_doc("Email Verification Run", run_name)

	if run.status not in QUEUEABLE_STATUSES:
		frappe.throw(f"Only a Draft Email Verification Run can be queued (current status: {run.status}).")

	run.status = "Queued"
	run.save(ignore_permissions=True)
	frappe.db.commit()

	frappe.enqueue(
		run_background_verification,
		queue="long",
		timeout=21600,
		job_id=f"email-verification-{run.name}",
		deduplicate=True,
		verification_run_name=run.name,
	)

	return {"status": run.status, "queued": True}


def run_background_verification(verification_run_name):
	frappe = get_frappe()
	run = frappe.get_doc("Email Verification Run", verification_run_name)
	settings = get_verification_settings()

	run.status = "Running"
	run.started_on = frappe.utils.now_datetime()
	run.timeout_used = settings.timeout
	run.chunk_size_used = settings.chunk_size
	run.save(ignore_permissions=True)
	frappe.db.commit()

	counts = {field: 0 for field in COUNTER_FIELDS}
	errors = []

	try:
		targets = _get_unverified_rows(limit_rows=frappe.utils.cint(run.limit_rows) or None)
		counts["total_rows"] = len(targets)

		_run_verification_pass(run.name, targets, settings, counts, errors)

		status = "Completed With Errors" if (counts["error_rows"] or counts["chunks_failed"]) else "Completed"
		_finalize_run(run.name, status, counts, errors)

	except Exception:
		_finalize_run(
			run.name, "Failed", counts, ["Email verification run failed. Check Error Log for technical details."]
		)
		frappe.log_error(title=f"Email Verification Run failed: {run.name}", message=frappe.get_traceback())
		raise

	return counts


def _get_unverified_rows(limit_rows=None):
	frappe = get_frappe()
	sql = """
		SELECT ccn.name AS row_name, ccn.parent, ccn.contact,
		       ccn.guessed_email_1, ccn.guessed_email_2, ccn.guessed_email_3,
		       ccn.guessed_email_4, ccn.guessed_email_5, ccn.guessed_email_6
		FROM `tabCompany Candidate Name` ccn
		WHERE ccn.parenttype = 'Company Profile'
		  AND IFNULL(ccn.confirmed, 0) = 1
		  AND IFNULL(ccn.guessed_email_1, '') != ''
		  AND IFNULL(ccn.verification_status, '') IN ('', 'Error')
		ORDER BY ccn.parent, ccn.idx
	"""
	if limit_rows:
		sql += f" LIMIT {int(limit_rows)}"
	return frappe.db.sql(sql, as_dict=True)


def _run_verification_pass(run_name, targets, settings, counts, errors):
	frappe = get_frappe()
	consecutive_failures = 0
	aborted = False

	for chunk in chunked(targets, settings.chunk_size):
		if aborted:
			break
		counts["chunks_total"] += 1
		chunk_failed = False

		# verify_candidate_row is frappe-independent (pure HTTP + stdlib), so
		# it's safe to run concurrently across a chunk's rows — only the
		# outcomes are applied back on the main thread, in original row
		# order, so DB writes and the consecutive-failure count stay
		# single-threaded and deterministic.
		with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_ROWS, len(chunk))) as pool:
			future_by_row_name = {
				pool.submit(verify_candidate_row, target, settings): target["row_name"] for target in chunk
			}
			outcome_by_row_name = {}
			error_by_row_name = {}
			for future in as_completed(future_by_row_name):
				row_name = future_by_row_name[future]
				try:
					outcome_by_row_name[row_name] = future.result()
				except VerificationAPIError as exc:
					error_by_row_name[row_name] = str(exc)

		for target in chunk:
			row_name = target["row_name"]
			if row_name in error_by_row_name:
				chunk_failed = True
				consecutive_failures += 1
				if _set_row_verification(row_name, {"verification_status": "Error"}):
					counts["error_rows"] += 1
				_record_row_error(errors, target, error_by_row_name[row_name])
				if consecutive_failures >= MAX_CONSECUTIVE_CHUNK_FAILURES:
					aborted = True
					break
				continue

			consecutive_failures = 0
			_apply_outcome(target, outcome_by_row_name[row_name], counts, errors)

		if chunk_failed:
			counts["chunks_failed"] += 1

		_update_run_progress(run_name, counts)
		frappe.db.commit()

	if aborted:
		errors.append(
			f"aborted after {consecutive_failures} consecutive verification API failures; "
			"remaining rows stay pending for the next run"
		)


def _apply_outcome(target, outcome, counts, errors):
	frappe = get_frappe()
	counts["credits_used"] += outcome["credits_used"]
	winner = outcome["winner"]
	if winner is None:
		# Shouldn't happen given _get_unverified_rows always selects rows with
		# guessed_email_1 set, but guard rather than crash the run.
		return

	now = frappe.utils.now_datetime()
	if winner["bucket"] == "verified":
		try:
			promoted = _promote_verified_row(target, winner["email"])
		except Exception:
			counts["error_rows"] += 1
			_record_row_error(errors, target, "promotion to Contact failed")
			return
		if promoted and _set_row_verification(
			target["row_name"],
			{
				"verification_status": "Verified",
				"verified_email": winner["email"],
				"verification_result": winner["result"],
				"verified_on": now,
			},
		):
			counts["verified_ok"] += 1
		return

	status_field = "catch_all" if winner["bucket"] == "catch_all" else "not_deliverable"
	if _set_row_verification(
		target["row_name"],
		{
			"verification_status": BUCKET_TO_STATUS[winner["bucket"]],
			"verified_email": winner["email"],
			"verification_result": winner["result"],
			"verified_on": now,
		},
	):
		counts[status_field] += 1


def _promote_verified_row(target, verified_email) -> bool:
	"""Promotes verified_email to the row's linked Contact primary address —
	the same primitive (email_guessing.apply_top_guess_to_contact) the human
	Confirm Name path already uses. Retries once on a concurrent save
	(frappe.TimestampMismatchError), same pattern as
	candidate_classification._auto_confirm_row."""
	frappe = get_frappe()
	from lead_outreach_manager.services.email_guessing import apply_top_guess_to_contact

	if not target.get("contact"):
		return False

	for attempt in (1, 2):
		if not _row_still_pending_verification(target["row_name"]):
			return False
		contact = frappe.get_doc("Contact", target["contact"])
		try:
			apply_top_guess_to_contact(contact, verified_email)
			return True
		except frappe.TimestampMismatchError:
			if attempt == 2:
				raise
	return False


def manually_promote_verified_email(company_profile, row_name):
	"""Whitelisted via Company Profile's use_this_email button — a human
	override for a Catch-All row the automatic pass wouldn't trust on its own
	(same "human can override an uncertain automatic result" pattern already
	used for Auto Rejected candidate names)."""
	frappe = get_frappe()
	from lead_outreach_manager.services.email_guessing import apply_top_guess_to_contact

	row = frappe.db.get_value(
		"Company Candidate Name",
		row_name,
		["contact", "verified_email", "verification_status", "parent"],
		as_dict=True,
	)
	if not row or row.parent != company_profile:
		frappe.throw("Candidate row not found on this Company Profile.")
	if row.verification_status != "Catch-All":
		frappe.throw("Only a Catch-All verification result can be manually promoted.")
	if not row.contact:
		frappe.throw("This row has no linked Contact.")

	contact = frappe.get_doc("Contact", row.contact)
	apply_top_guess_to_contact(contact, row.verified_email)
	frappe.db.set_value(
		"Company Candidate Name", row_name, {"verification_status": "Verified", "verified_on": frappe.utils.now_datetime()}
	)
	frappe.db.commit()
	return {"promoted": True, "email": row.verified_email}


def _row_still_pending_verification(row_name) -> bool:
	"""Guard against a concurrent writer (another verification run, or a
	manual promote) acting on the row between selection and this write."""
	frappe = get_frappe()
	current = frappe.db.get_value(
		"Company Candidate Name", row_name, ["confirmed", "verification_status"], as_dict=True
	)
	if not current:
		return False
	return bool(current.confirmed) and (current.verification_status or "") in ("", "Error")


def _set_row_verification(row_name, values) -> bool:
	frappe = get_frappe()
	if not _row_still_pending_verification(row_name):
		return False
	frappe.db.set_value("Company Candidate Name", row_name, values)
	return True


def _record_row_error(errors, target, reason):
	errors.append(f"{target['row_name']}: {reason}")


def _update_run_progress(run_name, counts):
	frappe = get_frappe()
	frappe.db.set_value("Email Verification Run", run_name, {field: counts[field] for field in COUNTER_FIELDS})


def _finalize_run(run_name, status, counts, errors):
	frappe = get_frappe()
	frappe.db.set_value(
		"Email Verification Run",
		run_name,
		{
			**{field: counts[field] for field in COUNTER_FIELDS},
			"status": status,
			"completed_on": frappe.utils.now_datetime(),
			"error_summary": "\n".join(errors[:ERROR_SUMMARY_MAX_ENTRIES]),
		},
	)
	frappe.db.commit()
	notify_verification_run_update(run_name, status)


def notify_verification_run_update(run_name, status):
	frappe = get_frappe()
	frappe.publish_realtime(
		VERIFICATION_RUN_UPDATE_EVENT,
		{"run_name": run_name, "status": status},
		doctype="Email Verification Run",
		docname=run_name,
	)


def get_frappe():
	import frappe

	return frappe
