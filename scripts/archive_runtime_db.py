"""Archive the runtime SQLite DB without deleting or cleaning it.

This is intentionally non-destructive. It creates a timestamped copy under
``backups/`` so polluted runtime state can be inspected before any cleanup is
approved.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    db_path = root / "aurum1" / "data" / "aurum1.sqlite3"
    backup_dir = root / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        print(f"Runtime DB not found: {db_path}")
        return 1
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    destination = backup_dir / f"aurum1_runtime_archive_{stamp}.sqlite3"
    shutil.copy2(db_path, destination)
    print(f"Archived runtime DB to: {destination}")
    print("No cleanup or deletion was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
