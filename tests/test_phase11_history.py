from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from aurum1.data.ingestion import AurumDataIngestor, initialize_database, load_ohlcv, load_settings
from scripts.data import audit_market_cache, fetch_oanda_history


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "aurum1" / "config" / "settings.yaml"


def _settings(runtime_db: Path) -> dict:
    settings = load_settings(SETTINGS_PATH)
    settings["data"]["db_path"] = str(runtime_db)
    settings["data"]["retry"] = {"attempts": 1, "base_delay_seconds": 0.0, "max_delay_seconds": 0.0}
    return settings


def _fixture_candles(source: str = "oanda", instrument: str = "XAU_USD") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:15:00Z",
                    "2026-01-01T00:15:00Z",
                ],
                utc=True,
            ),
            "open": [2300.0, 2301.0, 2301.5],
            "high": [2302.0, 2303.0, 2303.5],
            "low": [2299.0, 2300.0, 2300.5],
            "close": [2301.0, 2302.0, 2302.5],
            "volume": [1.0, 1.0, 1.0],
            "source": [source, source, source],
            "instrument": [instrument, instrument, instrument],
        }
    )


def test_fetch_history_writes_only_market_cache(tmp_path: Path, monkeypatch) -> None:
    runtime_db = tmp_path / "runtime.sqlite3"
    market_db = tmp_path / "market.sqlite3"
    initialize_database(runtime_db)
    settings = _settings(runtime_db)
    monkeypatch.setenv("OANDA_API_KEY", "test-key")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "test-account")
    monkeypatch.setattr(AurumDataIngestor, "fetch_ohlcv_range", lambda self, timeframe, start, end: _fixture_candles())

    result = fetch_oanda_history.fetch_oanda_history(
        settings,
        timeframe="M15",
        years=0.01,
        output_db=market_db,
        end=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert result["rows_stored"] == 2
    assert len(load_ohlcv("M15", market_db)) == 2
    with closing(sqlite3.connect(runtime_db)) as conn:
        runtime_rows = conn.execute("SELECT COUNT(*) FROM ohlcv_M15").fetchone()[0]
    assert runtime_rows == 0


def test_fetch_history_rejects_missing_oanda_credentials(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path / "runtime.sqlite3")
    monkeypatch.delenv("OANDA_API_KEY", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)

    try:
        fetch_oanda_history.fetch_oanda_history(settings, output_db=tmp_path / "market.sqlite3")
    except RuntimeError as exc:
        assert "Missing required OANDA environment variables" in str(exc)
    else:
        raise AssertionError("Expected missing OANDA credentials to fail loudly")


def test_fetch_history_deduplicates_candles(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path / "runtime.sqlite3")
    monkeypatch.setenv("OANDA_API_KEY", "test-key")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "test-account")
    monkeypatch.setattr(AurumDataIngestor, "fetch_ohlcv_range", lambda self, timeframe, start, end: _fixture_candles())

    result = fetch_oanda_history.fetch_oanda_history(
        settings,
        output_db=tmp_path / "market.sqlite3",
        end=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert result["duplicates_removed"] == 1
    assert result["rows_stored"] == 2


def test_fetch_history_requires_real_oanda_source(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path / "runtime.sqlite3")
    monkeypatch.setenv("OANDA_API_KEY", "test-key")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "test-account")
    monkeypatch.setattr(
        AurumDataIngestor,
        "fetch_ohlcv_range",
        lambda self, timeframe, start, end: _fixture_candles(source="yfinance", instrument="GC=F"),
    )

    try:
        fetch_oanda_history.fetch_oanda_history(
            settings,
            output_db=tmp_path / "market.sqlite3",
            end=datetime(2026, 1, 2, tzinfo=UTC),
        )
    except RuntimeError as exc:
        assert "real OANDA XAU_USD" in str(exc)
    else:
        raise AssertionError("Expected non-OANDA/proxy data to be rejected")


def test_audit_market_cache_reports_readiness(tmp_path: Path) -> None:
    db_path = tmp_path / "market.sqlite3"
    ingestor = AurumDataIngestor(_settings(db_path))
    frame = _fixture_candles().drop_duplicates(subset=["timestamp"])
    ingestor.persist_ohlcv("M15", frame)

    report = audit_market_cache.audit_market_cache(db_path, min_bars=2, min_days=0.0)

    assert report["source"] == "oanda"
    assert report["instrument"] == "XAU_USD"
    assert report["duplicate_timestamps"] == 0
    assert report["readiness_eligible"] is True
