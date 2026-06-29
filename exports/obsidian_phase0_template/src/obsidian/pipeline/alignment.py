"""Multi-timeframe alignment helpers for OBSIDIAN Phase 0."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd


def frame_with_timestamp_utc(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.copy()
    if "timestamp_utc" not in working.columns:
        if isinstance(working.index, pd.DatetimeIndex):
            working = working.reset_index().rename(columns={working.index.name or "index": "timestamp_utc"})
        else:
            raise ValueError("Frame requires timestamp_utc column or DatetimeIndex")
    working["timestamp_utc"] = pd.to_datetime(working["timestamp_utc"], utc=True)
    return working.sort_values("timestamp_utc").reset_index(drop=True)


def resample_ohlcv(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    working = frame_with_timestamp_utc(frame).set_index("timestamp_utc")
    resampled = (
        working[["open", "high", "low", "close", "volume"]]
        .resample(rule, label="right", closed="right")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )
    return resampled.reset_index()


def align_higher_timeframe(
    base_frame: pd.DataFrame,
    higher_frame: pd.DataFrame,
    *,
    prefix: str = "htf_",
    release_delay: timedelta = timedelta(0),
) -> pd.DataFrame:
    base = frame_with_timestamp_utc(base_frame)
    higher = frame_with_timestamp_utc(higher_frame)
    higher["_available_at"] = higher["timestamp_utc"] + pd.Timedelta(release_delay)
    higher[f"{prefix}timestamp_utc"] = higher["timestamp_utc"]

    payload_columns = [
        column
        for column in higher.columns
        if column not in {"timestamp_utc", "_available_at", "instrument", "timeframe", "complete"}
    ]
    rename_map = {column: f"{prefix}{column}" for column in payload_columns if column != f"{prefix}timestamp_utc"}
    higher = higher.rename(columns=rename_map)
    keep_columns = ["_available_at", f"{prefix}timestamp_utc"] + [
        rename_map.get(column, column)
        for column in payload_columns
        if column != f"{prefix}timestamp_utc"
    ]

    aligned = pd.merge_asof(
        base.sort_values("timestamp_utc"),
        higher[keep_columns].sort_values("_available_at"),
        left_on="timestamp_utc",
        right_on="_available_at",
        direction="backward",
    )
    return aligned.drop(columns=["_available_at"])


def assert_no_future_htf_leakage(
    aligned_frame: pd.DataFrame,
    *,
    htf_timestamp_column: str = "htf_timestamp_utc",
    base_timestamp_column: str = "timestamp_utc",
) -> None:
    if htf_timestamp_column not in aligned_frame.columns:
        raise ValueError(f"Missing HTF timestamp column: {htf_timestamp_column}")
    base = pd.to_datetime(aligned_frame[base_timestamp_column], utc=True)
    htf = pd.to_datetime(aligned_frame[htf_timestamp_column], utc=True)
    mask = htf.notna() & (htf > base)
    if bool(mask.any()):
        first = aligned_frame.loc[mask].iloc[0]
        raise AssertionError(
            "Future HTF leakage detected: "
            f"{htf_timestamp_column}={first[htf_timestamp_column]} "
            f"after {base_timestamp_column}={first[base_timestamp_column]}"
        )
