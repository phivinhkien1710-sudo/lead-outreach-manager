#!/usr/bin/env bash
# Restores a data backup into an install created by install.sh.
#
# Run this ONLY with a backup you received through a private handoff (email,
# a private file transfer, a USB drive) — never a file from this public repo.
# It contains real company and contact data.
#
# The backup directory must contain the files `bench backup --with-files`
# produces: one *-database.sql.gz, one *-files.tar (public files), and one
# *-private-files.tar.
#
# Usage:
#   ./restore-data.sh /path/to/backup-directory

set -euo pipefail

BACKUP_DIR="${1:?Usage: ./restore-data.sh /path/to/backup-directory}"
PROJECT="${LOM_PROJECT:-lead-outreach}"
SITENAME="${LOM_SITENAME:-lead-outreach.local}"
WORKDIR="${LOM_WORKDIR:-$HOME/lead-outreach-manager-install}"
COMPOSE_FILE="${LOM_COMPOSE_FILE:-$WORKDIR/${PROJECT}-compose.yml}"
ENCRYPTION_KEY="${LOM_ENCRYPTION_KEY:-}"

if [ ! -d "$BACKUP_DIR" ]; then
	echo "Not a directory: $BACKUP_DIR" >&2
	exit 1
fi
if [ ! -f "$COMPOSE_FILE" ]; then
	echo "Compose file not found: $COMPOSE_FILE (run install.sh first, or set LOM_COMPOSE_FILE)" >&2
	exit 1
fi

DB_FILE=$(find "$BACKUP_DIR" -name "*-database.sql.gz" | head -1)
PRIVATE_FILES=$(find "$BACKUP_DIR" -name "*-private-files.tar" | head -1)
PUBLIC_FILES=$(find "$BACKUP_DIR" -name "*-files.tar" ! -name "*-private-files.tar" | head -1)

if [ -z "$DB_FILE" ]; then
	echo "No *-database.sql.gz file found in $BACKUP_DIR" >&2
	exit 1
fi

CONTAINER=$(docker compose -f "$COMPOSE_FILE" -p "$PROJECT" ps -q backend)
if [ -z "$CONTAINER" ]; then
	echo "Could not find a running 'backend' container for project $PROJECT — is the install still running?" >&2
	exit 1
fi

REMOTE_DIR="/home/frappe/frappe-bench/sites/${SITENAME}/private/backups"
echo "Copying backup files into the running install..."
docker exec "$CONTAINER" mkdir -p "$REMOTE_DIR"
docker cp "$DB_FILE" "$CONTAINER:$REMOTE_DIR/"
[ -n "$PUBLIC_FILES" ] && docker cp "$PUBLIC_FILES" "$CONTAINER:$REMOTE_DIR/"
[ -n "$PRIVATE_FILES" ] && docker cp "$PRIVATE_FILES" "$CONTAINER:$REMOTE_DIR/"

RESTORE_CMD="bench --site ${SITENAME} restore --force ${REMOTE_DIR}/$(basename "$DB_FILE")"
[ -n "$PUBLIC_FILES" ] && RESTORE_CMD="$RESTORE_CMD --with-public-files ${REMOTE_DIR}/$(basename "$PUBLIC_FILES")"
[ -n "$PRIVATE_FILES" ] && RESTORE_CMD="$RESTORE_CMD --with-private-files ${REMOTE_DIR}/$(basename "$PRIVATE_FILES")"
[ -n "$ENCRYPTION_KEY" ] && RESTORE_CMD="$RESTORE_CMD --encryption-key $ENCRYPTION_KEY"

echo "Restoring..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT" exec backend bash -c "$RESTORE_CMD"

cat <<EOF

== Done ==
Log in and confirm Company Profile / Contact records are visible.

Before real outreach: open Outreach Settings and re-enter your OWN Email
Account and (if using verification) your OWN MillionVerifier key — these
were deliberately cleared from the handoff data and are not carried over
by this restore.
EOF
