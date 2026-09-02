"""Deduplicate memberships created before the composite membership key was enforced."""

import hashlib

import frappe


def execute():
	rows = frappe.get_all(
		"Lead Import Batch Member",
		fields=["name", "import_run", "company_profile"],
		order_by="creation asc",
	)
	seen = set()
	for row in rows:
		identity = (row.import_run, row.company_profile)
		if identity in seen:
			frappe.delete_doc(
				"Lead Import Batch Member", row.name, force=True, ignore_permissions=True
			)
			continue
		seen.add(identity)
		key_source = f"{row.import_run}|{row.company_profile}"
		membership_key = f"LBM-{hashlib.sha1(key_source.encode('utf-8')).hexdigest()[:20].upper()}"
		frappe.db.set_value(
			"Lead Import Batch Member", row.name, "membership_key", membership_key,
			update_modified=False,
		)

	for run_name in frappe.get_all("Lead CSV Import Run", pluck="name"):
		frappe.db.set_value(
			"Lead CSV Import Run",
			run_name,
			"batch_companies",
			frappe.db.count("Lead Import Batch Member", {"import_run": run_name}),
			update_modified=False,
		)
	frappe.db.commit()
