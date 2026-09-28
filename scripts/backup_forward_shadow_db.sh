#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-/opt/aurum1}"
DB_PATH="${ROOT_DIR}/reports/forward_shadow/donchian_shadow.sqlite3"
BACKUP_DIR="${ROOT_DIR}/backups/forward_shadow"

mkdir -p "${BACKUP_DIR}"

if [[ ! -f "${DB_PATH}" ]]; then
  echo "Forward shadow DB not found: ${DB_PATH}" >&2
  exit 1
fi

STAMP="$(date -u +%Y%m%d_%H%M%S)"
sqlite3 "${DB_PATH}" ".backup '${BACKUP_DIR}/donchian_shadow_${STAMP}.sqlite3'"
echo "Created backup: ${BACKUP_DIR}/donchian_shadow_${STAMP}.sqlite3"

# Retention: keep only the KEEP newest backups, delete the rest.
# Without this, daily backups accumulate forever and fill the disk
# (this caused a full-disk outage on 2026-09-28).
KEEP="${SHADOW_BACKUP_RETENTION:-28}"
mapfile -t backups < <(ls -1t "${BACKUP_DIR}"/donchian_shadow_*.sqlite3 2>/dev/null)
if (( ${#backups[@]} > KEEP )); then
  for old in "${backups[@]:KEEP}"; do
    rm -f "${old}"
    echo "Pruned old backup: ${old}"
  done
fi
