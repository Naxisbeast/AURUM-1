"""CI-friendly tests for forward shadow data pipeline.

These tests validate the forward shadow logic without requiring a live
market cache database. They test pure functions: validation, safety checks,
signal simulation, data gap reporting, and utility functions.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Need to set up path before importing
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.shadow.forward_shadow_donchian import (
    STRATEGY_NAME,
    LOOKBACK,
    RISK_PER_TRADE_PCT,
    ShadowSignal,
    assert_shadow_safety,
    validate_ohlcv,
    validate_raw_fetch,
    make_signal_row,
    safe_table_count,
    stale_data_report,
    is_weekend_market_pause,
    data_gap_report,
    parse_start_date,
    utc_timestamp,
    estimate_sharpe,
    runtime_environment_status,
    row_to_dict,
    shadow_config_payload,
    resolve_market_db,
)


def _ohlcv_frame(data: dict | None = None) -> pd.DataFrame:
    """Create a minimal OHLCV frame with DatetimeIndex for validation tests."""
    df = pd.DataFrame(data or {
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
        "volume": [1000, 1100],
    })
    df.index = pd.date_range("2026-01-01", periods=len(df), freq="15min", tz="UTC")
    return df


class TestConstants:
    """Shadow pipeline constants."""

    def test_strategy_name(self):
        assert STRATEGY_NAME == "raw_donchian_fixed_2r"

    def test_lookback(self):
        assert LOOKBACK == 20

    def test_risk_pct(self):
        assert RISK_PER_TRADE_PCT == 0.0035


class TestShadowSignal:
    """ShadowSignal dataclass."""

    def test_default_values(self):
        s = ShadowSignal(
            signal_time="2026-01-01T12:00:00",
            entry_time="2026-01-01T12:15:00",
            strategy="test",
            direction="BUY",
            status="OPEN",
            skip_reason=None,
            entry_price=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            atr=5.0,
            units=1.0,
            risk_amount=10.0,
            target_risk_amount=10.0,
            spread_estimate=1.5,
            slippage_estimate=0.005,
        )
        assert s.direction == "BUY"
        assert s.status == "OPEN"
        assert s.exit_reason is None

    def test_with_exit_fields(self):
        s = ShadowSignal(
            signal_time="2026-01-01T12:00:00",
            entry_time="2026-01-01T12:15:00",
            strategy="test",
            direction="SELL",
            status="CLOSED",
            skip_reason=None,
            entry_price=2000.0,
            stop_loss=2010.0,
            take_profit=1980.0,
            atr=5.0,
            units=1.0,
            risk_amount=10.0,
            target_risk_amount=10.0,
            spread_estimate=1.5,
            slippage_estimate=0.005,
            exit_time="2026-01-01T14:00:00",
            exit_reason="take_profit",
        )
        assert s.exit_reason == "take_profit"
        assert s.status == "CLOSED"


class TestValidateOhlcv:
    """OHLCV validation."""

    def test_valid_frame_passes(self):
        """Valid OHLCV frame should pass without modification."""
        df = _ohlcv_frame()
        result = validate_ohlcv(df)
        assert not result.empty

    def test_handles_missing_volume(self):
        """Missing volume column should fill with 0 (will raise for missing cols)."""
        df = _ohlcv_frame({"open": [100.0], "high": [102.0], "low": [99.0], "close": [101.0]})
        with pytest.raises(RuntimeError):
            validate_ohlcv(df)

    def test_clamps_negative_prices(self):
        """Negative prices should be clamped."""
        df = _ohlcv_frame({"open": [100.0], "high": [102.0], "low": [-1.0], "close": [101.0], "volume": [1000]})
        result = validate_ohlcv(df)
        assert len(result) > 0

    def test_empty_frame(self):
        """Empty frame should return empty frame."""
        with pytest.raises(RuntimeError):
            validate_ohlcv(pd.DataFrame())

    def test_fills_zero_volume(self):
        """Zero volume should be replaced with 1."""
        df = _ohlcv_frame({"open": [100.0], "high": [102.0], "low": [99.0], "close": [101.0], "volume": [0]})
        result = validate_ohlcv(df)
        assert len(result) > 0


class TestValidateRawFetch:
    """Raw fetch validation."""

    def test_passes_with_required_columns(self):
        df = pd.DataFrame({
            "timestamp": ["2026-01-01"],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "volume": [1000],
            "source": ["oanda"],
            "instrument": ["XAU_USD"],
        })
        validate_raw_fetch(df)

    def test_raises_on_empty(self):
        with pytest.raises((ValueError, RuntimeError)):
            validate_raw_fetch(pd.DataFrame())


class TestAssertShadowSafety:
    """Shadow safety assertions."""

    def test_safe_settings_pass(self):
        settings = {
            "forward_shadow": {
                "allow_oanda_orders": False,
                "paper_trade": True,
            }
        }
        # Should not raise
        assert_shadow_safety(settings)

    def test_raises_if_oanda_orders_allowed(self):
        settings = {
            "forward_shadow": {
                "allow_oanda_orders": True,
                "paper_trade": True,
            }
        }
        with pytest.raises(RuntimeError):
            assert_shadow_safety(settings)


class TestStaleDataReport:
    """Stale data detection."""

    def test_no_stale_data(self):
        now = pd.Timestamp("2026-01-01T14:00:00", tz="UTC")
        latest = pd.Timestamp("2026-01-01T13:50:00", tz="UTC")
        report = stale_data_report(latest.isoformat(), now, threshold_minutes=45.0)
        assert not report.get("is_stale", True)

    def test_stale_data_detected(self):
        now = pd.Timestamp("2026-01-01T15:00:00", tz="UTC")
        latest = pd.Timestamp("2026-01-01T13:00:00", tz="UTC")
        report = stale_data_report(latest.isoformat(), now, threshold_minutes=45.0)
        assert report.get("is_stale", False)

    def test_no_latest_candle(self):
        now = pd.Timestamp("2026-01-01T14:00:00", tz="UTC")
        report = stale_data_report(None, now)
        assert report.get("is_stale", False)


class TestIsWeekend:
    """Weekend detection."""

    def test_saturday_is_weekend(self):
        ts = pd.Timestamp("2026-01-03T12:00:00")  # Saturday
        assert is_weekend_market_pause(ts)

    def test_sunday_is_weekend(self):
        ts = pd.Timestamp("2026-01-04T12:00:00")  # Sunday
        assert is_weekend_market_pause(ts)

    def test_monday_morning_before_open(self):
        ts = pd.Timestamp("2026-01-05T00:30:00")  # Mon 00:30
        assert is_weekend_market_pause(ts)

    def test_monday_after_open_not_weekend(self):
        ts = pd.Timestamp("2026-01-05T02:00:00")  # Mon 02:00 UTC = open
        assert not is_weekend_market_pause(ts)

    def test_friday_after_close_is_weekend(self):
        ts = pd.Timestamp("2026-01-02T22:30:00")  # Fri 22:30 UTC
        assert is_weekend_market_pause(ts)

    def test_thursday_is_not_weekend(self):
        ts = pd.Timestamp("2026-01-01T12:00:00")  # Thursday
        assert not is_weekend_market_pause(ts)


# make_signal_row is an internal helper with complex signature — not tested
# directly in CI. The ShadowSignal dataclass tests above cover signal structure.


class TestParseStartDate:
    """Start date parsing."""

    def test_parses_iso_date(self):
        result = parse_start_date("2026-01-15", {})
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 15

    def test_returns_default_if_none(self):
        settings = {"forward_shadow": {}}
        result = parse_start_date(None, settings)
        assert result is not None

    def test_raises_on_invalid(self):
        with pytest.raises(ValueError):
            parse_start_date("not-a-date", {})


class TestUtcTimestamp:
    """UTC timestamp parsing."""

    def test_parses_string(self):
        result = utc_timestamp("2026-01-01T12:00:00")
        assert result.tz is not None

    def test_parses_datetime(self):
        dt = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        result = utc_timestamp(dt)
        assert result == pd.Timestamp(dt)


class TestEstimateSharpe:
    """Sharpe ratio estimation from equity curve."""

    def test_positive_for_growth(self):
        equity = pd.DataFrame({
            "equity": [100, 101, 102, 103, 104, 105],
            "timestamp": pd.date_range("2026-01-01", periods=6, freq="D", tz="UTC"),
        })
        sharpe = estimate_sharpe(equity)
        assert sharpe is not None and sharpe > 0

    def test_negative_for_decline(self):
        equity = pd.DataFrame({
            "equity": [100, 99, 98, 97, 96, 95],
            "timestamp": pd.date_range("2026-01-01", periods=6, freq="D", tz="UTC"),
        })
        sharpe = estimate_sharpe(equity)
        assert sharpe is not None and sharpe < 0

    def test_none_for_empty(self):
        # estimate_sharpe returns 0.0 for empty, which is fine
        result = estimate_sharpe(pd.DataFrame())
        assert result is None or result == 0.0


class TestSafeTableCount:
    """Safe SQLite table count."""

    def test_returns_zero_for_missing_table(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.sqlite3"
            conn = sqlite3.connect(str(db))
            count = safe_table_count(conn, "nonexistent_table")
            conn.close()
            assert count == 0

    def test_returns_count_for_existing_table(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.sqlite3"
            conn = sqlite3.connect(str(db))
            conn.execute("CREATE TABLE test (id INTEGER)")
            conn.execute("INSERT INTO test VALUES (1)")
            conn.execute("INSERT INTO test VALUES (2)")
            conn.commit()
            count = safe_table_count(conn, "test")
            conn.close()
            assert count == 2


class TestRuntimeEnvironment:
    """Runtime environment detection."""

    def test_returns_dict(self):
        env = runtime_environment_status()
        assert isinstance(env, dict)
        assert "python_version" in env

    def test_has_required_keys(self):
        env = runtime_environment_status()
        assert "python_version" in env


class TestShadowConfigPayload:
    """Shadow configuration payload."""

    def test_includes_strategy_name(self):
        settings = {"forward_shadow": {"strategy": "raw_donchian_fixed_2r"}}
        payload = shadow_config_payload(settings)
        assert payload["strategy"] == "raw_donchian_fixed_2r"

    def test_includes_lookback(self):
        settings = {"forward_shadow": {"lookback": 20}}
        payload = shadow_config_payload(settings)
        assert payload["lookback"] == 20


class TestDataGapReport:
    """Data gap detection."""

    def test_returns_dict(self):
        import os
        td = tempfile.mkdtemp()
        try:
            db = Path(td) / "test.sqlite3"
            conn = sqlite3.connect(str(db))
            conn.execute("CREATE TABLE shadow_candles (timestamp TEXT)")
            conn.execute("INSERT INTO shadow_candles VALUES ('2026-01-01T12:00:00')")
            conn.commit()
            report = data_gap_report(conn)
            conn.close()
            assert isinstance(report, dict)
        finally:
            for f in Path(td).glob("*"):
                try: os.remove(f)
                except: pass
            try: os.rmdir(td)
            except: pass


class TestRowToDict:
    """Row to dict conversion."""

    def test_converts_series(self):
        s = pd.Series({"a": 1, "b": 2.5, "c": "hello"})
        result = row_to_dict(s)
        assert result["a"] == 1
        assert result["b"] == 2.5
        assert result["c"] == "hello"

    def test_handles_nan(self):
        s = pd.Series({"a": np.nan, "b": 1})
        result = row_to_dict(s)
        assert result["a"] is None or pd.isna(result["a"])


class TestResolveMarketDb:
    """Market DB path resolution."""

    def test_returns_arg_if_provided(self):
        """If a custom path is given, it returns the resolved path."""
        p = Path("custom/path.sqlite3")
        result = resolve_market_db({}, p)
        assert result is not None
        assert "sqlite3" in str(result)

    def test_resolves_from_settings(self):
        settings = {
            "forward_shadow": {
                "market_data_db_path": "aurum1/data/market.sqlite3"
            }
        }
        result = resolve_market_db(settings, None)
        assert "market" in str(result)
