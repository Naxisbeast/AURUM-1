"""Run the AURUM-1 Phase S3 candidate filter shadow replay."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.reports.phase_s3_candidate_filter_shadow_replay import (
    parse_args,
    print_phase_s3_report,
    run_phase_s3_replay,
)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_phase_s3_replay(args.shadow_db, args.report_dir, as_of=args.as_of)
    print_phase_s3_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
