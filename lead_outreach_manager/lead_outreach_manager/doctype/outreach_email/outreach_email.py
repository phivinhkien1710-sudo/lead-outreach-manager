# Copyright (c) 2026, Kien Phi and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class OutreachEmail(Document):
	pass


@frappe.whitelist()
def create_outreach_email(company_profile, contact, email_template=None):
	from lead_outreach_manager.services.outreach_emails import create_outreach_email as create

	return create(company_profile, contact, email_template)


@frappe.whitelist()
def approve_outreach_email(outreach_email_name):
	from lead_outreach_manager.services.outreach_emails import approve_outreach_email as approve

	return approve(outreach_email_name)


@frappe.whitelist()
def schedule_send(outreach_email_name, send_after=None):
	from lead_outreach_manager.services.outreach_emails import schedule_send as do_schedule_send

	return do_schedule_send(outreach_email_name, send_after)
