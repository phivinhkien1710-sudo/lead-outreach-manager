# Copyright (c) 2026, Kien Phi and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CompanyProfile(Document):
	pass


@frappe.whitelist()
def confirm_candidate_name(company_profile, row_name):
	from lead_outreach_manager.services.candidate_names import confirm_candidate_name as confirm

	return confirm(company_profile, row_name)


@frappe.whitelist()
def use_this_email(company_profile, row_name):
	from lead_outreach_manager.services.email_verification import manually_promote_verified_email

	return manually_promote_verified_email(company_profile, row_name)
