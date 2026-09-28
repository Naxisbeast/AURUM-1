"""OANDA practice-mode smoke tests for the D4 trader (broker-agnostic refactor).

These exercise the real D4PaperTrader loop against a mocked OANDA broker — no
network, no oandapyV20 calls. They prove:
  - DB paths are settings-injectable (practice DB separate from paper evidence)
  - --broker oanda routes to OandaBroker and persists a real-execution fill
  - server-side closes are polled and reconstructed with the correct R
  - the merged account state keeps daily P&L / peak for the kill switches
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from aurum1.execution.broker import OandaBroker


def settings_for(tmp_path: Path, market_db_path: Path) -> dict:
    return {
        "app": {"random_seed": 42},
        "broker": {
            "paper_trade": False,
            "paper_initial_equity": 10000.0,
            "oanda": {
                "instrument": "XAU_USD",
                "api_key_env": "OANDA_API_KEY",
                "account_id_env": "OANDA_ACCOUNT_ID",
                "environment_env": "OANDA_ENV",
                "default_environment": "practice",
            },
        },
        "data": {"db_path": str(tmp_path / "aurum1.sqlite3")},
        "execution": {
            "paper_spread_pips": 1.5,
            "slippage_std_pips": 0.0,
            "oanda_order_type": "market",
        },
        "risk": {
            "pip_size": 0.01,
            "max_spread_pips": 3.0,
            "risk_per_trade_pct": 0.0035,
            "daily_loss_kill_pct": 0.03,
            "total_drawdown_kill_pct": 0.08,
        },
        "instruments": {
            "XAU_USD": {
                "oanda_instrument": "XAU_USD",
                "account_currency": "USD",
                "pip_size": 0.01,
                "ounces_per_unit": 1.0,
                "units_per_lot": 100.0,
                "min_units": 1.0,
                "max_units": 1000.0,
                "unit_precision": 0,
                "min_lot_size": 0.01,
                "max_lot_size": 10.0,
                "lot_step": 0.01,
            }
        },
        "paper_trading": {
            "db_path": str(tmp_path / "oanda_practice.sqlite3"),
            "market_db_path": str(market_db_path),
            "health_file": str(tmp_path / "health.json"),
        },
    }


def _flat_frame(start: pd.Timestamp, n: int) -> pd.DataFrame:
    index = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "open": [2330.0] * n,
            "high": [2330.0] * n,
            "low": [2330.0] * n,
            "close": [2330.0] * n,
            "volume": [1.0] * n,
        },
        index=index,
    )


def _breakout_frame(start: pd.Timestamp, n: int, breakout_idx: int) -> pd.DataFrame:
    """Flat candles with a Donchian BUY breakout at breakout_idx (close > prior 20-bar high)."""
    frame = _flat_frame(start, n)
    spike = breakout_idx - 1
    frame.iloc[spike, frame.columns.get_loc("high")] = 2340.0
    frame.iloc[spike, frame.columns.get_loc("close")] = 2340.0
    frame.iloc[breakout_idx, frame.columns.get_loc("open")] = 2341.0
    frame.iloc[breakout_idx, frame.columns.get_loc("high")] = 2342.0
    frame.iloc[breakout_idx, frame.columns.get_loc("low")] = 2339.0
    frame.iloc[breakout_idx, frame.columns.get_loc("close")] = 2341.0
    return frame


def write_cache(path: Path, frame: pd.DataFrame) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ohlcv_M15 (
                timestamp TEXT PRIMARY KEY, open REAL, high REAL, low REAL,
                close REAL, volume REAL, source TEXT, instrument TEXT
            )
            """
        )
        conn.executemany(
            "INSERT OR REPLACE INTO ohlcv_M15 VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    ts.isoformat(), float(r["open"]), float(r["high"]), float(r["low"]),
                    float(r["close"]), float(r["volume"]), "oanda", "XAU_USD",
                )
                for ts, r in frame.iterrows()
            ],
        )


