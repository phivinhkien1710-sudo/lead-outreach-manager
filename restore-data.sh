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

# `bench restore` needs the MariaDB root password and will otherwise sit at
# an interactive prompt with no indication why — fatal for a non-technical
# recipient. easy-install.py's own db container already has it as an env
# var; reading it from there means nobody ever has to go find or type it.
DB_CONTAINER=$(docker compose -f "$COMPOSE_FILE" -p "$PROJECT" ps -q db)
DB_ROOT_PASSWORD=""
if [ -n "$DB_CONTAINER" ]; then
	DB_ROOT_PASSWORD=$(docker exec "$DB_CONTAINER" printenv MYSQL_ROOT_PASSWORD 2>/dev/null || true)
fi
if [ -z "$DB_ROOT_PASSWORD" ]; then
	echo "Could not read the database root password from the 'db' container automatically." >&2
	echo "Pass it explicitly: LOM_DB_ROOT_PASSWORD=... ./restore-data.sh $BACKUP_DIR" >&2
	DB_ROOT_PASSWORD="${LOM_DB_ROOT_PASSWORD:?Set LOM_DB_ROOT_PASSWORD and re-run.}"
fi

REMOTE_DIR="/home/frappe/frappe-bench/sites/${SITENAME}/private/backups"
echo "Copying backup files into the running install..."
docker exec "$CONTAINER" mkdir -p "$REMOTE_DIR"
docker cp "$DB_FILE" "$CONTAINER:$REMOTE_DIR/"
[ -n "$PUBLIC_FILES" ] && docker cp "$PUBLIC_FILES" "$CONTAINER:$REMOTE_DIR/"
[ -n "$PRIVATE_FILES" ] && docker cp "$PRIVATE_FILES" "$CONTAINER:$REMOTE_DIR/"

RESTORE_CMD="bench --site ${SITENAME} restore --force --db-root-password ${DB_ROOT_PASSWORD} ${REMOTE_DIR}/$(basename "$DB_FILE")"
[ -n "$PUBLIC_FILES" ] && RESTORE_CMD="$RESTORE_CMD --with-public-files ${REMOTE_DIR}/$(basename "$PUBLIC_FILES")"
[ -n "$PRIVATE_FILES" ] && RESTORE_CMD="$RESTORE_CMD --with-private-files ${REMOTE_DIR}/$(basename "$PRIVATE_FILES")"
[ -n "$ENCRYPTION_KEY" ] && RESTORE_CMD="$RESTORE_CMD --encryption-key $ENCRYPTION_KEY"

echo "Restoring..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT" exec backend bash -c "$RESTORE_CMD"

# The backup's schema matches whatever Frappe/app versions were installed on
# the SENDING side at backup time, which won't exactly match this install's
# versions. Without this, the site comes back up but errors on the first
# thing that touches a column one side has and the other doesn't (hit this
# for real: 'Unknown column code_editor_type' on login, immediately after a
# restore that otherwise reported success).
echo "Migrating (reconciling schema differences from the sending side)..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT" exec backend bench --site "$SITENAME" migrate --skip-failing

# `bench restore` replaces the ENTIRE database, including the Administrator
# login itself — so the random password install.sh printed to your
# ~/passwords.txt no longer works after this point; it silently reverts to
# whoever's password was live on the sending side's site when the backup was
# made. Generating a fresh one here is what makes this handoff genuinely
# self-contained: the sender's password is never something you need to be
# told separately.
NEW_ADMIN_PASSWORD=$(docker compose -f "$COMPOSE_FILE" -p "$PROJECT" exec -T backend python3 -c \
	"import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(20)))" | tr -d '\r')
docker compose -f "$COMPOSE_FILE" -p "$PROJECT" exec backend bench --site "$SITENAME" set-admin-password "$NEW_ADMIN_PASSWORD" >/dev/null

cat <<EOF

== Done ==
New Administrator password (the restore overwrote the one install.sh gave
you earlier — this is the one that actually works now):

  $NEW_ADMIN_PASSWORD

Log in and confirm Company Profile / Contact records are visible.

Before real outreach: open Outreach Settings and re-enter your OWN Email
Account and (if using verification) your OWN MillionVerifier key — these
were deliberately cleared from the handoff data and are not carried over
by this restore.
EOF
