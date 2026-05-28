from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import pandas as pd
import pytest

from aurum1.data.ingestion import initialize_database
from aurum1.data.ingestion import load_settings
from monitor.metrics import (
    compute_drawdown_curve,
    compute_rolling_profit_factor,
    compute_rolling_sharpe,
    compute_rolling_win_rate,
    get_system_status,
    load_equity_curve,
)


def test_compute_rolling_sharpe_basic() -> None:
    timestamps = pd.date_range("2026-01-01", periods=40, freq="D", tz="UTC")
    equity = pd.DataFrame({"timestamp": timestamps, "equity": [10000.0 + idx * 25.0 for idx in range(40)]})

    sharpe = compute_rolling_sharpe(equity, window_days=30)

    assert len(sharpe) == len(equity)
    assert sharpe.iloc[-1] > 0.0


def test_compute_rolling_sharpe_empty() -> None:
    result = compute_rolling_sharpe(pd.DataFrame())

    assert result.empty


def test_compute_drawdown_curve_known_values() -> None:
    timestamps = pd.date_range("2026-01-01", periods=5, freq="D", tz="UTC")
    equity = pd.DataFrame({"timestamp": timestamps, "equity": [100.0, 110.0, 105.0, 95.0, 100.0]})

    drawdown = compute_drawdown_curve(equity)

    assert drawdown.min() == pytest.approx((95.0 - 110.0) / 110.0, abs=0.001)
    assert drawdown.iloc[0] == 0.0
    assert drawdown.iloc[1] == 0.0


def test_compute_rolling_profit_factor_basic() -> None:
    timestamps = pd.date_range("2026-01-01", periods=6, freq="D", tz="UTC")
    trades = pd.DataFrame({"timestamp": timestamps, "pnl": [100.0, -40.0, 80.0, -30.0, 60.0, 50.0]})

    profit_factor = compute_rolling_profit_factor(trades, window_days=30)

    assert profit_factor.iloc[-1] > 1.0


def test_compute_rolling_win_rate_basic() -> None:
    timestamps = pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC")
    trades = pd.DataFrame({"timestamp": timestamps, "pnl": [1, 1, 1, 1, 1, 1, -1, -1, -1, -1]})

    win_rate = compute_rolling_win_rate(trades, window_days=30)

    assert win_rate.iloc[-1] == pytest.approx(0.60, abs=0.001)


def test_load_equity_curve_returns_sorted() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        db_path = Path(tempdir) / "aurum.sqlite3"
        initialize_database(db_path)
        rows = [
            ("2026-01-02T00:00:00+00:00", "equity", 10100.0, None),
            ("2026-01-01T00:00:00+00:00", "equity", 10000.0, None),
            ("2026-01-02T00:00:00+00:00", "equity", 10150.0, None),
            ("2026-01-03T00:00:00+00:00", "account", None, json.dumps({"equity": 10200.0})),
        ]
        with closing(sqlite3.connect(db_path)) as conn:
            with conn:
                conn.executemany(
                    """
                    INSERT INTO performance_log
                    (timestamp, metric_name, metric_value, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    rows,
                )

        equity = load_equity_curve(str(db_path))

    assert equity["timestamp"].is_monotonic_increasing
    assert not equity["timestamp"].duplicated().any()
    assert equity.iloc[1]["equity"] == 10150.0


def test_get_system_status_returns_required_keys() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        db_path = Path(tempdir) / "aurum.sqlite3"
        initialize_database(db_path)

        status = get_system_status(
            str(db_path),
            None,
            {
                "broker": {"paper_trade": True, "paper_initial_equity": 10000.0},
                "risk": {"daily_loss_kill_pct": 0.03, "total_drawdown_kill_pct": 0.08},
                "signals": {"default_machine_mode": "RULE_REGIME"},
            },
        )

    required = {
        "equity",
        "daily_pnl",
        "open_positions",
        "active_mode",
        "blackout_active",
        "daily_kill_triggered",
        "total_drawdown_kill_triggered",
    }
    assert required.issubset(status)


def test_dashboard_binds_localhost_by_default() -> None:
    settings = load_settings(Path(__file__).resolve().parents[1] / "aurum1" / "config" / "settings.yaml")

    assert settings["monitor"]["dashboard_host"] == "127.0.0.1"
