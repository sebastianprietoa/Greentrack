#!/usr/bin/env bash
set -euo pipefail

require_var() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "ERROR: Missing ${name}."
    exit 1
  fi
}

require_var OLD_DATABASE_URL
require_var NEW_DATABASE_URL

for cmd in pg_dump pg_restore psql; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: ${cmd} is not installed."
    exit 1
  fi
done

export PGSSLMODE="${PGSSLMODE:-require}"

BACKUP_FILE="greentrack_backup_$(date +%Y%m%d_%H%M%S).dump"

echo "Creating database backup..."
pg_dump "$OLD_DATABASE_URL" --no-owner --no-privileges -Fc -f "$BACKUP_FILE"

echo "Restoring backup into target database..."
pg_restore --no-owner --no-privileges --clean --if-exists -d "$NEW_DATABASE_URL" "$BACKUP_FILE"

show_count_if_present() {
  local database_url="$1"
  local table_name="$2"
  local label="$3"

  if [ "$(psql "$database_url" -v ON_ERROR_STOP=1 -Atqc "SELECT to_regclass('public.${table_name}') IS NOT NULL;")" = "t" ]; then
    psql "$database_url" -v ON_ERROR_STOP=1 -c "SELECT COUNT(*) AS ${label} FROM ${table_name};"
  fi
}

echo "Verifying restored tables..."
show_count_if_present "$NEW_DATABASE_URL" usuarios usuarios
show_count_if_present "$NEW_DATABASE_URL" registros registros

echo "Clone completed. Backup saved as ${BACKUP_FILE}."
