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
BRANCH="${LOM_BRANCH:-v1.1.0}"
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

cat >apps.json <<JSON
[
  {
    "url": "${REPO_URL}",
    "branch": "${BRANCH}"
  }
]
JSON

echo "Building and starting everything — this is the slow step (10-20 minutes)..."
python3 easy-install.py build \
	--project "$PROJECT" \
	--apps-json apps.json \
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
