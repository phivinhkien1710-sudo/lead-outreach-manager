import frappe
from frappe.model.document import Document


class LeadCSVImportRun(Document):
	pass


@frappe.whitelist()
def validate_csv(run_name):
	from lead_outreach_manager.services.csv_imports import validate_import_run

	return validate_import_run(run_name)


@frappe.whitelist()
def queue_import(run_name):
	from lead_outreach_manager.services.csv_imports import queue_import_run

	return queue_import_run(run_name)


@frappe.whitelist()
def download_template():
	content = (
		"country,uen,source_id,company_name,postal_code,domain,candidate_name,position,email,phone,website,"
		"industry_tier,priority_tier,source_url\n"
		"Singapore,201234567A,,Example Manufacturing Pte Ltd,049315,example.com,Jane Tan,Director,"
		"jane@example.com,+6561234567,https://example.com,Industrial,A,https://example.com/about\n"
	)
	frappe.local.response.filename = "lead_outreach_process_ready_template.csv"
	frappe.local.response.filecontent = content
	frappe.local.response.type = "download"
