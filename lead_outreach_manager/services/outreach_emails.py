"""Generate -> verify -> schedule flow for a single contact's outreach email,
built entirely on stock Frappe/ERPNext primitives (Communication, Email
Queue, Email Template) rather than custom mail-sending code.

Flow:
  create_outreach_email  -> unsent draft Communication + Outreach Email(Draft)
  [human edits the draft Communication directly on its own Desk form — that
   IS the verify step, nothing custom built for it]
  approve_outreach_email -> Outreach Email(Ready to Send)
  schedule_send          -> sets Communication.send_after, calls
                             Communication.send_email(), which creates the
                             actual Email Queue row. The already-registered
                             frappe.email.queue.flush cron (every ~60s)
                             handles the rest.
  reconcile_outreach_email_status (hourly cron) -> Sent/Failed follow-up,
                             since nothing calls back into Outreach Email when
                             Email Queue finishes asynchronously.
"""
from __future__ import annotations

import frappe

GUESSED_EMAIL_FIELDS = [
	field
	for i in range(1, 7)
	for field in (f"guessed_email_{i}", f"guessed_pattern_{i}")
]


def create_outreach_email(company_profile, contact, email_template=None):
	profile = frappe.get_doc("Company Profile", company_profile)
	contact_doc = frappe.get_doc("Contact", contact)

	check_can_contact(profile, contact_doc)

	template_name = email_template or _default_email_template()
	if not template_name:
		frappe.throw("No Email Template specified and no default configured in Outreach Settings.")
	template = frappe.get_doc("Email Template", template_name)

	recipient_email = contact_doc.email_id or profile.primary_email
	if not recipient_email:
		frappe.throw(f"No email address found for contact {contact_doc.name}.")

	context = {"doc": profile, "contact": contact_doc}
	subject = frappe.render_template(template.subject, context)
	content = frappe.render_template(template.response_, context)

	from frappe.core.doctype.communication.email import make

	comm_result = make(
		doctype="Company Profile",
		name=profile.name,
		subject=subject,
		content=content,
		recipients=[recipient_email],
		sender=_default_sender_email(),
		email_template=template.name,
		send_email=False,
	)
	communication_name = comm_result["name"]

	outreach = frappe.new_doc("Outreach Email")
	outreach.status = "Draft"
	outreach.company_profile = profile.name
	outreach.contact = contact_doc.name
	outreach.email_template = template.name
	outreach.communication = communication_name
	outreach.recipient_email = recipient_email
	outreach.subject = subject
	outreach.generated_by = frappe.session.user
	outreach.generated_on = frappe.utils.now_datetime()
	_copy_guessed_emails(outreach, contact_doc.name)
	outreach.insert(ignore_permissions=True)

	return {"outreach_email": outreach.name, "communication": communication_name}


def approve_outreach_email(outreach_email_name):
	outreach = frappe.get_doc("Outreach Email", outreach_email_name)
	if outreach.status != "Draft":
		frappe.throw(f"Only a Draft Outreach Email can be approved (current status: {outreach.status}).")

	profile = frappe.get_doc("Company Profile", outreach.company_profile)
	contact_doc = frappe.get_doc("Contact", outreach.contact)
	check_can_contact(profile, contact_doc)  # re-check — state may have drifted since generation

	comm = frappe.get_doc("Communication", outreach.communication)
	outreach.subject = comm.subject  # snapshot the (possibly human-edited-during-review) subject
	outreach.status = "Ready to Send"
	outreach.approved_by = frappe.session.user
	outreach.approved_on = frappe.utils.now_datetime()
	outreach.save(ignore_permissions=True)

	return {"status": outreach.status}


def schedule_send(outreach_email_name, send_after=None):
	outreach = frappe.get_doc("Outreach Email", outreach_email_name)
	if outreach.status != "Ready to Send":
		frappe.throw(
			f"Only a 'Ready to Send' Outreach Email can be scheduled (current status: {outreach.status})."
		)

	profile = frappe.get_doc("Company Profile", outreach.company_profile)
	contact_doc = frappe.get_doc("Contact", outreach.contact)
	try:
		check_can_contact(profile, contact_doc)
	except frappe.ValidationError as exc:
		outreach.status = "Cancelled"
		outreach.blocked_reason = str(exc)
		outreach.save(ignore_permissions=True)
		raise

	send_after_dt = frappe.utils.get_datetime(send_after) if send_after else frappe.utils.now_datetime()

	comm = frappe.get_doc("Communication", outreach.communication)
	comm.send_after = send_after_dt
	comm.save(ignore_permissions=True)
	comm.send_email()

	outreach.status = "Scheduled"
	outreach.send_after = send_after_dt
	outreach.save(ignore_permissions=True)

	return {"status": outreach.status, "send_after": str(send_after_dt)}


