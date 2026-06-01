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
