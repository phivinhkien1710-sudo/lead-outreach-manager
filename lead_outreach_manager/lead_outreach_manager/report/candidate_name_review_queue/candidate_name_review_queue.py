"""Browses classification funnel results for Company Candidate Name rows.
Defaults to the Needs Review queue — the human end of the funnel, names the
LLM couldn't confidently decide — but the classification_status filter can
switch to Auto Confirmed (names the LLM/email-match pass verified and acted
on) or Auto Rejected/Error. Sorted most-confident 'person' first so a Needs
Review pass works the likeliest real names first; confirmation itself still
happens via the Confirm Name button on the linked Company Profile's
candidate_names grid."""

import frappe


def execute(filters=None):
	filters = filters or {}
	data = get_data(filters)
	return get_columns(), data, None, get_chart(data), get_report_summary(data)


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
			"label": "Country",
			"fieldname": "country",
			"fieldtype": "Data",
			"width": 100,
		},
		{
			"label": "Industry Tier",
			"fieldname": "industry_tier",
			"fieldtype": "Data",
			"width": 120,
		},
		{
			"label": "Candidate Name",
			"fieldname": "name_text",
			"fieldtype": "Data",
			"width": 180,
		},
		{
			"label": "Title",
			"fieldname": "title_text",
			"fieldtype": "Data",
			"width": 160,
		},
		{
			"label": "Status",
			"fieldname": "classification_status",
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"label": "LLM Verdict",
			"fieldname": "llm_verdict",
			"fieldtype": "Data",
			"width": 100,
		},
		{
			"label": "Confidence",
			"fieldname": "llm_confidence",
			"fieldtype": "Float",
			"precision": 2,
			"width": 100,
		},
		{
			"label": "Method",
			"fieldname": "classification_method",
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"label": "Provider",
			"fieldname": "llm_provider",
			"fieldtype": "Data",
			"width": 90,
		},
		{
			"label": "Confirmed",
			"fieldname": "confirmed",
			"fieldtype": "Check",
			"width": 90,
		},
		{
			"label": "Source URL",
			"fieldname": "source_url",
			"fieldtype": "Data",
			"width": 260,
		},
		{
			"label": "Classified On",
			"fieldname": "classified_on",
			"fieldtype": "Datetime",
			"width": 160,
		},
	]


def get_data(filters):
	status = clean_text(filters.get("classification_status")) or "Needs Review"
	conditions = [
		"ccn.parenttype = 'Company Profile'",
		"ccn.classification_status = %(classification_status)s",
	]
	values = {"classification_status": status}
	if clean_text(filters.get("import_run")):
		conditions.append("""
			EXISTS (
				SELECT 1 FROM `tabLead Import Batch Member` lbm
				WHERE lbm.import_run = %(import_run)s AND lbm.company_profile = ccn.parent
			)
		""")
		values["import_run"] = clean_text(filters.get("import_run"))

	if status == "Needs Review":
		# A human can confirm a Needs Review row directly via the Company
		# Profile grid without classification_status catching up — don't
		# show already-actioned rows as if they still need attention. Auto
		# Confirmed rows are always confirmed=1 by definition, so this
		# exclusion only makes sense for the Needs Review view.
		conditions.append("IFNULL(ccn.confirmed, 0) = 0")

	if clean_text(filters.get("llm_verdict")):
		conditions.append("ccn.llm_verdict = %(llm_verdict)s")
		values["llm_verdict"] = clean_text(filters.get("llm_verdict"))

	if clean_text(filters.get("classification_method")):
		conditions.append("ccn.classification_method = %(classification_method)s")
		values["classification_method"] = clean_text(filters.get("classification_method"))

	if clean_text(filters.get("llm_provider")):
		conditions.append("ccn.llm_provider = %(llm_provider)s")
		values["llm_provider"] = clean_text(filters.get("llm_provider"))

	if filters.get("min_confidence"):
		conditions.append("ccn.llm_confidence >= %(min_confidence)s")
		values["min_confidence"] = frappe.utils.flt(filters.get("min_confidence"))

	if clean_text(filters.get("industry_tier")):
		conditions.append("cp.industry_tier = %(industry_tier)s")
		values["industry_tier"] = clean_text(filters.get("industry_tier"))

	if clean_text(filters.get("company_name")):
		conditions.append("cp.entity_name LIKE %(company_name)s")
		values["company_name"] = f"%{clean_text(filters.get('company_name'))}%"

	if clean_text(filters.get("country")):
		conditions.append("cp.country = %(country)s")
		values["country"] = clean_text(filters.get("country"))

	return frappe.db.sql(
		f"""
		SELECT
			ccn.parent AS company_profile,
			cp.entity_name,
			cp.country,
			cp.industry_tier,
			ccn.name_text,
			ccn.title_text,
			ccn.classification_status,
			ccn.llm_verdict,
			ccn.llm_confidence,
			ccn.classification_method,
			ccn.llm_provider,
			ccn.confirmed,
			ccn.source_url,
			ccn.classified_on
		FROM `tabCompany Candidate Name` ccn
		JOIN `tabCompany Profile` cp ON cp.name = ccn.parent
		WHERE {" AND ".join(conditions)}
		ORDER BY (ccn.llm_verdict = 'person') DESC, ccn.llm_confidence DESC, ccn.parent
		""",
		values,
		as_dict=True,
	)


def get_chart(data):
	countries = ["Singapore", "Vietnam"]
	counts = {country: 0 for country in countries}
	for row in data:
		country = row.get("country") or "Unspecified"
		counts[country] = counts.get(country, 0) + 1
	labels = [*countries, *sorted(country for country in counts if country not in countries)]
	return {
		"data": {"labels": labels, "datasets": [{"name": "Candidates", "values": [counts[x] for x in labels]}]},
		"type": "bar",
		"colors": ["#2490ef"],
	}


def get_report_summary(data):
	return [
		{"label": "Candidates", "value": len(data), "indicator": "Blue", "datatype": "Int"},
		{
			"label": "Singapore",
			"value": sum(1 for row in data if row.get("country") == "Singapore"),
			"indicator": "Green",
			"datatype": "Int",
		},
		{
			"label": "Vietnam",
			"value": sum(1 for row in data if row.get("country") == "Vietnam"),
			"indicator": "Orange",
			"datatype": "Int",
		},
		{
			"label": "Confirmed",
			"value": sum(1 for row in data if row.get("confirmed")),
			"indicator": "Green",
			"datatype": "Int",
		},
	]


def clean_text(value):
	if value is None:
		return None
	value = str(value).strip()
	return value or None
