import frappe

LOGGER_NAME = "lead_outreach_manager"


def daily_backup_site():
	"""Scheduler entrypoint (see hooks.py). install.sh's Docker deployment already
	gets a database+files backup every 6h for free, via a bundled cron container
	that runs independently of this site's own scheduler (see docs/DEPLOYMENT.md).
	This task exists as a fallback for the OTHER documented production path —
	`bench setup production` (supervisor+nginx) — which has no such external cron
	and would otherwise only get backed up if someone remembered to run
	`bench backup` by hand. Skipped if a backup already exists from within the
	last 6 hours, per new_backup's own default `older_than`, so running both
	mechanisms at once (e.g. during a migration between the two) is harmless."""
	from frappe.utils.backups import new_backup

	logger = frappe.logger(LOGGER_NAME)
	logger.info("Starting scheduled daily site backup")

	backup = new_backup(ignore_files=False)
	summary = backup.get_summary()

	logger.info("Completed scheduled daily site backup: %s", summary)
	return summary


def hourly_reconcile_outreach_email_status():
	"""Scheduler entrypoint (see hooks.py). Email Queue's own flush cron sends
	mail asynchronously and never calls back into Outreach Email, so this
	closes the loop by checking each Scheduled email's Email Queue outcome."""
	from lead_outreach_manager.services.outreach_emails import reconcile_outreach_email_status

	logger = frappe.logger(LOGGER_NAME)
	logger.info("Starting hourly outreach email status reconciliation")

	result = reconcile_outreach_email_status()

	logger.info(
		"Completed outreach email status reconciliation: sent=%s failed=%s unchanged=%s",
		result["sent"],
		result["failed"],
		result["unchanged"],
	)
	return result