def make_closed_trade(tid: str, entry: float, exit_px: float, realized: float,
                      financing: float, reason: str) -> dict:
    today = datetime.now(UTC).date().isoformat()
    sl_state = "FILLED" if reason == "stop_loss" else "CANCELLED"
    tp_state = "FILLED" if reason == "take_profit" else "CANCELLED"
    return {
        "id": tid, "initialUnits": "10", "realizedPL": str(realized), "financing": str(financing),
        "averageOpenPrice": str(entry), "averageClosePrice": str(exit_px),
        "openTime": f"{today}T12:00:00Z", "closeTime": f"{today}T14:00:00Z",
        "stopLossOrder": {"state": sl_state}, "takeProfitOrder": {"state": tp_state},
    }


def _mock_oanda(monkeypatch: pytest.MonkeyPatch, closed_state: list) -> None:
    """Class-level mocks on OandaBroker so the instance created inside the trader
    uses them (no network / no oandapyV20)."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    monkeypatch.setattr(OandaBroker, "_account_summary", lambda self: {
        "account": {"NAV": "10000.0", "balance": "10000.0", "openTradeCount": 0}})
    monkeypatch.setattr(OandaBroker, "_pricing", lambda self, instrument: {
        "prices": [{"bids": [{"price": "2341.00"}], "asks": [{"price": "2341.02"}]}]})
    monkeypatch.setattr(OandaBroker, "_open_positions", lambda self: {"positions": []})
    monkeypatch.setattr(OandaBroker, "_open_trades", lambda self: {"trades": []})
    monkeypatch.setattr(OandaBroker, "_submit_limit_order", lambda self, data: {
        "orderFillTransaction": {
            "id": "999", "price": "2341.5", "time": f"{datetime.now(UTC).isoformat()}",
            "tradeOpened": {"tradeID": "777"},
        }})
    monkeypatch.setattr(OandaBroker, "_closed_trades", lambda self: {"trades": list(closed_state)})


def test_d4_trader_db_path_injectable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The trader's practice DB / market cache / health file honor settings."""
    from scripts.paper_trading.d4_paper_trader import D4PaperTrader

    _mock_oanda(monkeypatch, [])
    write_cache(tmp_path / "market.sqlite3", _flat_frame(pd.Timestamp("2026-01-01", tz="UTC"), 300))
    settings = settings_for(tmp_path, tmp_path / "market.sqlite3")

    trader = D4PaperTrader(settings)

    assert str(trader._paper_db) == str(tmp_path / "oanda_practice.sqlite3")
    assert str(trader.market_db) == str(tmp_path / "market.sqlite3")
    assert str(trader.health_file) == str(tmp_path / "health.json")
    assert trader._paper_db.exists()  # schema created


def test_d4_oanda_run_once_entry_persists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--broker oanda: run_once places a BUY breakout order, persists open-meta."""
    from scripts.paper_trading.d4_paper_trader import D4PaperTrader

    _mock_oanda(monkeypatch, [])
    start = pd.Timestamp("2026-01-01", tz="UTC")
    # 300 bars; breakout at index 298 == iloc[-2] (the last completed candle).
    write_cache(tmp_path / "market.sqlite3", _breakout_frame(start, 300, 298))

    trader = D4PaperTrader(settings_for(tmp_path, tmp_path / "market.sqlite3"))
    trader.run_once()

    # Entry placed via OANDA: open-meta keyed by the OANDA trade ID.
    assert "777" in trader._open_meta
    meta = trader._open_meta["777"]
    assert meta["direction"] == "BUY"
    assert meta["risk_amount"] > 0

    # Persisted to the practice DB (not paper evidence).
    with sqlite3.connect(trader._paper_db) as conn:
        row = conn.execute(
            "SELECT position_id, direction, risk_amount FROM open_positions"
        ).fetchone()
    assert row is not None
    assert row[0] == "777"
    assert row[1] == "BUY"
    assert row[2] > 0

    # Merged account state reports real OANDA equity, not paper sim.
    assert trader._account_state().equity == pytest.approx(10000.0)


def test_d4_oanda_run_once_persists_close(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A server-side SL/TP close is polled, reconstructed, and persisted with R."""
    from scripts.paper_trading.d4_paper_trader import D4PaperTrader

    closed_state: list = []
    _mock_oanda(monkeypatch, closed_state)
    start = pd.Timestamp("2026-01-01", tz="UTC")
    write_cache(tmp_path / "market.sqlite3", _breakout_frame(start, 300, 298))

    trader = D4PaperTrader(settings_for(tmp_path, tmp_path / "market.sqlite3"))
    trader.run_once()
    assert "777" in trader._open_meta
    risk_amount = trader._open_meta["777"]["risk_amount"]

    # Append 2 flat bars so a second run_once processes a completed candle.
    write_cache(tmp_path / "market.sqlite3", _flat_frame(start + pd.Timedelta(minutes=300 * 15), 2))
    # Simulate the OANDA position closing at the take-profit (+2R).
    realized = round(risk_amount * 2.0, 2)
    closed_state.append(make_closed_trade("777", 2341.5, 2343.0, realized, 0.0, "take_profit"))

    trader.run_once()

    with sqlite3.connect(trader._paper_db) as conn:
        row = conn.execute(
            "SELECT position_id, r_multiple, net_pnl, exit_reason FROM trades"
        ).fetchone()
    assert row is not None
    assert row[0] == "777"
    assert row[1] == pytest.approx(realized / risk_amount, rel=1e-3)
    assert row[2] == pytest.approx(realized, rel=1e-3)
    assert row[3] == "take_profit"

    # Position closed: open-meta drained and no open position persisted.
    assert "777" not in trader._open_meta
    # Daily P&L for the merged account state reflects the close.
    assert trader._account_state().daily_pnl == pytest.approx(realized, rel=1e-3)


