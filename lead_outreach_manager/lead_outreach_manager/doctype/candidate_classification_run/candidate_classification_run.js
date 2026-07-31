// Copyright (c) 2026, Kien Phi and contributors
// For license information, please see license.txt

const CLASSIFICATION_RUN_UPDATE_EVENT = "candidate_classification_update";

frappe.ui.form.on("Candidate Classification Run", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Start Classification"), () => {
				frappe.confirm(
					__(
						"This will classify every pending candidate name in the background — deterministic passes first, then the claude CLI (your Claude subscription) for the rest. Continue?"
					),
					() => {
						frappe.call({
							method:
								"lead_outreach_manager.lead_outreach_manager.doctype.candidate_classification_run.candidate_classification_run.queue_classification_run",
							args: { run_name: frm.doc.name },
							freeze: true,
							freeze_message: __("Queuing classification run..."),
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
			register_classification_run_listener(frm);
		}
	},
});

function register_classification_run_listener(frm) {
	if (frm.__classification_run_listener_registered) {
		return;
	}
	frm.__classification_run_listener_registered = true;

	frappe.realtime.on(CLASSIFICATION_RUN_UPDATE_EVENT, (data) => {
		if (data.run_name === frm.doc.name) {
			frm.__classification_run_listener_registered = false;
			frm.reload_doc();
		}
	});
}
