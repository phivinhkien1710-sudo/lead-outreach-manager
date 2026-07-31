"""Browses email deliverability check results for Company Candidate Name
rows. Defaults to the Catch-All queue — results the automatic verification
pass (see services/email_verification.py) couldn't fully trust, since the
domain accepts everything and doesn't actually confirm this one address.
Promotion for a Catch-All row happens via the "Use This Email" button on the
linked Company Profile's candidate_names grid."""

import frappe


def execute(filters=None):
	return get_columns(), get_data(filters or {})


def get_columns():
	return [
		{
			"label": "Company Profile",
			"fieldname": "company_profile",
			"fieldtype": "Link",
			"options": "Company Profile",
			"width": 140,
		},
		{
			"label": "Entity Name",
			"fieldname": "entity_name",
			"fieldtype": "Data",
			"width": 220,
		},
		{
			"label": "Candidate Name",
			"fieldname": "name_text",
			"fieldtype": "Data",
			"width": 180,
		},
		{
			"label": "Verification Status",
			"fieldname": "verification_status",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": "Verified Email",
			"fieldname": "verified_email",
			"fieldtype": "Data",
			"width": 220,
		},
		{
			"label": "Verification Result",
			"fieldname": "verification_result",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": "Verified On",
			"fieldname": "verified_on",
			"fieldtype": "Datetime",
			"width": 160,
		},
	]


def get_data(filters):
	status = clean_text(filters.get("verification_status")) or "Catch-All"
	conditions = ["ccn.parenttype = 'Company Profile'", "ccn.verification_status = %(verification_status)s"]
	values = {"verification_status": status}

	if clean_text(filters.get("industry_tier")):
		conditions.append("cp.industry_tier = %(industry_tier)s")
		values["industry_tier"] = clean_text(filters.get("industry_tier"))

	return frappe.db.sql(
		f"""
		SELECT
			ccn.parent AS company_profile,
			cp.entity_name,
			ccn.name_text,
			ccn.verification_status,
			ccn.verified_email,
			ccn.verification_result,
			ccn.verified_on
		FROM `tabCompany Candidate Name` ccn
		JOIN `tabCompany Profile` cp ON cp.name = ccn.parent
		WHERE {" AND ".join(conditions)}
		ORDER BY ccn.verified_on DESC
		""",
		values,
		as_dict=True,
	)


def clean_text(value):
	if value is None:
		return None
	value = str(value).strip()
	return value or None
