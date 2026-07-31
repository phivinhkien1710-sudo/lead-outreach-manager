// Copyright (c) 2026, Kien Phi and contributors
// For license information, please see license.txt
//
// Injected onto the core Contact doctype via hooks.py's doctype_js — a
// standard, non-invasive way to extend a core doctype's Desk form without
// touching Frappe core.

frappe.ui.form.on("Contact", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		const company_profile_link = (frm.doc.links || []).find(
			(link) => link.link_doctype === "Company Profile"
		);
		if (!company_profile_link) {
			return;
		}

		frappe.db
			.get_value("Company Profile", company_profile_link.link_name, [
				"has_email_contact",
				"do_not_contact",
			])
			.then(({ message }) => {
				if (!message) {
					return;
				}
				const eligible =
					message.has_email_contact && !message.do_not_contact && !frm.doc.unsubscribed;
				if (!eligible) {
					return;
				}
				add_generate_outreach_email_button(frm, company_profile_link.link_name);
			});
	},
});

function add_generate_outreach_email_button(frm, company_profile) {
	frm.add_custom_button(__("Generate Outreach Email"), () => {
		frappe.call({
			method:
				"lead_outreach_manager.lead_outreach_manager.doctype.outreach_email.outreach_email.create_outreach_email",
			args: {
				company_profile: company_profile,
				contact: frm.doc.name,
			},
			freeze: true,
			freeze_message: __("Generating draft..."),
			callback(r) {
				if (!r.message) {
					return;
				}
				frappe.show_alert({
					message: __("Draft created — review it before sending."),
					indicator: "green",
				});
				frappe.set_route("Form", "Outreach Email", r.message.outreach_email);
			},
		});
	});
}
