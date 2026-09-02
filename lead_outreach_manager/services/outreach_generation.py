"""Bulk outreach-email generation for auto-confirmed candidates. Every name
the classification funnel (services/candidate_classification.py) auto-
confirmed already has a personalized Contact ready to go — this is the bulk
equivalent of clicking "Generate Outreach Email" on each of those Contacts
one at a time.

Mirrors outreach_batches.py's job pattern (status-tracked doc ->
frappe.enqueue(queue="long") -> per-target try/except accounting ->
frappe.publish_realtime on completion -> .js realtime listener refreshes the
open form). Re-runnable by design: a contact that already has an Outreach
Email record (any status) is never targeted again, so running this twice
never creates duplicate drafts.
"""
from __future__ import annotations

QUEUEABLE_STATUSES = ("Draft",)
GENERATION_RUN_UPDATE_EVENT = "outreach_generation_update"
COMMIT_EVERY = 100
ERROR_SUMMARY_MAX_ENTRIES = 20


def enqueue_for_auto_confirmed(
	limit_rows=None, email_template=None, country=None, industry_tier=None, company_name=None, import_run=None,
):
	"""bench execute lead_outreach_manager.services.outreach_generation.enqueue_for_auto_confirmed
	[--kwargs "{'limit_rows': 20}"] — creates and queues a run. `email_template`
	overrides Outreach Settings' default for this run only."""
	frappe = get_frappe()
	run = frappe.new_doc("Outreach Generation Run")
	run.limit_rows = frappe.utils.cint(limit_rows) or None
	run.email_template = email_template or None
	run.country = country or None
	run.industry_tier = industry_tier or None
	run.company_name = company_name or None
	run.import_run = import_run or None
	run.insert(ignore_permissions=True)
	queue_generation_run(run.name)
	return run.name


def queue_generation_run(run_name):
	frappe = get_frappe()
	run = frappe.get_doc("Outreach Generation Run", run_name)

	if run.status not in QUEUEABLE_STATUSES:
		frappe.throw(f"Only a Draft Outreach Generation Run can be queued (current status: {run.status}).")

	run.status = "Queued"
	run.save(ignore_permissions=True)
	frappe.db.commit()

	frappe.enqueue(
		run_background_generation,
		queue="long",
		timeout=6000,
		job_id=f"outreach-generation-{run.name}",
		deduplicate=True,
		outreach_generation_run_name=run.name,
	)

	return {"status": run.status, "queued": True}


def run_background_generation(outreach_generation_run_name):
	frappe = get_frappe()
	from lead_outreach_manager.services.outreach_emails import create_outreach_email

	run = frappe.get_doc("Outreach Generation Run", outreach_generation_run_name)
	run.status = "Generating"
	run.started_on = frappe.utils.now_datetime()
	run.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		targets = _get_auto_confirmed_targets(
			limit_rows=frappe.utils.cint(run.limit_rows) or None,
			country=run.country,
			industry_tier=run.industry_tier,
			company_name=run.company_name,
			import_run=run.import_run,
		)
		run.total_targets = len(targets)

		generated_count = 0
		skipped_count = 0
		error_count = 0
		errors = []

		for index, target in enumerate(targets):
			try:
				create_outreach_email(
					target["company_profile"],
					target["contact"],
					email_template=run.email_template,
					import_run=run.import_run,
				)
				generated_count += 1
			except frappe.ValidationError as exc:
				# do-not-contact / unsubscribed / no-email / no-template gate
				# tripped — skip this contact, don't fail the whole run.
				skipped_count += 1
				errors.append(f"{target['contact']} skipped: {exc}")
			except Exception:
				error_count += 1
				errors.append(f"{target['contact']}: {frappe.get_traceback()}")

			if (index + 1) % COMMIT_EVERY == 0:
				frappe.db.commit()

		run.generated_count = generated_count
		run.skipped_count = skipped_count
		run.error_count = error_count
		run.error_summary = "\n".join(errors[:ERROR_SUMMARY_MAX_ENTRIES])
		run.completed_on = frappe.utils.now_datetime()
		run.status = "Completed With Errors" if error_count else "Completed"
		run.save(ignore_permissions=True)
		frappe.db.commit()

	except Exception:
		run.status = "Failed"
		run.completed_on = frappe.utils.now_datetime()
		run.error_summary = "Generation run failed. Check Error Log for technical details."
		run.save(ignore_permissions=True)
		frappe.log_error(title=f"Outreach Generation Run failed: {run.name}", message=frappe.get_traceback())
		frappe.db.commit()
		notify_generation_run_update(run)
		raise

	notify_generation_run_update(run)
	return {"generated": generated_count, "skipped": skipped_count, "errors": error_count}


def _get_auto_confirmed_targets(
	limit_rows=None, country=None, industry_tier=None, company_name=None, import_run=None,
):
	"""Every confirmed row from the auto passes (LLM or Email Match — either
	way classification_status ends up Auto Confirmed) that doesn't already
	have an Outreach Email for that (company_profile, contact) pair. Human
	confirms are intentionally excluded — the human already reviewed that
	one individually and can generate for it the same way."""
	frappe = get_frappe()
	conditions = [
		"ccn.parenttype = 'Company Profile'",
		"ccn.confirmed = 1",
		"ccn.classification_status = 'Auto Confirmed'",
		"ccn.contact IS NOT NULL AND ccn.contact != ''",
		"IFNULL(cp.has_email_contact, 0) = 1",
		"IFNULL(cp.do_not_contact, 0) = 0",
	]
	values = {}
	if import_run:
		conditions.append("""
			EXISTS (
				SELECT 1 FROM `tabLead Import Batch Member` lbm
				WHERE lbm.import_run = %(import_run)s AND lbm.company_profile = ccn.parent
			)
		""")
		values["import_run"] = import_run
	if country:
		conditions.append("cp.country = %(country)s")
		values["country"] = country
	if industry_tier:
		conditions.append("cp.industry_tier = %(industry_tier)s")
		values["industry_tier"] = industry_tier
	if company_name:
		conditions.append("cp.entity_name LIKE %(company_name)s")
		values["company_name"] = f"%{company_name.strip()}%"

	sql = f"""
		SELECT DISTINCT ccn.parent AS company_profile, ccn.contact
		FROM `tabCompany Candidate Name` ccn
		JOIN `tabCompany Profile` cp ON cp.name = ccn.parent
		WHERE {" AND ".join(conditions)}
		  AND NOT EXISTS (
		      SELECT 1 FROM `tabOutreach Email` oe
		      WHERE oe.company_profile = ccn.parent AND oe.contact = ccn.contact
		  )
		ORDER BY ccn.parent
	"""
	if limit_rows:
		sql += f" LIMIT {int(limit_rows)}"
	return frappe.db.sql(sql, values, as_dict=True)


def notify_generation_run_update(run):
	frappe = get_frappe()
	frappe.publish_realtime(
		GENERATION_RUN_UPDATE_EVENT,
		{"run_name": run.name, "status": run.status},
		doctype="Outreach Generation Run",
		docname=run.name,
	)


def get_frappe():
	import frappe

	return frappe
