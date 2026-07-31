# Copyright (c) 2026, Kien Phi and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CandidateClassificationRun(Document):
	pass


@frappe.whitelist()
def queue_classification_run(run_name):
	from lead_outreach_manager.services.candidate_classification import queue_classification_run as queue

	return queue(run_name)
