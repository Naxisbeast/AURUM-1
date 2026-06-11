"""Run the AURUM-1 Phase S1 forward-shadow failure audit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.reports.phase_s1_forward_shadow_failure_audit import (
    DEFAULT_REPORT_DIR,
    DEFAULT_SHADOW_DB,
    print_phase_s1_report,
    run_phase_s1_audit,
)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_phase_s1_audit(args.shadow_db, args.report_dir, as_of=args.as_of)
    print_phase_s1_report(result)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AURUM-1 Phase S1 forward shadow failure audit.")
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--as-of", default=None, help="Optional UTC timestamp to stamp the summary.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
