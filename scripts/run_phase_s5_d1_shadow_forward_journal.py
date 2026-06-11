"""Run the AURUM-1 Phase S5 D1 shadow forward journal."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.reports.phase_s5_d1_shadow_forward_journal import (
    parse_args,
    print_phase_s5_report,
    run_phase_s5_journal,
)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_phase_s5_journal(
        args.shadow_db,
        args.report_dir,
        update_outcomes=args.update_outcomes,
        dry_run=args.dry_run,
        max_holding_candles=args.max_holding_candles,
        as_of=args.as_of,
    )
    print_phase_s5_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
