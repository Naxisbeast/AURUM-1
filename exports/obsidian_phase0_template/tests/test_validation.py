from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from obsidian.pipeline.validation import (
    incomplete_candle_count,
    invalid_ohlc_count,
    missing_timeframe_gaps,
    non_utc_timestamp_count,
    validate_ohlcv,
)


def test_ohlc_validity_and_incomplete_candle_detection() -> None:
    frame = pd.DataFrame(
        {
            "timestamp_utc": ["2026-01-01T00:00:00Z", "2026-01-01T00:15:00Z"],
            "open": [10.0, 10.0],
            "high": [11.0, 9.0],
            "low": [9.0, 8.0],
            "close": [10.5, 10.5],
            "volume": [1.0, 1.0],
            "complete": [True, False],
        }
    )

    assert invalid_ohlc_count(frame) == 1
    assert incomplete_candle_count(frame) == 1


def test_m15_gap_detection_counts_missing_candles() -> None:
    frame = pd.DataFrame(
        {
            "timestamp_utc": [
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:15:00Z",
                "2026-01-01T00:45:00Z",
            ],
            "open": [1.0, 1.0, 1.0],
            "high": [2.0, 2.0, 2.0],
            "low": [0.5, 0.5, 0.5],
            "close": [1.5, 1.5, 1.5],
            "volume": [1.0, 1.0, 1.0],
            "complete": [True, True, True],
        }
    )

    gaps = missing_timeframe_gaps(frame, timeframe="M15")

    assert len(gaps) == 1
    assert int(gaps.loc[0, "missing_candles"]) == 1
    assert gaps.loc[0, "gap"] == pd.Timedelta(minutes=30)


def test_non_utc_and_sorted_validation_report() -> None:
    frame = pd.DataFrame(
        {
            "timestamp_utc": [
                "2026-01-01T00:15:00Z",
                "2026-01-01T02:00:00+02:00",
                "2026-01-01T00:00:00",
            ],
            "open": [1.0, 1.0, 1.0],
            "high": [2.0, 2.0, 2.0],
            "low": [0.5, 0.5, 0.5],
            "close": [1.5, 1.5, 1.5],
            "volume": [1.0, 1.0, 1.0],
            "complete": [True, True, True],
        }
    )

    report = validate_ohlcv(frame, timeframe="M15", now_utc="2026-01-01T01:00:00Z")

    assert non_utc_timestamp_count(frame) == 2
    assert report["duplicates"] == 1
    assert report["sorted_timestamps"] is False
    assert report["stale_latest_candle"] is True
