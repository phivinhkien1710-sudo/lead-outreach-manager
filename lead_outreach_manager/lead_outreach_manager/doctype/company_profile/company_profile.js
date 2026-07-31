// Copyright (c) 2026, Kien Phi and contributors
// For license information, please see license.txt

frappe.ui.form.on("Company Profile", {
	refresh(frm) {},
});

frappe.ui.form.on("Company Candidate Name", {
	// Fires when the per-row "Confirm Name" Button field (see
	// company_candidate_name.json, depends_on: eval:!doc.confirmed) is
	// clicked. A candidate name is a noisy best-effort guess until a human
	// explicitly confirms it — see services/candidate_names.py.
	confirm_name(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.call({
			method:
				"lead_outreach_manager.lead_outreach_manager.doctype.company_profile.company_profile.confirm_candidate_name",
			args: {
				company_profile: frm.doc.name,
				row_name: row.name,
			},
			freeze: true,
			freeze_message: __("Confirming name..."),
			callback() {
				frm.reload_doc();
			},
		});
	},

	// Fires when the per-row "Use This Email" Button field (see
	// company_candidate_name.json, depends_on: verification_status=="Catch-All")
	// is clicked. A Catch-All result means the domain accepts everything, so
	// the automatic verification pass won't trust it as the outreach
	// recipient on its own — this lets a human promote it anyway. See
	// services/email_verification.py.
	use_this_email(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.call({
			method:
				"lead_outreach_manager.lead_outreach_manager.doctype.company_profile.company_profile.use_this_email",
			args: {
				company_profile: frm.doc.name,
				row_name: row.name,
			},
			freeze: true,
			freeze_message: __("Promoting email..."),
			callback() {
				frm.reload_doc();
			},
		});
	},
});
