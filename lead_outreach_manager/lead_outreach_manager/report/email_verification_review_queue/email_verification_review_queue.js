frappe.query_reports["Email Verification Review Queue"] = {
	filters: [
		{
			fieldname: "verification_status",
			label: __("Verification Status"),
			fieldtype: "Select",
			options: "Catch-All\nVerified\nNot Deliverable\nError",
			default: "Catch-All",
			description: __(
				"Catch-All means the domain accepts everything, so the automatic pass couldn't confirm this one address — promote it manually via the Use This Email button on the Company Profile."
			),
		},
		{
			fieldname: "industry_tier",
			label: __("Industry Tier"),
			fieldtype: "Data",
		},
	],
};
