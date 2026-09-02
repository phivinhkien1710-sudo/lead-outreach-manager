// Copyright (c) 2026, Kien Phi and contributors
// For license information, please see license.txt

const OUTREACH_GENERATION_UPDATE_EVENT = "outreach_generation_update";

frappe.ui.form.on("Outreach Generation Run", {
	setup(frm) {
		set_completed_import_query(frm);
	},
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Start Generation"), () => {
				frappe.confirm(
					__(
						"This will generate a draft Outreach Email for every auto-confirmed contact that doesn't already have one, in the background. Continue?"
					),
					() => {
						frappe.call({
							method:
								"lead_outreach_manager.lead_outreach_manager.doctype.outreach_generation_run.outreach_generation_run.queue_generation_run",
							args: { run_name: frm.doc.name },
							freeze: true,
							freeze_message: __("Queuing generation run..."),
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

		if (["Queued", "Generating"].includes(frm.doc.status)) {
			frm.dashboard.set_headline_alert(
				__(
					"This run is generating drafts in the background. The form will refresh automatically when it finishes."
				)
			);
			register_generation_run_listener(frm);
		}
	},
});

function set_completed_import_query(frm) {
	frm.set_query("import_run", () => ({
		filters: { status: ["in", ["Completed", "Completed With Errors"]] },
	}));
}

function register_generation_run_listener(frm) {
	if (frm.__generation_run_listener_registered) {
		return;
	}
	frm.__generation_run_listener_registered = true;

	frappe.realtime.on(OUTREACH_GENERATION_UPDATE_EVENT, (data) => {
		if (data.run_name === frm.doc.name) {
			frm.__generation_run_listener_registered = false;
			frm.reload_doc();
		}
	});
}
