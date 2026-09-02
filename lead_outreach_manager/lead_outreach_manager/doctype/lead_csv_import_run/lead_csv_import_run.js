// Copyright (c) 2026, Kien Phi and contributors

const LEAD_CSV_IMPORT_UPDATE_EVENT = "lead_csv_import_update";

frappe.ui.form.on("Lead CSV Import Run", {
	refresh(frm) {
		frm.add_custom_button(__("Download CSV Template"), () => {
			window.open(
				"/api/method/lead_outreach_manager.lead_outreach_manager.doctype.lead_csv_import_run.lead_csv_import_run.download_template"
			);
		}, __("Tools"));

		if (frm.is_new()) return;

		if (["Draft", "Validation Failed"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Validate CSV"), () => call_import_method(frm, "validate_csv", __("Validating CSV...")));
		}

		if (["Validated", "Completed With Errors"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Start Import"), () => {
				frappe.confirm(
					__("Import all valid rows in this CSV? Existing companies are updated without removing candidate-review decisions."),
					() => call_import_method(frm, "queue_import", __("Queuing import..."))
				);
			});
		}

		if (["Queued", "Importing"].includes(frm.doc.status)) {
			frm.dashboard.set_headline_alert(__("This CSV is being imported in the background."));
			register_import_listener(frm);
		}

		if (["Completed", "Completed With Errors"].includes(frm.doc.status)) {
			add_batch_workflow_buttons(frm);
		}
	},
});

function add_batch_workflow_buttons(frm) {
	frm.add_custom_button(__("Classify Candidates"), () => {
		frappe.new_doc("Candidate Classification Run", { import_run: frm.doc.name });
	}, __("Batch Workflow"));
	frm.add_custom_button(__("Review Candidates"), () => {
		frappe.set_route("query-report", "Candidate Name Review Queue", { import_run: frm.doc.name });
	}, __("Batch Workflow"));
	frm.add_custom_button(__("Verify Emails"), () => {
		frappe.new_doc("Email Verification Run", { import_run: frm.doc.name });
	}, __("Batch Workflow"));
	frm.add_custom_button(__("Review Verification"), () => {
		frappe.set_route("query-report", "Email Verification Review Queue", { import_run: frm.doc.name });
	}, __("Batch Workflow"));
	frm.add_custom_button(__("Generate Drafts"), () => {
		frappe.new_doc("Outreach Generation Run", { import_run: frm.doc.name });
	}, __("Batch Workflow"));
	frm.add_custom_button(__("Schedule Approved Drafts"), () => {
		frappe.new_doc("Outreach Batch", { import_run: frm.doc.name });
	}, __("Batch Workflow"));
}

function call_import_method(frm, action, freeze_message) {
	frappe.call({
		method: `lead_outreach_manager.lead_outreach_manager.doctype.lead_csv_import_run.lead_csv_import_run.${action}`,
		args: { run_name: frm.doc.name },
		freeze: true,
		freeze_message,
		callback() { frm.reload_doc(); },
	});
}

function register_import_listener(frm) {
	if (frm.__lead_csv_listener_registered) return;
	frm.__lead_csv_listener_registered = true;
	frappe.realtime.on(LEAD_CSV_IMPORT_UPDATE_EVENT, (data) => {
		if (data.run_name === frm.doc.name) {
			frm.__lead_csv_listener_registered = false;
			frm.reload_doc();
		}
	});
}
