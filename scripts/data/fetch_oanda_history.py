"""Fetch real OANDA XAU_USD history into the dedicated market-cache DB.

This script is data-only. It never creates brokers and never places orders.
It also refuses to write to the runtime paper/live SQLite database.
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import AurumDataIngestor, load_ohlcv, load_settings

DEFAULT_OUTPUT_DB = ROOT / "aurum1" / "data" / "backtest_market_cache.sqlite3"
RUNTIME_DB = ROOT / "aurum1" / "data" / "aurum1.sqlite3"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args(argv)
    load_dotenv(ROOT / ".env")
    settings = load_settings(args.settings)
    try:
        result = fetch_oanda_history(
            settings,
            timeframe=args.timeframe,
            years=args.years,
            output_db=args.output_db,
        )
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 2

    print("AURUM-1 OANDA history fetch")
    print(f"Instrument: {result['instrument']}")
    print(f"Source: {result['source']}")
    print(f"Timeframe: {result['timeframe']}")
    print(f"Start: {result['start']}")
    print(f"End: {result['end']}")
    print(f"Rows fetched: {result['rows_fetched']}")
    print(f"Rows stored: {result['rows_stored']}")
    print(f"Duplicates removed: {result['duplicates_removed']}")
    print(f"Output DB: {result['output_db']}")
    print("Orders sent: no")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch real OANDA XAU_USD M15 history into the market cache.")
    parser.add_argument("--settings", type=Path, default=ROOT / "aurum1" / "config" / "settings.yaml")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--years", type=float, default=2.0)
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    return parser.parse_args(argv)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def fetch_oanda_history(
    settings: dict[str, Any],
    *,
    timeframe: str = "M15",
    years: float = 2.0,
    output_db: str | Path = DEFAULT_OUTPUT_DB,
    end: datetime | None = None,
) -> dict[str, Any]:
    normalized_timeframe = timeframe.upper()
    output_path = Path(output_db)
    _assert_not_runtime_db(settings, output_path)
    _require_oanda_credentials(settings)
    fetch_settings = _history_settings(settings, output_path)
    end_utc = end.astimezone(UTC) if end is not None else datetime.now(UTC)
    start_utc = end_utc - timedelta(days=365.25 * float(years))

    ingestor = AurumDataIngestor(fetch_settings)
    raw = ingestor.fetch_ohlcv_range(normalized_timeframe, start_utc, end_utc)
    _assert_real_oanda_xauusd(raw)
    deduped, duplicates_removed = deduplicate_candles(raw)
    ingestor.persist_ohlcv(normalized_timeframe, deduped)

    stored = _stored_real_oanda_rows(normalized_timeframe, output_path)
    stored_start = stored.index.min().isoformat() if not stored.empty else "n/a"
    stored_end = stored.index.max().isoformat() if not stored.empty else "n/a"
    return {
        "instrument": "XAU_USD",
        "source": "oanda",
        "timeframe": normalized_timeframe,
        "start": stored_start,
        "end": stored_end,
        "rows_fetched": int(len(raw)),
        "rows_stored": int(len(stored)),
        "duplicates_removed": int(duplicates_removed),
        "output_db": str(output_path),
    }


def deduplicate_candles(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if frame.empty:
        return frame.copy(), 0
    working = frame.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"], utc=True)
    before = len(working)
    working = working.sort_values("timestamp").drop_duplicates(subset=["timestamp", "instrument"], keep="last")
    return working.reset_index(drop=True), before - len(working)


def _history_settings(settings: dict[str, Any], output_db: Path) -> dict[str, Any]:
    copied = copy.deepcopy(settings)
    copied.setdefault("data", {})
    copied.setdefault("broker", {}).setdefault("oanda", {})
    copied["data"]["db_path"] = str(output_db)
    copied["broker"]["oanda"]["instrument"] = "XAU_USD"
    environment_env = str(copied["broker"]["oanda"].get("environment_env", "OANDA_ENV"))
    os.environ[environment_env] = "practice"
    copied["broker"]["oanda"]["default_environment"] = "practice"
    return copied


def _require_oanda_credentials(settings: dict[str, Any]) -> None:
    oanda = settings.get("broker", {}).get("oanda", {})
    missing = [
        env_name
        for env_name in (
            str(oanda.get("api_key_env", "OANDA_API_KEY")),
            str(oanda.get("account_id_env", "OANDA_ACCOUNT_ID")),
        )
        if not os.getenv(env_name)
    ]
    if missing:
        raise RuntimeError("Missing required OANDA environment variables: " + ", ".join(missing))


def _assert_not_runtime_db(settings: dict[str, Any], output_db: Path) -> None:
    configured_runtime = Path(str(settings.get("data", {}).get("db_path", RUNTIME_DB))).resolve()
    output_path = output_db.resolve()
    if output_path == configured_runtime or output_path == RUNTIME_DB.resolve():
        raise RuntimeError(f"Refusing to write OANDA history to runtime DB: {output_db}")


def _assert_real_oanda_xauusd(frame: pd.DataFrame) -> None:
    if frame.empty:
        raise RuntimeError("OANDA returned no candles")
    source_ok = set(frame["source"].astype(str).str.lower()) == {"oanda"}
    instrument_ok = set(frame["instrument"].astype(str)) == {"XAU_USD"}
    if not source_ok or not instrument_ok:
        raise RuntimeError("History fetch requires real OANDA XAU_USD candles only")


def _stored_real_oanda_rows(timeframe: str, output_db: Path) -> pd.DataFrame:
    frame = load_ohlcv(timeframe, output_db)
    if frame.empty:
        return frame
    frame = frame[frame["source"].astype(str).str.lower().eq("oanda")]
    frame = frame[frame["instrument"].astype(str).eq("XAU_USD")]
    return frame.sort_index()


if __name__ == "__main__":
    raise SystemExit(main())
