# Copyright (c) 2026, Kien Phi and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class OutreachBatch(Document):
	pass


@frappe.whitelist()
def queue_batch_scheduling(batch_name):
	from lead_outreach_manager.services.outreach_batches import queue_batch_scheduling as queue

	return queue(batch_name)
