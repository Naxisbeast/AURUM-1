"""UTC timestamp and timeframe utilities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd


TIMEFRAME_DELTAS: dict[str, timedelta] = {
    "M1": timedelta(minutes=1),
    "M5": timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "M30": timedelta(minutes=30),
    "H1": timedelta(hours=1),
    "H4": timedelta(hours=4),
    "D1": timedelta(days=1),
}


def timeframe_delta(timeframe: str) -> timedelta:
    normalized = timeframe.upper()
    try:
        return TIMEFRAME_DELTAS[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe {timeframe!r}") from exc


def parse_utc_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError(f"Timestamp must be timezone-aware UTC: {value!r}")
    return timestamp.tz_convert("UTC")


def canonical_utc_iso(value: Any) -> str:
    timestamp = parse_utc_timestamp(value)
    return timestamp.isoformat().replace("+00:00", "Z")


def utc_series(values: Any) -> pd.Series:
    return pd.to_datetime(values, utc=True)


def now_utc() -> datetime:
    return datetime.now(UTC)
