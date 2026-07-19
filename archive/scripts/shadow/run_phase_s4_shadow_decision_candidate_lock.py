"""Run the AURUM-1 Phase S4 shadow decision candidate lock."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.reports.phase_s4_shadow_decision_candidate_lock import (
    parse_args,
    print_phase_s4_report,
    run_phase_s4_lock,
)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_phase_s4_lock(args.shadow_db, args.report_dir, as_of=args.as_of)
    print_phase_s4_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