def reconcile_outreach_email_status():
	"""Hourly cron (see hooks.py) — Email Queue's own flush cron sends mail
	asynchronously and never calls back into Outreach Email, so this closes
	the loop by reading the linked Email Queue row's final status."""
	scheduled = frappe.get_all("Outreach Email", filters={"status": "Scheduled"}, fields=["name", "communication"])
	result = {"sent": 0, "failed": 0, "unchanged": 0}

	for row in scheduled:
		if not row.communication:
			continue
		queue_row = frappe.db.get_value(
			"Email Queue", {"communication": row.communication}, ["status", "error"], as_dict=True
		)
		if not queue_row:
			result["unchanged"] += 1
			continue

		if queue_row.status == "Sent":
			frappe.db.set_value("Outreach Email", row.name, "status", "Sent")
			result["sent"] += 1
		elif queue_row.status == "Error":
			frappe.db.set_value(
				"Outreach Email", row.name, {"status": "Failed", "blocked_reason": queue_row.error or ""}
			)
			result["failed"] += 1
		else:
			result["unchanged"] += 1

	frappe.db.commit()
	return result


def check_can_contact(profile, contact_doc):
	if profile.do_not_contact:
		frappe.throw(f"{profile.entity_name} is marked Do Not Contact.")
	if contact_doc.get("unsubscribed"):
		frappe.throw(f"Contact {contact_doc.name} has unsubscribed.")
	if not profile.has_email_contact and not has_verified_email(contact_doc):
		frappe.throw(f"{profile.entity_name} has no email contact point.")


def has_verified_email(contact_doc) -> bool:
	"""Company Profile.has_email_contact is computed once at import time from
	the scraped contact_points (see imports/company_profile_imports.py) — it
	records whether a *generic* company email was found, and is never
	recomputed. Email verification (services/email_verification.py) can later
	establish a deliverable *personal* address for a contact on such a
	profile and promote it to Contact.email_id, which leaves the stale flag
	gating a lead we can now demonstrably reach.

	Deliberately narrow: only a row the provider confirmed deliverable
	("Verified") counts, and only when it still matches the address we would
	actually send to. An unverified guess sitting on Contact.email_id (a
	human Confirm Name pushes the top guess there by product decision, see
	services/email_guessing.py) is NOT enough to open this gate."""
	if not contact_doc.get("email_id"):
		return False
	return bool(
		frappe.db.exists(
			"Company Candidate Name",
			{
				"contact": contact_doc.name,
				"verification_status": "Verified",
				"verified_email": contact_doc.email_id,
			},
		)
	)


def _copy_guessed_emails(outreach, contact_name):
	"""Snapshots the confirmed Company Candidate Name row's guessed_email_1-6
	onto the new Outreach Email, purely for a reviewer's visibility — a
	confirmed contact can, in principle, be linked from more than one
	candidate_names row over time (e.g. re-confirmed later), so this takes
	the most recently confirmed one. Informational only: does not touch
	outreach.recipient_email, so the safety rule (only a human Confirm Name
	click may promote a guess to the real recipient) is untouched by this."""
	row = frappe.db.get_value(
		"Company Candidate Name",
		{"contact": contact_name, "confirmed": 1},
		GUESSED_EMAIL_FIELDS,
		as_dict=True,
		order_by="confirmed_on desc",
	)
	if not row:
		return
	outreach.update(row)


def backfill_guessed_emails():
	"""One-off cleanup for Outreach Email records created before
	_copy_guessed_emails existed — snapshots each one's confirmed contact's
	guessed_email_1-6/guessed_pattern_1-6 the same way generation now does
	automatically. Purely informational fields; does not touch recipient_email.
	bench execute lead_outreach_manager.services.outreach_emails.backfill_guessed_emails
	"""
	rows = frappe.db.sql(
		"SELECT name, contact FROM `tabOutreach Email` WHERE IFNULL(guessed_email_1, '') = ''",
		as_dict=True,
	)
	fixed = 0
	for row in rows:
		guesses = frappe.db.get_value(
			"Company Candidate Name",
			{"contact": row.contact, "confirmed": 1},
			GUESSED_EMAIL_FIELDS,
			as_dict=True,
			order_by="confirmed_on desc",
		)
		if not guesses:
			continue
		frappe.db.set_value("Outreach Email", row.name, guesses)
		fixed += 1

	frappe.db.commit()
	return {"fixed": fixed, "total_checked": len(rows)}


def _default_email_template():
	return frappe.db.get_single_value("Outreach Settings", "default_email_template")


def _default_sender_email():
	account = frappe.db.get_single_value("Outreach Settings", "default_sender_email_account")
	if not account:
		return None
	return frappe.db.get_value("Email Account", account, "email_id")
