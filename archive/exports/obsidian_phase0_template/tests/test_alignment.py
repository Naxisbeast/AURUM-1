from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from obsidian.pipeline.alignment import align_higher_timeframe, assert_no_future_htf_leakage, resample_ohlcv


def test_multi_timeframe_alignment_uses_latest_closed_htf_candle() -> None:
    base = pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime(
                ["2026-01-01T10:15:00Z", "2026-01-01T10:45:00Z", "2026-01-01T11:15:00Z"],
                utc=True,
            ),
            "close": [1.0, 2.0, 3.0],
        }
    )
    htf = pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime(["2026-01-01T10:00:00Z", "2026-01-01T11:00:00Z"], utc=True),
            "open": [100.0, 110.0],
            "high": [105.0, 115.0],
            "low": [95.0, 105.0],
            "close": [101.0, 111.0],
            "volume": [10.0, 11.0],
        }
    )

    aligned = align_higher_timeframe(base, htf, prefix="H1_")

    assert aligned.loc[0, "H1_close"] == 101.0
    assert aligned.loc[1, "H1_close"] == 101.0
    assert aligned.loc[2, "H1_close"] == 111.0
    assert_no_future_htf_leakage(aligned, htf_timestamp_column="H1_timestamp_utc")


def test_no_future_htf_candle_leakage_assertion_raises() -> None:
    aligned = pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime(["2026-01-01T10:15:00Z"], utc=True),
            "H1_timestamp_utc": pd.to_datetime(["2026-01-01T11:00:00Z"], utc=True),
        }
    )

    with pytest.raises(AssertionError):
        assert_no_future_htf_leakage(aligned, htf_timestamp_column="H1_timestamp_utc")


def test_resample_ohlcv_labels_higher_timeframe_by_close_time() -> None:
    frame = pd.DataFrame(
        {
            "timestamp_utc": pd.date_range("2026-01-01T00:15:00Z", periods=4, freq="15min"),
            "open": [1.0, 2.0, 3.0, 4.0],
            "high": [2.0, 3.0, 4.0, 5.0],
            "low": [0.5, 1.5, 2.5, 3.5],
            "close": [1.5, 2.5, 3.5, 4.5],
            "volume": [10.0, 20.0, 30.0, 40.0],
        }
    )

    htf = resample_ohlcv(frame, "1h")

    assert len(htf) == 1
    assert htf.loc[0, "timestamp_utc"] == pd.Timestamp("2026-01-01T01:00:00Z")
    assert htf.loc[0, "open"] == 1.0
    assert htf.loc[0, "close"] == 4.5
    assert htf.loc[0, "volume"] == 100.0
