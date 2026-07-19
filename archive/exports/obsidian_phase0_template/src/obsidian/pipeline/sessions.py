"""America/New_York session derivation for OBSIDIAN Phase 0."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SessionFlags:
    ny_timestamp: pd.Timestamp
    ny_date: str
    ny_time: str
    ny_hour: int
    ny_session_label: str
    is_london_killzone: bool
    is_ny_am_killzone: bool
    is_silver_bullet: bool
    is_london_close: bool
    is_asia_session: bool


def derive_new_york_session(timestamp_utc: Any) -> SessionFlags:
    utc_timestamp = pd.Timestamp(timestamp_utc)
    if utc_timestamp.tzinfo is None:
        raise ValueError("timestamp_utc must be timezone-aware")
    ny_timestamp = utc_timestamp.tz_convert(NEW_YORK)
    minute_of_day = int(ny_timestamp.hour) * 60 + int(ny_timestamp.minute)
    london_killzone = _in_minutes(minute_of_day, "02:00", "05:00")
    ny_am_killzone = _in_minutes(minute_of_day, "08:30", "11:00")
    silver_bullet = _in_minutes(minute_of_day, "10:00", "11:00")
    london_close = _in_minutes(minute_of_day, "10:00", "12:00")
    asia_session = _in_minutes(minute_of_day, "18:00", "24:00")
    label = _session_label(
        is_london_killzone=london_killzone,
        is_ny_am_killzone=ny_am_killzone,
        is_silver_bullet=silver_bullet,
        is_london_close=london_close,
        is_asia_session=asia_session,
    )
    return SessionFlags(
        ny_timestamp=ny_timestamp,
        ny_date=ny_timestamp.date().isoformat(),
        ny_time=ny_timestamp.strftime("%H:%M:%S"),
        ny_hour=int(ny_timestamp.hour),
        ny_session_label=label,
        is_london_killzone=london_killzone,
        is_ny_am_killzone=ny_am_killzone,
        is_silver_bullet=silver_bullet,
        is_london_close=london_close,
        is_asia_session=asia_session,
    )


def add_new_york_session_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if "timestamp_utc" not in frame.columns:
        raise ValueError("Frame requires timestamp_utc")
    result = frame.copy()
    timestamps = pd.to_datetime(result["timestamp_utc"], utc=True)
    ny = timestamps.dt.tz_convert(NEW_YORK)
    minute_of_day = (ny.dt.hour * 60) + ny.dt.minute
    result["ny_timestamp"] = ny
    result["ny_date"] = ny.dt.date.astype(str)
    result["ny_time"] = ny.dt.strftime("%H:%M:%S")
    result["ny_hour"] = ny.dt.hour.astype("int64")
    result["is_london_killzone"] = _between_series(minute_of_day, "02:00", "05:00")
    result["is_ny_am_killzone"] = _between_series(minute_of_day, "08:30", "11:00")
    result["is_silver_bullet"] = _between_series(minute_of_day, "10:00", "11:00")
    result["is_london_close"] = _between_series(minute_of_day, "10:00", "12:00")
    result["is_asia_session"] = _between_series(minute_of_day, "18:00", "24:00")
    result["ny_session_label"] = [
        _session_label(
            is_london_killzone=bool(london),
            is_ny_am_killzone=bool(ny_am),
            is_silver_bullet=bool(silver),
            is_london_close=bool(close),
            is_asia_session=bool(asia),
        )
        for london, ny_am, silver, close, asia in zip(
            result["is_london_killzone"],
            result["is_ny_am_killzone"],
            result["is_silver_bullet"],
            result["is_london_close"],
            result["is_asia_session"],
            strict=True,
        )
    ]
    return result


def _session_label(
    *,
    is_london_killzone: bool,
    is_ny_am_killzone: bool,
    is_silver_bullet: bool,
    is_london_close: bool,
    is_asia_session: bool,
) -> str:
    if is_silver_bullet:
        return "silver_bullet"
    if is_ny_am_killzone:
        return "ny_am_killzone"
    if is_london_close:
        return "london_close"
    if is_london_killzone:
        return "london_killzone"
    if is_asia_session:
        return "asia"
    return "other"


def _between_series(values: pd.Series, start: str, end: str) -> pd.Series:
    return (values >= _minutes(start)) & (values < _minutes(end))


def _in_minutes(value: int, start: str, end: str) -> bool:
    return _minutes(start) <= value < _minutes(end)


def _minutes(value: str) -> int:
    if value == "24:00":
        return 24 * 60
    hour, minute = value.split(":", 1)
    return (int(hour) * 60) + int(minute)
