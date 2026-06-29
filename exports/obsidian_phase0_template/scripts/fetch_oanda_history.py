"""Fetch OANDA OHLCV history into the OBSIDIAN Phase 0 market cache."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from obsidian.config import load_config, load_dotenv
from obsidian.pipeline.cache import load_ohlcv, save_ohlcv
from obsidian.pipeline.ingestion import fetch_oanda_history
from obsidian.pipeline.validation import duplicate_timestamp_count


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(args.env_file)
    config = load_config(args.config)
    db_path = args.db or config.db_path

    try:
        raw = fetch_oanda_history(
            config,
            instrument=args.instrument,
            timeframe=args.timeframe,
            years=args.years,
            closed_only=not args.include_incomplete,
        )
        duplicates_before_save = duplicate_timestamp_count(raw)
        rows_saved = save_ohlcv(db_path, raw, instrument=args.instrument, timeframe=args.timeframe)
        stored = load_ohlcv(db_path, instrument=args.instrument, timeframe=args.timeframe)
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 2

    start = stored["timestamp_utc"].min().isoformat() if not stored.empty else "n/a"
    end = stored["timestamp_utc"].max().isoformat() if not stored.empty else "n/a"
    print("OBSIDIAN OANDA history fetch")
    print(f"Instrument: {args.instrument.upper()}")
    print(f"Timeframe: {args.timeframe.upper()}")
    print(f"Rows fetched: {len(raw)}")
    print(f"Rows saved: {rows_saved}")
    print(f"Rows stored: {len(stored)}")
    print(f"Start UTC: {start}")
    print(f"End UTC: {end}")
    print(f"Duplicates before save: {duplicates_before_save}")
    print(f"Output DB: {db_path}")
    print("Orders sent: no")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch OANDA OHLCV history into OBSIDIAN cache.")
    parser.add_argument("--instrument", default="XAU_USD")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--years", type=float, default=1.0)
    parser.add_argument("--db", type=Path, default=None, help="SQLite cache path.")
    parser.add_argument("--config", type=Path, default=None, help="Optional JSON/YAML OBSIDIAN config.")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--include-incomplete", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
