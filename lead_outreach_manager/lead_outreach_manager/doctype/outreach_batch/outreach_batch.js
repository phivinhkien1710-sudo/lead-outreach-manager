// Copyright (c) 2026, Kien Phi and contributors
// For license information, please see license.txt

const OUTREACH_BATCH_UPDATE_EVENT = "outreach_batch_update";

frappe.ui.form.on("Outreach Batch", {
	setup(frm) {
		set_completed_import_query(frm);
	},
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Queue Batch"), () => {
				frappe.confirm(
					__(
						"This will schedule sending for every eligible Outreach Email in the background, staggered per the rate limit. Continue?"
					),
					() => {
						frappe.call({
							method:
								"lead_outreach_manager.lead_outreach_manager.doctype.outreach_batch.outreach_batch.queue_batch_scheduling",
							args: { batch_name: frm.doc.name },
							freeze: true,
							freeze_message: __("Queuing batch..."),
							callback() {
								frm.reload_doc();
								frappe.show_alert({
									message: __(
										"Batch queued. This form will refresh automatically when it finishes."
									),
									indicator: "blue",
								});
							},
						});
					}
				);
			});
		}

		if (["Queued", "Scheduling"].includes(frm.doc.status)) {
			frm.dashboard.set_headline_alert(
				__(
					"This batch is running in the background. The form will refresh automatically when it finishes."
				)
			);
			register_outreach_batch_listener(frm);
		}
	},
});

function set_completed_import_query(frm) {
	frm.set_query("import_run", () => ({
		filters: { status: ["in", ["Completed", "Completed With Errors"]] },
	}));
}

function register_outreach_batch_listener(frm) {
	if (frm.__outreach_batch_listener_registered) {
		return;
	}
	frm.__outreach_batch_listener_registered = true;

	frappe.realtime.on(OUTREACH_BATCH_UPDATE_EVENT, (data) => {
		if (data.batch_name === frm.doc.name) {
			frm.__outreach_batch_listener_registered = false;
			frm.reload_doc();
		}
	});
}
