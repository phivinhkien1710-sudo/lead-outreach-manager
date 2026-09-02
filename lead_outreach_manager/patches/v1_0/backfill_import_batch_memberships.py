"""Build immutable batch membership for CSV imports completed before batch scoping existed."""

import frappe


def execute():
	from lead_outreach_manager.services.csv_imports import (
		ensure_batch_membership,
		get_profile_key,
		read_and_validate,
	)

	runs = frappe.get_all(
		"Lead CSV Import Run",
		filters={"status": ["in", ["Completed", "Completed With Errors"]]},
		pluck="name",
	)
	for run_name in runs:
		try:
			run = frappe.get_doc("Lead CSV Import Run", run_name)
			rows, _errors, _total, _valid = read_and_validate(run)
			for item in rows:
				profile_name = frappe.db.exists("Company Profile", get_profile_key(item))
				if profile_name:
					ensure_batch_membership(run.name, profile_name, item.get("row_number"))
			frappe.db.set_value(
				"Lead CSV Import Run",
				run.name,
				"batch_companies",
				frappe.db.count("Lead Import Batch Member", {"import_run": run.name}),
				update_modified=False,
			)
		except Exception:
			# An old attachment may have been removed; one unavailable historical
			# CSV must not block schema migration for current and future batches.
			frappe.log_error(
				title=f"Batch membership backfill skipped: {run_name}",
				message=frappe.get_traceback(),
			)
	frappe.db.commit()
