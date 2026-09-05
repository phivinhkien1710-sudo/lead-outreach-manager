#!/usr/bin/env bash
# One-command installer for Lead Outreach Manager.
#
# Wraps Frappe's own official Docker installer (easy-install.py) so a
# non-technical person only has to run this one script instead of the
# multi-step manual process in README.md's "Installing from scratch"
# section (which this script follows exactly — see that section if you
# want to understand what each step below actually does).
#
# This installs the APP ONLY, with no data. If you were given a data
# backup separately (through a private handoff, never from this public
# repo — it would contain real company/contact information), run
# restore-data.sh afterwards.
#
# Usage:
#   ./install.sh
#
# Override any default with an environment variable, e.g.:
#   LOM_SITENAME=my-outreach.local LOM_HTTP_PORT=8090 ./install.sh

set -euo pipefail

REPO_URL="${LOM_REPO_URL:-https://github.com/phivinhkien1710-sudo/lead-outreach-manager}"
BRANCH="${LOM_BRANCH:-v1.1.1}"
ERPNEXT_BRANCH="${LOM_ERPNEXT_BRANCH:-version-15}"
PROJECT="${LOM_PROJECT:-lead-outreach}"
SITENAME="${LOM_SITENAME:-lead-outreach.local}"
HTTP_PORT="${LOM_HTTP_PORT:-8080}"
EMAIL="${LOM_EMAIL:-you@example.com}"
WORKDIR="${LOM_WORKDIR:-$HOME/lead-outreach-manager-install}"

echo "== Lead Outreach Manager installer =="
echo "Project: $PROJECT   Site: $SITENAME   Port: $HTTP_PORT   Work dir: $WORKDIR"
echo

if ! command -v docker >/dev/null 2>&1; then
	cat <<'EOF'
Docker was not found on this computer. Install it first:

- Mac: install Docker Desktop (https://www.docker.com/products/docker-desktop/),
  open it once so it's running, then run this script again.
- Windows: install WSL2 (https://learn.microsoft.com/windows/wsl/install), then
  Docker Desktop with WSL2 integration enabled, then run this script again.
- Linux: Frappe's own installer can install Docker for you automatically —
  it will attempt that in the next step, no action needed here.
EOF
fi

mkdir -p "$WORKDIR"
cd "$WORKDIR"

if [ ! -f easy-install.py ]; then
	echo "Downloading Frappe's installer..."
	curl -fsSL -O https://raw.githubusercontent.com/frappe/bench/develop/easy-install.py
fi

# ERPNext is included deliberately, even though this app's own doctypes don't
# depend on it: the site this app's data comes from has erpnext installed, so a
# database restored from it lists erpnext in its installed apps. Without the
# erpnext package present, `bench migrate` after a restore dies with
# "No module named 'erpnext'" and the site is left half-restored. Found by
# actually restoring a real backup into a build that omitted it.
cat >apps.json <<JSON
[
  {
    "url": "https://github.com/frappe/erpnext",
    "branch": "${ERPNEXT_BRANCH}"
  },
  {
    "url": "${REPO_URL}",
    "branch": "${BRANCH}"
  }
]
JSON

# Python installed from python.org on macOS has no access to the system
# keychain's root certificates, so easy-install.py's HTTPS fetch of
# frappe_docker dies with "CERTIFICATE_VERIFY_FAILED: unable to get local
# issuer certificate" — and then reports the far more confusing
# "No such file or directory: 'frappe_docker'" as the actual error, which
# tells you nothing about the real cause. Hit for real on a clean Mac.
if [ -z "$(python3 -c 'import ssl; print(ssl.get_default_verify_paths().cafile or "")' 2>/dev/null)" ]; then
	CERTIFI_PEM=$(python3 -c 'import certifi; print(certifi.where())' 2>/dev/null || true)
	if [ -n "$CERTIFI_PEM" ]; then
		export SSL_CERT_FILE="$CERTIFI_PEM"
		export REQUESTS_CA_BUNDLE="$CERTIFI_PEM"
		echo "Note: Python had no CA bundle configured; using certifi's ($CERTIFI_PEM)."
	else
		cat <<'CERTWARN'
WARNING: Python has no root certificates configured and certifi isn't installed,
so the download step below will likely fail with CERTIFICATE_VERIFY_FAILED.
Fix it with either of these, then re-run this script:
  open "/Applications/Python 3.12/Install Certificates.command"
  python3 -m pip install certifi
CERTWARN
	fi
fi

echo "Building and starting everything — this is the slow step (10-20 minutes)..."
python3 easy-install.py build \
	--project "$PROJECT" \
	--apps-json apps.json \
	--app erpnext \
	--app lead_outreach_manager \
	--sitename "$SITENAME" \
	--no-ssl \
	--http-port "$HTTP_PORT" \
	--email "$EMAIL" \
	--deploy

cat <<EOF

== Done ==
Open http://localhost:${HTTP_PORT} in a browser.
Log in as Administrator — the password was saved to ~/passwords.txt on this computer.

Next steps (see README.md's "Setup" section):
  1. Configure an outgoing Email Account (a real business email, not a personal one).
  2. Create an Email Template with real outreach copy.
  3. Fill in Outreach Settings.

If you were given a data backup separately, run:
  ./restore-data.sh /path/to/that/backup/directory
EOF
