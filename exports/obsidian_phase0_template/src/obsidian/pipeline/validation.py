"""OHLCV validation utilities for OBSIDIAN Phase 0."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd

from obsidian.utils.time import parse_utc_timestamp, timeframe_delta


def timestamp_series(frame: pd.DataFrame) -> pd.Series:
    if "timestamp_utc" in frame.columns:
        return frame["timestamp_utc"]
    if isinstance(frame.index, pd.DatetimeIndex):
        return pd.Series(frame.index, index=frame.index)
    raise ValueError("Frame requires timestamp_utc column or DatetimeIndex")


def parsed_timestamps_utc(frame: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(timestamp_series(frame), utc=True, format="mixed")


def duplicate_timestamp_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    timestamps = parsed_timestamps_utc(frame)
    return int(timestamps.duplicated().sum())


def non_utc_timestamp_count(frame: pd.DataFrame) -> int:
    count = 0
    for value in timestamp_series(frame):
        try:
            timestamp = pd.Timestamp(value)
        except Exception:
            count += 1
            continue
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            count += 1
    return count


def sorted_timestamps(frame: pd.DataFrame) -> bool:
    if frame.empty:
        return True
    timestamps = parsed_timestamps_utc(frame)
    return bool(timestamps.is_monotonic_increasing)


def invalid_ohlc_mask(frame: pd.DataFrame) -> pd.Series:
    required = ["open", "high", "low", "close", "volume"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing OHLCV columns: {missing}")
    numeric = frame[required].apply(pd.to_numeric, errors="coerce")
    finite = numeric.notna().all(axis=1)
    high = numeric["high"]
    low = numeric["low"]
    open_ = numeric["open"]
    close = numeric["close"]
    volume = numeric["volume"]
    valid = (
        finite
        & (high >= low)
        & (high >= open_)
        & (high >= close)
        & (low <= open_)
        & (low <= close)
        & (volume >= 0)
    )
    return ~valid


def invalid_ohlc_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    return int(invalid_ohlc_mask(frame).sum())


def incomplete_candle_count(frame: pd.DataFrame) -> int:
    if "complete" not in frame.columns or frame.empty:
        return 0
    return int((~frame["complete"].astype(bool)).sum())


def missing_timeframe_gaps(frame: pd.DataFrame, *, timeframe: str = "M15") -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["previous_timestamp_utc", "timestamp_utc", "gap", "missing_candles"])
    delta = pd.Timedelta(timeframe_delta(timeframe))
    timestamps = parsed_timestamps_utc(frame).drop_duplicates().sort_values().reset_index(drop=True)
    rows: list[dict[str, Any]] = []
    for index in range(1, len(timestamps)):
        previous = timestamps.iloc[index - 1]
        current = timestamps.iloc[index]
        gap = current - previous
        if gap > delta:
            missing = max(0, int(round(gap / delta)) - 1)
            rows.append(
                {
                    "previous_timestamp_utc": previous,
                    "timestamp_utc": current,
                    "gap": gap,
                    "missing_candles": missing,
                }
            )
    return pd.DataFrame(rows, columns=["previous_timestamp_utc", "timestamp_utc", "gap", "missing_candles"])


def latest_candle_age(frame: pd.DataFrame, *, now_utc: Any | None = None) -> pd.Timedelta | None:
    if frame.empty:
        return None
    now = parse_utc_timestamp(now_utc) if now_utc is not None else pd.Timestamp.now(tz="UTC")
    latest = parsed_timestamps_utc(frame).max()
    return pd.Timestamp(now) - pd.Timestamp(latest)


def stale_latest_candle(
    frame: pd.DataFrame,
    *,
    timeframe: str = "M15",
    now_utc: Any | None = None,
    stale_after: timedelta | None = None,
) -> bool:
    age = latest_candle_age(frame, now_utc=now_utc)
    if age is None:
        return True
    threshold = pd.Timedelta(stale_after or timeframe_delta(timeframe) * 2)
    return bool(age > threshold)


def validate_ohlcv(
    frame: pd.DataFrame,
    *,
    timeframe: str = "M15",
    now_utc: Any | None = None,
) -> dict[str, Any]:
    gaps = missing_timeframe_gaps(frame, timeframe=timeframe)
    age = latest_candle_age(frame, now_utc=now_utc)
    max_gap = gaps["gap"].max() if not gaps.empty else pd.Timedelta(0)
    return {
        "rows": int(len(frame)),
        "duplicates": duplicate_timestamp_count(frame),
        "gap_count": int(len(gaps)),
        "max_gap": max_gap,
        "invalid_ohlc_rows": invalid_ohlc_count(frame),
        "incomplete_candles": incomplete_candle_count(frame),
        "complete_candles": int(len(frame) - incomplete_candle_count(frame)),
        "non_utc_timestamps": non_utc_timestamp_count(frame),
        "sorted_timestamps": sorted_timestamps(frame),
        "latest_candle_age": age,
        "stale_latest_candle": stale_latest_candle(frame, timeframe=timeframe, now_utc=now_utc),
    }
