// Copyright (c) 2026, Kien Phi and contributors
// For license information, please see license.txt

frappe.ui.form.on("Outreach Email", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		if (frm.doc.communication) {
			frm.add_custom_button(__("Open Draft"), () => {
				frappe.set_route("Form", "Communication", frm.doc.communication);
			});
		}

		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Approve"), () => {
				frappe.call({
					method:
						"lead_outreach_manager.lead_outreach_manager.doctype.outreach_email.outreach_email.approve_outreach_email",
					args: { outreach_email_name: frm.doc.name },
					freeze: true,
					freeze_message: __("Approving..."),
					callback() {
						frm.reload_doc();
					},
				});
			});
		}

		if (frm.doc.status === "Ready to Send") {
			frm.add_custom_button(__("Schedule Send"), () => {
				frappe.prompt(
					{
						fieldname: "send_after",
						label: __("Send After"),
						fieldtype: "Datetime",
						reqd: 1,
						default: frappe.datetime.now_datetime(),
					},
					(values) => {
						frappe.call({
							method:
								"lead_outreach_manager.lead_outreach_manager.doctype.outreach_email.outreach_email.schedule_send",
							args: {
								outreach_email_name: frm.doc.name,
								send_after: values.send_after,
							},
							freeze: true,
							freeze_message: __("Scheduling..."),
							callback() {
								frm.reload_doc();
							},
						});
					},
					__("Schedule Send"),
					__("Schedule")
				);
			});
		}
	},
});
