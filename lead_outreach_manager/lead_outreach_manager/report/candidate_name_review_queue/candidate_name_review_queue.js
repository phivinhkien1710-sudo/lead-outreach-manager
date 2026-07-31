frappe.query_reports["Candidate Name Review Queue"] = {
	filters: [
		{
			fieldname: "classification_status",
			label: __("Classification Status"),
			fieldtype: "Select",
			options: "Needs Review\nAuto Confirmed\nAuto Rejected\nError",
			default: "Needs Review",
			description: __(
				"Switch to Auto Confirmed to see names the funnel verified and acted on automatically."
			),
		},
		{
			fieldname: "classification_method",
			label: __("Method"),
			fieldtype: "Select",
			options: "\nBlacklist\nEmail Match\nLLM",
		},
		{
			fieldname: "llm_provider",
			label: __("LLM Provider"),
			fieldtype: "Select",
			options: "\nclaude\ncodex",
		},
		{
			fieldname: "llm_verdict",
			label: __("LLM Verdict"),
			fieldtype: "Select",
			options: "\nperson\nnot_person\nuncertain",
		},
		{
			fieldname: "min_confidence",
			label: __("Min Confidence"),
			fieldtype: "Float",
		},
		{
			fieldname: "industry_tier",
			label: __("Industry Tier"),
			fieldtype: "Data",
		},
	],
};
