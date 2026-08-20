#!/usr/bin/env bash
# Provision droplet-local PostgreSQL for the aedwards app.
#
# Installs PostgreSQL from apt, creates the `aedwards` role (peer-authenticated
# over the local unix socket — no password, no TCP listener needed) and the
# `aedwards` database, and installs a nightly pg_dump backup cron with 7-day
# retention (mirroring the retired SQLite backup scheme).
#
# Idempotent: safe to re-run; existing role/database/cron are left alone.
#
# Usage (from the repo root, against staging or prod):
#   bash deploy/provision_pg.sh <host-or-ip>
#
# After provisioning, the app's DATABASE_URL (in /opt/aedwards/.env) becomes:
#   postgresql://aedwards@/aedwards?host=/var/run/postgresql
# — but switching prod is a runbook step (docs/runbooks/postgres-cutover.md),
# not something this script does.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <host-or-ip>" >&2
  exit 1
fi

HOST="$1"
KEY_PATH="${KEY_PATH:-$HOME/.ssh/NoJobDevKey.pem}"
SSH_USER="${SSH_USER:-root}"
APP_USER="${APP_USER:-aedwards}"
PG_DB="${PG_DB:-aedwards}"
BACKUP_DIR="${BACKUP_DIR:-/opt/aedwards/pg_backups}"

if [[ ! -f "${KEY_PATH}" ]]; then
  echo "SSH key not found: ${KEY_PATH}" >&2
  exit 1
fi

ssh -i "${KEY_PATH}" "${SSH_USER}@${HOST}" bash -s <<REMOTE
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
if ! command -v psql >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq postgresql postgresql-contrib
fi
systemctl enable --now postgresql

# Role matching the app's system user -> peer auth over the unix socket.
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname = '${APP_USER}'" | grep -q 1; then
  sudo -u postgres createuser "${APP_USER}"
fi
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname = '${PG_DB}'" | grep -q 1; then
  sudo -u postgres createdb -O "${APP_USER}" "${PG_DB}"
fi

# Nightly 2 AM pg_dump with 7-day retention, run as the app user (peer auth).
mkdir -p "${BACKUP_DIR}"
chown "${APP_USER}:${APP_USER}" "${BACKUP_DIR}"
cat > /etc/cron.d/aedwards-pg-backup <<'CRON'
0 2 * * * ${APP_USER} pg_dump -Fc -h /var/run/postgresql ${PG_DB} > ${BACKUP_DIR}/${PG_DB}-\$(date +\%F).dump && find ${BACKUP_DIR} -name '${PG_DB}-*.dump' -mtime +7 -delete
CRON
chmod 644 /etc/cron.d/aedwards-pg-backup

echo "PostgreSQL provisioned: \$(psql --version)"
sudo -u postgres psql -tAc "SELECT datname, pg_get_userbyid(datdba) FROM pg_database WHERE datname = '${PG_DB}'"
REMOTE

echo "provision_pg complete on ${HOST}."
echo "App DATABASE_URL for this host: postgresql://${APP_USER}@/${PG_DB}?host=/var/run/postgresql"
