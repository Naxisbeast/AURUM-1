"""Backfill the trial ledger with historical D4 walk-forward trials.

The trial ledger (`aurum1/data/trial_ledger.sqlite3`) was built during the
audit hardening (see `docs/system/AUDIT_DECISIONS.md` Decision 6) but was
never populated — no research run ever called `log_trial()`. The 100-trade
gate's first criterion (DSR >= 0.95) is meaningless without a real trial
pool to deflate against, so this script backfills the apples-to-apples
trials we actually have: the D4 walk-forward runs.

WHAT IS LOGGED (apples-to-apples):
- `d4_walk_forward_L20_local_results.json`  — LOOKBACK=20, 18 windows
- `d4_walk_forward_L20_v2.json`            — LOOKBACK=20, 18 windows (re-run)
- `d4_walk_forward_L55_results.json`       — LOOKBACK=55, 11 windows
- `d4_walk_forward_L55_v2.json`            — LOOKBACK=55, 18 windows (re-run)

These all share the same walk-forward methodology: per-window Sharpe ratios
over non-overlapping test windows on the same backtest cache. The DSR
deflation pool is built from the per-window Sharpe values, which keeps
observation type consistent (per-window, not per-trade).

WHAT IS NOT LOGGED (documented, excluded):
- `phase_s4_candidate_metrics.csv` (D1-D4) — different exits (1R vs 2R),
  BUY-only, filter variants, no clean per-window Sharpe. Mixing units
  would corrupt the DSR.
- `d4_full_backtest_v2.json` — full-sample single Sharpe with a session-aware
  cost model; different aggregation, not a walk-forward trial.

The script is idempotent: it deletes any existing rows for the same
variant_id before inserting, so re-running after a data refresh replaces
rather than duplicates. It is NOT the intended future path — the walk-forward
runner scripts now auto-log (`scripts/backtesting/run_*_walk_forward.py`).
This is a one-time migration for historical runs.

Usage:
    python scripts/gates/backfill_trial_ledger.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.stats import kurtosis, skew

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.research.trial_ledger import log_trial, TrialRecord  # noqa: E402

# (variant_id, relative path to walk-forward result JSON, notes)
TRIAL_SOURCES: list[tuple[str, str, str]] = [
    (
        "D4_walkforward_L20",
        "reports/forward_shadow/d4_walk_forward_L20_local_results.json",
        "Donchian LOOKBACK=20, 2R exit, BUY+SELL, no filters. 18 non-overlapping "
        "windows on local backtest cache. Unannualized per-window Sharpe.",
    ),
    (
        "D4_walkforward_L20_v2",
        "reports/forward_shadow/d4_walk_forward_L20_v2.json",
        "Re-run of D4_walkforward_L20 after data refresh (v2). 18 windows. "
        "Unannualized per-window Sharpe.",
    ),
    (
        "D4_walkforward_L55",
        "reports/forward_shadow/d4_walk_forward_L55_results.json",
        "Donchian LOOKBACK=55, 2R exit, BUY+SELL, no filters. 11 non-overlapping "
        "windows. Unannualized per-window Sharpe.",
    ),
    (
        "D4_walkforward_L55_v2",
        "reports/forward_shadow/d4_walk_forward_L55_v2.json",
        "Re-run of D4_walkforward_L55 after data refresh (v2). 18 windows. "
        "Unannualized per-window Sharpe.",
    ),
]


def load_windows(result_path: Path) -> list[dict]:
    """Load the per-window list from a walk-forward result JSON."""
    with open(result_path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or "windows" not in data:
        raise ValueError(f"{result_path}: expected {{'windows': [...]}} structure")
    return data["windows"]


def build_records() -> list[TrialRecord]:
    """Build the TrialRecords to insert (excluding the logged_at field)."""
    records: list[TrialRecord] = []
    for variant_id, rel_path, notes in TRIAL_SOURCES:
        result_path = ROOT / rel_path
        if not result_path.exists():
            print(f"  !! MISSING {rel_path} — skipping")
            continue
        windows = load_windows(result_path)
        sharpes = np.array([w["sharpe"] for w in windows], dtype=float)
        if sharpes.size < 3:
            print(f"  !! {variant_id}: only {sharpes.size} windows, need >=3 — skipping")
            continue
        records.append(
            TrialRecord(
                variant_id=variant_id,
                parent_family="donchian_breakout",
                n_obs=len(windows),
                sharpe=float(sharpes.mean()),
                skew=float(skew(sharpes)),
                kurtosis=float(kurtosis(sharpes, fisher=False)),  # raw kurtosis
                return_series_path=rel_path,
                notes=notes,
            )
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print records without writing.")
    args = parser.parse_args()

    records = build_records()
    print(f"Built {len(records)} trial record(s) from {len(TRIAL_SOURCES)} source(s).\n")
    for rec in records:
        print(
            f"  {rec.variant_id:<24} n_obs={rec.n_obs:<3} "
            f"sharpe={rec.sharpe:.4f} skew={rec.skew:.4f} kurt={rec.kurtosis:.4f}"
        )
    if not records:
        print("Nothing to log.")
        return

    if args.dry_run:
        print("\n[dry-run] No rows written.")
        return

    # Idempotent: wipe prior rows for the same variant before inserting.
    from aurum1.research.trial_ledger import delete_trial

    for rec in records:
        delete_trial(variant_id=rec.variant_id)
        log_trial(rec)
        print(f"  logged {rec.variant_id}")

    print(f"\nDone. {len(records)} trial(s) logged to the ledger.")


if __name__ == "__main__":
    main()
