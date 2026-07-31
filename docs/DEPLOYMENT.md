# Deployment Guide

For whoever provisions and operates this app — **not for end users.** Infra-agnostic on purpose: no
specific host, cloud provider, or domain is assumed here, since none has been chosen yet. Check the
requirements below off against whatever environment gets picked.

This package ships **no live data.** The dev site this app was built against holds real imported
companies, contacts, verified emails, and outreach drafts — none of that is included. A fresh install
starts completely empty; importing real leads is a separate action against your own source data (see
README's "Importing leads").

## Infrastructure requirements

- A Frappe/ERPNext **bench**, compatible with this app's `requires-python >=3.10`.
- **Redis** — a bench requirement regardless, but make sure it's a persistent, properly-managed
  instance in production, not something hand-started for a dev session (see "Process management" below
  for what that actually looked like during development, and why it's not a template to copy).
- A **process manager** for production — `bench setup production` (supervisor + nginx) or an
  equivalent. Development used a manually-launched `bench start` in a terminal — fine for iterating on
  code, not something to run unattended for a real deployment (it dies with the terminal session, has
  no restart-on-crash, and needs its Redis instances started by hand beforehand).
- An **outgoing-mail-capable email account** the app can send through (see "Email sending" below).
- The **`claude` CLI** reachable by whatever machine runs the background worker process, if candidate
  classification will be used (optional `codex` CLI as a fallback). See the README's Dependencies
  section for how these get auto-detected.
- A **funded MillionVerifier account** if email verification will be used (see "Email verification"
  below).

## Install

```bash
bench new-site <your-site>.local
bench get-app lead_outreach_manager <path-or-url-to-this-repo>
bench --site <your-site>.local install-app lead_outreach_manager
bench --site <your-site>.local scheduler enable
```

The last step matters: the app's one cron job (hourly Outreach Email reconciliation, see README's
Scheduled Tasks) silently does nothing if the site's scheduler is disabled or paused — there's no error,
records just never update.

## Configuration checklist

Maps onto the README's "Outreach Settings, in full" section — before enabling real outreach:

- [ ] Outgoing Email Account configured, `enable_outgoing=1`
- [ ] At least one Email Template created, reviewed real copy (not placeholder)
- [ ] `Outreach Settings.default_email_template` and `default_sender_email_account` set
- [ ] `Outreach Settings.email_verification_api_key` set, if using verification
- [ ] Decide on `auto_classify_after_import` / `auto_verify_after_classification` — both default off;
      leaving them off means classification and verification stay manual, human-triggered actions

## Known gaps to resolve before real outreach volume

These are real things found through actually using the app during development — not hypothetical
concerns. Each is a decision for the receiving team to make, not something this package can resolve on
their behalf.

**Sender account.** Development used a personal Gmail account. It works — one real email sent through
it successfully — but it's wrong for company outreach at any real volume: Gmail's free-tier send cap is
roughly 500/day, and sending cold outreach from a personal address risks that account's own
deliverability and reputation. Needs a real business domain + transactional email setup before volume
sending.

**Email Template content.** The template used in development is literally named "LOM Test - Industrial
Intro" — it's placeholder copy, not reviewed messaging. Write and review real content before enabling
any sends.

**No bulk-approve.** `approve_outreach_email` only ever acts on one `Outreach Email` at a time — a
deliberate human-review checkpoint (see README's Design Decisions), not an oversight. But at real
volume it's a genuine bottleneck: development alone generated 1,100+ Draft outreach emails, and
approving them one at a time does not scale. If the receiving team needs bulk approval, that's a scope
decision beyond "deploy as-is" — flagging it here rather than quietly building it into this handoff.

**MillionVerifier credits.** Pay-as-you-go, and the development account ran out mid-use (a real
"Insufficient credits" API error, not a hypothetical). Budget for it — verification volume drives cost
directly (up to 6 API calls per candidate).

**Importer default paths.** Both `imports/company_profile_imports.py` and
`imports/vietnam_lead_imports.py` default `sqlite_path` to a personal absolute path from development.
Always pass `sqlite_path` explicitly against your own source database (see README's "Importing leads").

## Verifying a fresh install

Before trusting this package, prove it actually installs clean:

```bash
bench new-site lom-verify.local
bench --site lom-verify.local install-app lead_outreach_manager
bench --site lom-verify.local run-tests --app lead_outreach_manager
```

Then follow the README's own Setup section literally, step by step, against that site — the point is
to catch drift between what the docs claim and what the code actually needs before it matters.
