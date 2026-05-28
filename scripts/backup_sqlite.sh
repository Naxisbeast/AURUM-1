#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-/opt/aurum1}"
DB_PATH="${ROOT_DIR}/aurum1/data/aurum1.sqlite3"
BACKUP_DIR="${ROOT_DIR}/backups"

mkdir -p "${BACKUP_DIR}"

if [[ ! -f "${DB_PATH}" ]]; then
  echo "Runtime DB not found: ${DB_PATH}" >&2
  exit 1
fi

STAMP="$(date -u +%Y%m%d_%H%M%S)"
sqlite3 "${DB_PATH}" ".backup '${BACKUP_DIR}/aurum1_${STAMP}.sqlite3'"
echo "Created backup: ${BACKUP_DIR}/aurum1_${STAMP}.sqlite3"
