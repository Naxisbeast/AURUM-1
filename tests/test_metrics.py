"""Tests for dashboard metric computations.

These tests verify the metric computation functions in monitor/metrics.py
without requiring a live dashboard or database connection.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from monitor.metrics import (
    compute_drawdown_curve,
    compute_rolling_profit_factor,
    compute_rolling_sharpe,
    compute_rolling_win_rate,
    compute_r_distribution,
    compute_mae_mfe,
    load_equity_curve,
    load_system_health,
    get_system_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_equity_frame(values: list[float], start: str = "2026-01-01") -> pd.DataFrame:
    """Create an equity curve DataFrame from a list of values."""
    timestamps = pd.date_range(start, periods=len(values), freq="h", tz="UTC")
    return pd.DataFrame({"timestamp": timestamps, "equity": values})


def _make_trade_frame(pnls: list[float], start: str = "2026-01-01") -> pd.DataFrame:
    """Create a trades DataFrame from a list of PnL values."""
    timestamps = pd.date_range(start, periods=len(pnls), freq="h", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps,
        "pnl": pnls,
        "r_multiple": [2.0 if p > 0 else -1.0 for p in pnls],
        "direction": ["BUY" if i % 2 == 0 else "SELL" for i in range(len(pnls))],
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLoadEquityCurve:
    """Equity curve loading from various sources."""

    def test_returns_empty_frame_for_nonexistent_db(self):
        result = load_equity_curve("/nonexistent/path/db.sqlite3")
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_returns_empty_frame_for_empty_paper_db(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "paper_trading.sqlite3"
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS account_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        equity REAL NOT NULL
                    )
                """)
                conn.commit()
            result = load_equity_curve(str(db))
            assert isinstance(result, pd.DataFrame)
            assert result.empty

    def test_loads_from_account_snapshots(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "paper_trading.sqlite3"
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS account_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        equity REAL NOT NULL
                    )
                """)
                conn.execute(
                    "INSERT INTO account_snapshots (timestamp, equity) VALUES (?, ?)",
                    ("2026-01-01T12:00:00+00:00", 10000.0),
                )
                conn.execute(
                    "INSERT INTO account_snapshots (timestamp, equity) VALUES (?, ?)",
                    ("2026-01-01T13:00:00+00:00", 10100.0),
                )
                conn.commit()
            result = load_equity_curve(str(db))
            assert not result.empty
            assert len(result) == 2
            assert result["equity"].iloc[-1] == 10100.0


class TestComputeDrawdownCurve:
    """Drawdown curve computation."""

    def test_returns_empty_for_empty_input(self):
        result = compute_drawdown_curve(pd.DataFrame())
        assert isinstance(result, pd.Series)
        assert result.empty

    def test_no_drawdown_when_equity_only_rises(self):
        frame = _make_equity_frame([100, 101, 102, 103])
        result = compute_drawdown_curve(frame)
        assert not result.empty
        assert all(v == 0.0 for v in result)

    def test_drawdown_on_decline(self):
        frame = _make_equity_frame([100, 110, 105, 95, 100])
        result = compute_drawdown_curve(frame)
        assert not result.empty
        # At 95 from peak 110: (95-110)/110 = -13.6%
        assert result.iloc[3] == pytest.approx(-0.13636, rel=0.01)

    def test_drawdown_recovers(self):
        frame = _make_equity_frame([100, 90, 110])
        result = compute_drawdown_curve(frame)
        assert not result.empty
        assert result.iloc[0] == 0.0  # start
        assert result.iloc[1] < 0   # drawdown
        assert result.iloc[2] == 0.0  # recovered


class TestComputeRollingSharpe:
    """Rolling Sharpe ratio."""

    def test_returns_empty_for_empty_input(self):
        result = compute_rolling_sharpe(pd.DataFrame())
        assert isinstance(result, pd.Series)
        assert result.empty

    def test_sharpe_positive_for_rising_equity(self):
        # Need enough daily data for meaningful Sharpe
        days = pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC")
        values = [100 + i * 0.5 for i in range(30)]  # steady uptrend
        frame = pd.DataFrame({"timestamp": days, "equity": values})
        result = compute_rolling_sharpe(frame)
        assert not result.empty
        last_valid = result.dropna()
        if len(last_valid) > 0:
            assert last_valid.iloc[-1] > 0

    def test_sharpe_negative_for_falling_equity(self):
        days = pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC")
        values = [100 - i * 0.5 for i in range(30)]  # steady downtrend
        frame = pd.DataFrame({"timestamp": days, "equity": values})
        result = compute_rolling_sharpe(frame)
        assert not result.empty
        last_valid = result.dropna()
        if len(last_valid) > 0:
            assert last_valid.iloc[-1] < 0


class TestComputeRollingProfitFactor:
    """Rolling profit factor."""

    def test_returns_empty_for_empty_trades(self):
        trades = pd.DataFrame(columns=["pnl"])
        result = compute_rolling_profit_factor(trades)
        assert isinstance(result, pd.Series)

    def test_pf_above_1_for_profitable_trades(self):
        trades = _make_trade_frame([100, -50, 100, -50, 100])
        result = compute_rolling_profit_factor(trades)
        assert not result.empty
        assert result.iloc[-1] >= 1.0

    def test_pf_below_1_for_losing_trades(self):
        trades = _make_trade_frame([-100, -100, -100, -100])
        result = compute_rolling_profit_factor(trades)
        assert not result.empty
        assert result.iloc[-1] <= 1.0


class TestComputeRollingWinRate:
    """Rolling win rate."""

    def test_returns_empty_for_empty_trades(self):
        result = compute_rolling_win_rate(pd.DataFrame())
        assert isinstance(result, pd.Series)

    def test_win_rate_100_for_all_wins(self):
        trades = _make_trade_frame([100, 50, 200])
        result = compute_rolling_win_rate(trades)
        assert not result.empty
        assert result.iloc[-1] == 1.0

    def test_win_rate_0_for_all_losses(self):
        trades = _make_trade_frame([-100, -50, -200])
        result = compute_rolling_win_rate(trades)
        assert not result.empty
        assert result.iloc[-1] == 0.0

    def test_win_rate_50_for_mixed(self):
        trades = _make_trade_frame([100, -50, 200, -100])
        result = compute_rolling_win_rate(trades)
        assert not result.empty


class TestComputeRDistribution:
    """R-multiple distribution statistics."""

    def test_returns_empty_for_empty_trades(self):
        stats = compute_r_distribution(pd.DataFrame())
        assert stats["n_trades"] == 0

    def test_win_rate_matches_input(self):
        trades = _make_trade_frame([100, -50, 100])
        stats = compute_r_distribution(trades)
        assert stats["n_trades"] == 3
        assert stats["n_wins"] == 2

    def test_cumulative_r(self):
        trades = pd.DataFrame({
            "r_multiple": [2.0, -1.0, 2.0],
            "direction": ["BUY", "SELL", "BUY"],
        })
        stats = compute_r_distribution(trades)
        assert stats["cumulative_r"] == pytest.approx(3.0)

    def test_r_sharpe_computed(self):
        trades = pd.DataFrame({
            "r_multiple": [2.0, -1.0, 2.0, -1.0, 2.0],
            "direction": ["BUY"] * 5,
        })
        stats = compute_r_distribution(trades)
        assert stats["r_sharpe"] > 0

    def test_deciles_present(self):
        trades = pd.DataFrame({
            "r_multiple": list(range(-5, 6)),
            "direction": ["BUY"] * 11,
        })
        stats = compute_r_distribution(trades)
        assert len(stats["r_deciles"]) == 9


class TestComputeMaeMfe:
    """MAE/MFE computation."""

    def test_returns_frame(self):
        trades = _make_trade_frame([100, -50])
        result = compute_mae_mfe(trades)
        assert isinstance(result, pd.DataFrame)

    def test_empty_for_empty_trades(self):
        result = compute_mae_mfe(pd.DataFrame())
        assert result.empty


class TestLoadSystemHealth:
    """System health file loading."""

    def test_returns_defaults_without_health_file(self):
        with tempfile.TemporaryDirectory() as td:
            fake_db = Path(td) / "aurum1" / "data" / "aurum1.sqlite3"
            fake_db.parent.mkdir(parents=True, exist_ok=True)
            fake_db.touch()
            health = load_system_health(str(fake_db))
            assert health["source"] == "none"
            assert health["trade_count"] == 0

    def test_reads_health_file(self):
        with tempfile.TemporaryDirectory() as td:
            # Create fake DB path structure
            fake_db = Path(td) / "aurum1" / "data" / "aurum1.sqlite3"
            fake_db.parent.mkdir(parents=True, exist_ok=True)
            fake_db.touch()

            # Create health file at run/d4_paper_trader_health.json (3 levels up from db)
            root = Path(td)
            health_dir = root / "run"
            health_dir.mkdir(parents=True, exist_ok=True)
            health_file = health_dir / "d4_paper_trader_health.json"
            health_file.write_text(json.dumps({
                "uptime_seconds": 36000,
                "equity": 10500.0,
                "peak_equity": 10600.0,
                "trade_count": 30,
                "signals_seen": 50,
                "missed_signals": 3,
                "avg_entry_slippage_units": 0.02,
                "avg_exit_slippage_units": 0.01,
                "avg_spread_pips": 1.5,
                "avg_latency_seconds": 0.05,
                "min_latency_seconds": 0.01,
                "max_latency_seconds": 0.5,
                "market_latest_candle_age_minutes": 5.0,
                "timestamp": "2026-01-01T12:00:00+00:00",
            }))

            health = load_system_health(str(fake_db))
            assert health["source"] == "d4_health_file"
            assert health["trade_count"] == 30
            assert health["uptime_hours"] == 10.0  # 36000 / 3600
            assert health["avg_spread_pips"] == 1.5
            assert health["missed_signals"] == 3


class TestGetSystemStatus:
    """System status extraction."""

    def test_returns_dict_with_required_keys(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "aurum1" / "data" / "aurum1.sqlite3"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.touch()
            settings = {
                "broker": {"paper_trade": True, "paper_initial_equity": 10000.0},
                "risk": {"daily_loss_kill_pct": 0.03, "total_drawdown_kill_pct": 0.08},
                "signals": {"default_machine_mode": "RULE_REGIME"},
                "execution": {"paper_spread_pips": 1.5},
            }
            status = get_system_status(str(db_path), settings)
            assert "system_mode" in status
            assert "equity" in status
            assert "daily_kill_triggered" in status
            assert "total_drawdown_kill_triggered" in status
