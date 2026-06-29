"""SQLite OHLCV market-cache helpers for OBSIDIAN Phase 0."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pandas as pd

from obsidian.utils.time import canonical_utc_iso


REQUIRED_OHLCV_COLUMNS = [
    "timestamp_utc",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "complete",
    "instrument",
    "timeframe",
]

OHLCV_NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume"]
_IDENTIFIER_RE = re.compile(r"^[A-Z0-9_]+$")


def normalize_instrument(instrument: str) -> str:
    normalized = instrument.strip().upper()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"Unsupported instrument identifier: {instrument!r}")
    return normalized


def normalize_timeframe(timeframe: str) -> str:
    normalized = timeframe.strip().upper()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"Unsupported timeframe identifier: {timeframe!r}")
    return normalized


def table_name(instrument: str, timeframe: str) -> str:
    return f"ohlcv_{normalize_instrument(instrument)}_{normalize_timeframe(timeframe)}"


def ensure_ohlcv_table(db_path: str | Path, instrument: str = "XAU_USD", timeframe: str = "M15") -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = table_name(instrument, timeframe)
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    timestamp_utc TEXT PRIMARY KEY,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    complete INTEGER NOT NULL CHECK (complete IN (0, 1)),
                    instrument TEXT NOT NULL,
                    timeframe TEXT NOT NULL
                )
                """
            )


def normalize_ohlcv_frame(
    frame: pd.DataFrame,
    *,
    instrument: str = "XAU_USD",
    timeframe: str = "M15",
) -> pd.DataFrame:
    working = frame.copy()
    if "timestamp_utc" not in working.columns:
        if isinstance(working.index, pd.DatetimeIndex):
            working = working.reset_index().rename(columns={working.index.name or "index": "timestamp_utc"})
        elif "timestamp" in working.columns:
            working = working.rename(columns={"timestamp": "timestamp_utc"})
        else:
            raise ValueError("OHLCV frame requires timestamp_utc, timestamp, or a DatetimeIndex")

    normalized_instrument = normalize_instrument(instrument)
    normalized_timeframe = normalize_timeframe(timeframe)
    working["timestamp_utc"] = working["timestamp_utc"].map(canonical_utc_iso)
    working["instrument"] = working.get("instrument", normalized_instrument)
    working["timeframe"] = working.get("timeframe", normalized_timeframe)
    working["instrument"] = working["instrument"].astype(str).str.upper()
    working["timeframe"] = working["timeframe"].astype(str).str.upper()
    if "complete" not in working.columns:
        working["complete"] = True
    working["complete"] = working["complete"].astype(bool)

    for column in OHLCV_NUMERIC_COLUMNS:
        if column not in working.columns:
            raise ValueError(f"OHLCV frame missing required column: {column}")
        working[column] = pd.to_numeric(working[column], errors="raise").astype("float64")

    missing = [column for column in REQUIRED_OHLCV_COLUMNS if column not in working.columns]
    if missing:
        raise ValueError(f"OHLCV frame missing required columns: {missing}")
    return working[REQUIRED_OHLCV_COLUMNS]


def deduplicate_ohlcv(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if frame.empty:
        return frame.copy(), 0
    working = frame.copy()
    before = len(working)
    working = working.sort_values("timestamp_utc").drop_duplicates(
        subset=["timestamp_utc", "instrument", "timeframe"],
        keep="last",
    )
    return working.reset_index(drop=True), before - len(working)


def save_ohlcv(
    db_path: str | Path,
    frame: pd.DataFrame,
    *,
    instrument: str = "XAU_USD",
    timeframe: str = "M15",
) -> int:
    ensure_ohlcv_table(db_path, instrument, timeframe)
    normalized = normalize_ohlcv_frame(frame, instrument=instrument, timeframe=timeframe)
    deduped, _ = deduplicate_ohlcv(normalized)
    records = _records_for_sql(deduped)
    if not records:
        return 0
    table = table_name(instrument, timeframe)
    with closing(sqlite3.connect(Path(db_path))) as conn:
        with conn:
            conn.executemany(
                f"""
                INSERT OR REPLACE INTO {table}
                (timestamp_utc, open, high, low, close, volume, complete, instrument, timeframe)
                VALUES
                (:timestamp_utc, :open, :high, :low, :close, :volume, :complete, :instrument, :timeframe)
                """,
                records,
            )
    return len(records)


def load_ohlcv(
    db_path: str | Path,
    *,
    instrument: str = "XAU_USD",
    timeframe: str = "M15",
) -> pd.DataFrame:
    ensure_ohlcv_table(db_path, instrument, timeframe)
    table = table_name(instrument, timeframe)
    with closing(sqlite3.connect(Path(db_path))) as conn:
        frame = pd.read_sql_query(f"SELECT * FROM {table} ORDER BY timestamp_utc", conn)
    if frame.empty:
        return pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS)

    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True)
    for column in OHLCV_NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
    frame["complete"] = frame["complete"].astype(bool)
    return frame[REQUIRED_OHLCV_COLUMNS].sort_values("timestamp_utc").reset_index(drop=True)


def _records_for_sql(frame: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in frame.to_dict(orient="records"):
        cleaned = dict(record)
        cleaned["timestamp_utc"] = canonical_utc_iso(cleaned["timestamp_utc"])
        cleaned["complete"] = 1 if bool(cleaned["complete"]) else 0
        records.append(cleaned)
    return records
