"""Inspect an OBSIDIAN Phase 0 SQLite OHLCV cache."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from obsidian.config import load_config
from obsidian.pipeline.cache import load_ohlcv
from obsidian.pipeline.validation import validate_ohlcv


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    db_path = args.db or config.db_path
    frame = load_ohlcv(db_path, instrument=args.instrument, timeframe=args.timeframe)
    report = validate_ohlcv(frame, timeframe=args.timeframe)

    start = frame["timestamp_utc"].min().isoformat() if not frame.empty else "n/a"
    end = frame["timestamp_utc"].max().isoformat() if not frame.empty else "n/a"
    latest_age = "n/a" if report["latest_candle_age"] is None else str(report["latest_candle_age"])
    print(f"instrument: {args.instrument.upper()}")
    print(f"timeframe: {args.timeframe.upper()}")
    print(f"rows: {report['rows']}")
    print(f"start UTC: {start}")
    print(f"end UTC: {end}")
    print(f"latest candle age: {latest_age}")
    print(f"duplicates: {report['duplicates']}")
    print(f"gap count: {report['gap_count']}")
    print(f"max gap: {report['max_gap']}")
    print(f"invalid OHLC rows: {report['invalid_ohlc_rows']}")
    print(f"complete candles: {report['complete_candles']}")
    print(f"incomplete candles: {report['incomplete_candles']}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect OBSIDIAN SQLite OHLCV cache.")
    parser.add_argument("--instrument", default="XAU_USD")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--db", type=Path, default=None, help="SQLite cache path.")
    parser.add_argument("--config", type=Path, default=None, help="Optional JSON/YAML OBSIDIAN config.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
