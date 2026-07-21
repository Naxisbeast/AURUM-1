"""Audit the dedicated AURUM-1 market-data cache for readiness."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import TIMEFRAME_DELTAS, load_ohlcv

MIN_HISTORY_BARS = 20000
MIN_HISTORY_DAYS = 250.0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_market_cache(
        args.db,
        timeframe=args.timeframe,
        min_bars=args.min_bars,
        min_days=args.min_days,
    )
    print_audit_report(report)
    return 0 if report["readiness_eligible"] else 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit AURUM-1 market-cache readiness.")
    parser.add_argument("--db", type=Path, default=ROOT / "aurum1" / "data" / "backtest_market_cache.sqlite3")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--min-bars", type=int, default=MIN_HISTORY_BARS)
    parser.add_argument("--min-days", type=float, default=MIN_HISTORY_DAYS)
    return parser.parse_args(argv)


def audit_market_cache(
    db_path: str | Path,
    *,
    timeframe: str = "M15",
    min_bars: int = MIN_HISTORY_BARS,
    min_days: float = MIN_HISTORY_DAYS,
) -> dict[str, Any]:
    path = Path(db_path)
    normalized_timeframe = timeframe.upper()
    table = f"ohlcv_{normalized_timeframe}"
    if not path.exists():
        return _empty_report(path, "database_not_found", min_bars, min_days)
    try:
        frame = load_ohlcv(normalized_timeframe, path)
    except Exception as exc:
        report = _empty_report(path, f"load_failed: {exc}", min_bars, min_days)
        return report

    duplicate_timestamps = _duplicate_timestamp_count(path, table)
    if frame.empty:
        return _empty_report(path, "empty", min_bars, min_days, duplicate_timestamps=duplicate_timestamps)

    sources = sorted(set(frame["source"].astype(str).str.lower()))
    instruments = sorted(set(frame["instrument"].astype(str)))
    rows = int(len(frame))
    start = pd.Timestamp(frame.index.min())
    end = pd.Timestamp(frame.index.max())
    calendar_days = max(0.0, float((end - start).total_seconds() / 86400.0))
    missing_gaps = _missing_gap_count(frame.index, normalized_timeframe)
    source_ok = sources == ["oanda"]
    instrument_ok = instruments == ["XAU_USD"]
    meets_minimum_bars = rows >= int(min_bars)
    meets_minimum_days = calendar_days >= float(min_days)
    readiness_eligible = (
        source_ok
        and instrument_ok
        and duplicate_timestamps == 0
        and meets_minimum_bars
        and meets_minimum_days
    )
    return {
        "db_path": str(path),
        "timeframe": normalized_timeframe,
        "instrument": "XAU_USD" if instrument_ok else ",".join(instruments),
        "source": "oanda" if source_ok else ",".join(sources),
        "rows": rows,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "calendar_days": calendar_days,
        "duplicate_timestamps": duplicate_timestamps,
        "missing_candle_gaps": missing_gaps,
        "meets_minimum_bars": meets_minimum_bars,
        "meets_minimum_days": meets_minimum_days,
        "readiness_eligible": readiness_eligible,
        "reason": "",
    }


def print_audit_report(report: dict[str, Any]) -> None:
    print(f"Instrument: {report['instrument']}")
    print(f"Source: {report['source']}")
    print(f"Rows: {report['rows']}")
    print(f"Start: {report['start']}")
    print(f"End: {report['end']}")
    print(f"Calendar days: {report['calendar_days']:.1f}")
    print(f"Duplicate timestamps: {report['duplicate_timestamps']}")
    print(f"Missing candle gaps: {report['missing_candle_gaps']}")
    print(f"Meets minimum bars: {_yes_no(report['meets_minimum_bars'])}")
    print(f"Meets minimum days: {_yes_no(report['meets_minimum_days'])}")
    print(f"Readiness eligible: {_yes_no(report['readiness_eligible'])}")
    if report.get("reason"):
        print(f"Reason: {report['reason']}")


def _duplicate_timestamp_count(db_path: Path, table: str) -> int:
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            rows = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM (
                    SELECT timestamp
                    FROM {table}
                    GROUP BY timestamp
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()
            return int(rows[0] if rows else 0)
    except sqlite3.Error:
        return 0


def _missing_gap_count(index: pd.DatetimeIndex, timeframe: str) -> int:
    if len(index) < 2:
        return 0
    expected = TIMEFRAME_DELTAS.get(timeframe)
    if expected is None:
        return 0
    diffs = pd.Series(index).diff().dropna()
    return int((diffs > pd.Timedelta(expected) * 1.5).sum())


def _empty_report(
    db_path: Path,
    reason: str,
    min_bars: int,
    min_days: float,
    *,
    duplicate_timestamps: int = 0,
) -> dict[str, Any]:
    return {
        "db_path": str(db_path),
        "timeframe": "M15",
        "instrument": "none",
        "source": "none",
        "rows": 0,
        "start": "n/a",
        "end": "n/a",
        "calendar_days": 0.0,
        "duplicate_timestamps": duplicate_timestamps,
        "missing_candle_gaps": 0,
        "meets_minimum_bars": False,
        "meets_minimum_days": False,
        "readiness_eligible": False,
        "reason": reason,
        "min_bars": min_bars,
        "min_days": min_days,
    }


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


if __name__ == "__main__":
    raise SystemExit(main())
