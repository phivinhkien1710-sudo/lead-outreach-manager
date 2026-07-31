# Copyright (c) 2026, Kien Phi and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class OutreachGenerationRun(Document):
	pass


@frappe.whitelist()
def queue_generation_run(run_name):
	from lead_outreach_manager.services.outreach_generation import queue_generation_run as queue

	return queue(run_name)
