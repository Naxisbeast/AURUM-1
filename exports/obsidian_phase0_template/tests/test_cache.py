from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from obsidian.pipeline.cache import load_ohlcv, save_ohlcv, table_name
from obsidian.pipeline.ingestion import normalize_oanda_candles
from obsidian.utils.time import canonical_utc_iso, parse_utc_timestamp


def _fixture() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp_utc": [
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:15:00Z",
                "2026-01-01T00:15:00Z",
            ],
            "open": [2300.0, 2301.0, 2301.5],
            "high": [2302.0, 2303.0, 2303.5],
            "low": [2299.0, 2300.0, 2300.5],
            "close": [2301.0, 2302.0, 2302.5],
            "volume": [10.0, 11.0, 12.0],
            "complete": [True, True, True],
            "instrument": ["XAU_USD", "XAU_USD", "XAU_USD"],
            "timeframe": ["M15", "M15", "M15"],
        }
    )


def test_sqlite_save_load_and_duplicate_handling(tmp_path: Path) -> None:
    db_path = tmp_path / "obsidian.sqlite3"

    rows_saved = save_ohlcv(db_path, _fixture(), instrument="XAU_USD", timeframe="M15")
    loaded = load_ohlcv(db_path, instrument="XAU_USD", timeframe="M15")

    assert rows_saved == 2
    assert len(loaded) == 2
    assert loaded.loc[1, "close"] == 2302.5
    assert str(loaded["timestamp_utc"].dt.tz) == "UTC"
    assert loaded["timestamp_utc"].is_monotonic_increasing
    assert table_name("XAU_USD", "M15") == "ohlcv_XAU_USD_M15"


def test_utc_parsing_requires_timezone_and_canonicalizes_offsets() -> None:
    assert canonical_utc_iso("2026-01-01T02:00:00+02:00") == "2026-01-01T00:00:00Z"

    with pytest.raises(ValueError):
        parse_utc_timestamp("2026-01-01T00:00:00")


def test_oanda_normalization_filters_incomplete_closed_candles() -> None:
    payload = {
        "candles": [
            {
                "complete": True,
                "time": "2026-01-01T00:00:00.000000000Z",
                "volume": 10,
                "mid": {"o": "2300.0", "h": "2302.0", "l": "2299.0", "c": "2301.0"},
            },
            {
                "complete": False,
                "time": "2026-01-01T00:15:00.000000000Z",
                "volume": 11,
                "mid": {"o": "2301.0", "h": "2303.0", "l": "2300.0", "c": "2302.0"},
            },
        ]
    }

    closed = normalize_oanda_candles(payload, instrument="XAU_USD", timeframe="M15", closed_only=True)
    all_rows = normalize_oanda_candles(payload, instrument="XAU_USD", timeframe="M15", closed_only=False)

    assert len(closed) == 1
    assert len(all_rows) == 2
    assert all_rows["complete"].tolist() == [True, False]
