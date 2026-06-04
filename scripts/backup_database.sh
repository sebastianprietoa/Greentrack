#!/usr/bin/env bash
set -euo pipefail

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: Missing DATABASE_URL."
  exit 1
fi

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "ERROR: pg_dump is not installed."
  exit 1
fi

export PGSSLMODE="${PGSSLMODE:-require}"

BACKUP_FILE="greentrack_backup_$(date +%Y%m%d_%H%M%S).dump"

echo "Creating database backup..."
pg_dump "$DATABASE_URL" --no-owner --no-privileges -Fc -f "$BACKUP_FILE"

echo "Backup saved as ${BACKUP_FILE}."
