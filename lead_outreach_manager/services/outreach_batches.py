"""Self-imposed rate limiting for bulk outreach scheduling. Email Queue
itself has no per-hour send cap, so this staggers each targeted Outreach
Email's own `send_after` far enough apart to respect
Outreach Batch.rate_limit_per_hour, optionally confined to business hours —
mirrors receivable_risk_manager's Receivables Import Job pattern (status-
tracked job doc -> frappe.enqueue(queue="long") -> frappe.publish_realtime
on completion -> .js realtime listener refreshes the open form).
"""
from __future__ import annotations

from datetime import timedelta

QUEUEABLE_STATUSES = ("Draft",)
OUTREACH_BATCH_UPDATE_EVENT = "outreach_batch_update"
COMMIT_EVERY = 100


def compute_staggered_send_after(
	index, start_after, rate_limit_per_hour, *, business_hours_only=True, business_hours_start=9, business_hours_end=18,
):
	"""Pure function, deliberately zero Frappe dependency so it's directly
	unit-testable. `start_after` and the return value are naive datetimes."""
	spacing_seconds = 3600 / rate_limit_per_hour
	candidate = start_after + timedelta(seconds=spacing_seconds * index)

	if not business_hours_only:
		return candidate
	return _roll_into_business_hours(candidate, business_hours_start, business_hours_end)


def _roll_into_business_hours(dt, start_hour, end_hour):
	if dt.hour < start_hour:
		return dt.replace(hour=start_hour, minute=0, second=0, microsecond=0)
	if dt.hour >= end_hour:
		next_day = dt + timedelta(days=1)
		return next_day.replace(hour=start_hour, minute=0, second=0, microsecond=0)
	return dt


def queue_batch_scheduling(batch_name):
	frappe = get_frappe()
	batch = frappe.get_doc("Outreach Batch", batch_name)

	if batch.status not in QUEUEABLE_STATUSES:
		frappe.throw(f"Only a Draft Outreach Batch can be queued (current status: {batch.status}).")

	batch.status = "Queued"
	batch.save(ignore_permissions=True)
	frappe.db.commit()

	frappe.enqueue(
		run_background_batch_scheduling,
		queue="long",
		timeout=6000,
		job_id=f"outreach-batch-{batch.name}",
		deduplicate=True,
		outreach_batch_name=batch.name,
	)

	return {"status": batch.status, "queued": True}


def run_background_batch_scheduling(outreach_batch_name):
	frappe = get_frappe()
	from lead_outreach_manager.services.outreach_emails import schedule_send

	batch = frappe.get_doc("Outreach Batch", outreach_batch_name)
	batch.status = "Scheduling"
	batch.started_on = frappe.utils.now_datetime()
	batch.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		targets = _get_eligible_targets(batch)
		batch.total_targets = len(targets)

		scheduled_count = 0
		skipped_count = 0
		error_count = 0
		errors = []
		start_after = frappe.utils.get_datetime(batch.start_after)

		for index, outreach_name in enumerate(targets):
			send_after = compute_staggered_send_after(
				index,
				start_after,
				batch.rate_limit_per_hour,
				business_hours_only=bool(batch.business_hours_only),
				business_hours_start=batch.business_hours_start,
				business_hours_end=batch.business_hours_end,
			)
			try:
				schedule_send(outreach_name, send_after=send_after)
				scheduled_count += 1
			except frappe.ValidationError:
				# do-not-contact / unsubscribed / no-email gate tripped — skip, don't fail the batch
				skipped_count += 1
			except Exception:
				error_count += 1
				errors.append(f"{outreach_name}: {frappe.get_traceback()}")

			if (index + 1) % COMMIT_EVERY == 0:
				frappe.db.commit()

		batch.scheduled_count = scheduled_count
		batch.skipped_count = skipped_count
		batch.error_count = error_count
		batch.error_summary = "\n".join(errors[:20])
		batch.completed_on = frappe.utils.now_datetime()
		batch.status = "Completed With Errors" if error_count else "Completed"
		batch.save(ignore_permissions=True)
		frappe.db.commit()

	except Exception:
		batch.status = "Failed"
		batch.completed_on = frappe.utils.now_datetime()
		batch.error_summary = "Batch scheduling failed. Check Error Log for technical details."
		batch.save(ignore_permissions=True)
		frappe.log_error(title=f"Outreach Batch failed: {batch.name}", message=frappe.get_traceback())
		frappe.db.commit()
		notify_outreach_batch_update(batch)
		raise

	notify_outreach_batch_update(batch)
	return {"scheduled": scheduled_count, "skipped": skipped_count, "errors": error_count}


def _get_eligible_targets(batch):
	frappe = get_frappe()

	conditions = ["oe.status = 'Ready to Send'"]
	values = {}
	if batch.filter_company_industry_tier:
		conditions.append("cp.industry_tier = %(industry_tier)s")
		values["industry_tier"] = batch.filter_company_industry_tier
	if batch.filter_priority_tier:
		conditions.append("cp.priority_tier = %(priority_tier)s")
		values["priority_tier"] = batch.filter_priority_tier

	rows = frappe.db.sql(
		f"""
		SELECT oe.name
		FROM `tabOutreach Email` oe
		JOIN `tabCompany Profile` cp ON cp.name = oe.company_profile
		WHERE {" AND ".join(conditions)}
		ORDER BY oe.creation
		""",
		values,
		as_dict=True,
	)
	return [row.name for row in rows]


def notify_outreach_batch_update(batch):
	frappe = get_frappe()
	frappe.publish_realtime(
		OUTREACH_BATCH_UPDATE_EVENT,
		{"batch_name": batch.name, "status": batch.status},
		doctype="Outreach Batch",
		docname=batch.name,
	)


def get_frappe():
	import frappe

	return frappe
