// Copyright (c) 2026, Kien Phi and contributors
// For license information, please see license.txt

const VERIFICATION_RUN_UPDATE_EVENT = "email_verification_update";

frappe.ui.form.on("Email Verification Run", {
	setup(frm) {
		set_completed_import_query(frm);
	},
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Start Verification"), () => {
				frappe.confirm(
					__(
						"This will check pending confirmed candidates in the selected import batch (or all pending candidates when blank) and promote deliverable results. Continue?"
					),
					() => {
						frappe.call({
							method:
								"lead_outreach_manager.lead_outreach_manager.doctype.email_verification_run.email_verification_run.queue_verification_run",
							args: { run_name: frm.doc.name },
							freeze: true,
							freeze_message: __("Queuing verification run..."),
							callback() {
								frm.reload_doc();
								frappe.show_alert({
									message: __(
										"Run queued. This form will refresh automatically when it finishes."
									),
									indicator: "blue",
								});
							},
						});
					}
				);
			});
		}

		if (["Queued", "Running"].includes(frm.doc.status)) {
			frm.dashboard.set_headline_alert(
				__(
					"This run is executing in the background. The form will refresh automatically when it finishes."
				)
			);
			register_verification_run_listener(frm);
		}
	},
});

function set_completed_import_query(frm) {
	frm.set_query("import_run", () => ({
		filters: { status: ["in", ["Completed", "Completed With Errors"]] },
	}));
}

function register_verification_run_listener(frm) {
	if (frm.__verification_run_listener_registered) {
		return;
	}
	frm.__verification_run_listener_registered = true;

	frappe.realtime.on(VERIFICATION_RUN_UPDATE_EVENT, (data) => {
		if (data.run_name === frm.doc.name) {
			frm.__verification_run_listener_registered = false;
			frm.reload_doc();
		}
	});
}