def test_d4_oanda_restore_meta_reconstructs_r(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """After a restart, risk_amount from open_positions reconstructs R for a close."""
    from scripts.paper_trading.d4_paper_trader import D4PaperTrader

    closed_state: list = []
    _mock_oanda(monkeypatch, closed_state)
    start = pd.Timestamp("2026-01-01", tz="UTC")
    write_cache(tmp_path / "market.sqlite3", _breakout_frame(start, 300, 298))

    trader = D4PaperTrader(settings_for(tmp_path, tmp_path / "market.sqlite3"))
    trader.run_once()
    risk_amount = trader._open_meta["777"]["risk_amount"]

    # Simulate restart: a fresh trader restores open_positions (incl. risk_amount)
    # and then a close arrives that was NOT in self.trades.
    trader2 = D4PaperTrader(settings_for(tmp_path, tmp_path / "market.sqlite3"))
    assert "777" in trader2._open_meta
    assert trader2._open_meta["777"]["risk_amount"] == pytest.approx(risk_amount, rel=1e-3)

    realized = round(risk_amount * -1.0, 2)  # stop-loss: -1R
    closed_state.append(make_closed_trade("777", 2341.5, 2339.0, realized, 0.0, "stop_loss"))
    write_cache(tmp_path / "market.sqlite3", _flat_frame(start + pd.Timedelta(minutes=300 * 15), 2))

    trader2.run_once()

    with sqlite3.connect(trader2._paper_db) as conn:
        row = conn.execute(
            "SELECT position_id, r_multiple, exit_reason FROM trades"
        ).fetchone()
    assert row is not None
    assert row[0] == "777"
    assert row[1] == pytest.approx(-1.0, rel=1e-3)
    assert row[2] == "stop_loss"


def test_main_broker_flag_routes_paper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """main() --broker paper routes to paper_trade=True (backward compatible)."""
    from scripts.paper_trading import d4_paper_trader as mod

    settings = settings_for(tmp_path, tmp_path / "market.sqlite3")
    settings["broker"]["paper_trade"] = True

    captured: dict = {}
    monkeypatch.setattr(mod, "_acquire_pid_lock", lambda: True)
    monkeypatch.setattr(mod, "_release_pid_lock", lambda: None)
    monkeypatch.setattr(mod, "load_settings", lambda *a, **k: settings)

    class FakeTrader:
        def __init__(self, s):
            captured["paper_trade"] = s["broker"]["paper_trade"]
            self.stop_requested = object()

        def run_once(self):
            pass

        def _print_summary(self):
            pass

    monkeypatch.setattr(mod, "D4PaperTrader", FakeTrader)
    monkeypatch.setattr(sys, "argv", ["d4_paper_trader.py", "--broker", "paper", "--run-once"])

    assert mod.main() == 0
    assert captured["paper_trade"] is True
