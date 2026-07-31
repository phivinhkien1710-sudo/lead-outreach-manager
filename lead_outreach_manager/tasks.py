import frappe

LOGGER_NAME = "lead_outreach_manager"


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
