# Copyright (c) 2026, Kien Phi and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class EmailVerificationRun(Document):
	pass


@frappe.whitelist()
def queue_verification_run(run_name):
	from lead_outreach_manager.services.email_verification import queue_verification_run as queue

	return queue(run_name)
