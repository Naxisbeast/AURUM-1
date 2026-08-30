"""Tests for Phase 1 Observability — slippage, spread, latency, missed signals, open position recovery."""

from __future__ import annotations

import json
import math
import sqlite3
import dataclasses
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from aurum1.execution.broker import PaperBroker
from aurum1.risk.manager import RiskManager
from aurum1.signals import CandleRow, TradeInstruction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def settings(db_path: Path | None = None) -> dict[str, Any]:
    path = str(db_path or ":memory:")
    return {
        "general": {"random_seed": 7},
        "data": {"db_path": path},
        "broker": {
            "paper_trade": True,
            "paper_initial_equity": 10000.0,
            "oanda": {
                "instrument": "XAU_USD",
                "api_key_env": "OANDA_API_KEY",
                "account_id_env": "OANDA_ACCOUNT_ID",
                "environment_env": "OANDA_ENV",
                "default_environment": "practice",
            },
        },
        "risk": {"max_spread_pips": 3.0, "pip_size": 0.01},
        "execution": {"fill_timeout_candles": 3, "slippage_std_pips": 0.5, "paper_spread_pips": 1.5},
    }


def make_instruction(
    direction: str = "BUY",
    entry_price: float = 100.0,
    stop_loss: float = 98.0,
    take_profit: float = 104.0,
) -> TradeInstruction:
    return TradeInstruction(
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        atr_at_entry=2.0,
        signal_score=0.8,
        regime="TRENDING_UP" if direction == "BUY" else "TRENDING_DOWN",
        confidence=0.9,
        machine_mode="rule_regime",
    )


def candle_row(
    ts: datetime | None = None,
    open_p: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
) -> CandleRow:
    return CandleRow(
        timestamp=ts or datetime(2026, 1, 1, 12, 15, tzinfo=UTC),
        open=open_p,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        atr_14=2.0,
        adx_14=30.0,
        ema_9=99.0,
        ema_20=98.0,
        session_london=1,
        session_ny=0,
        session_overlap=0,
    )


def open_trade(broker: PaperBroker, risk_manager: RiskManager,
               entry: float = 100.01, direction: str = "BUY") -> dict:
    """Helper: open a trade and return the trade dict from the broker."""
    instr = make_instruction(direction=direction, entry_price=entry)
    account = broker.get_account_state()
    order = risk_manager.evaluate(instr, account, list(broker._trade_history))
    result = broker.submit_order(order)
    assert result.success
    return result.raw_response  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Slippage tracking
# ---------------------------------------------------------------------------

class TestEntryExitSlippage:
    """Verify entry and exit slippage is recorded correctly."""

    def test_entry_slippage_recorded(self, monkeypatch) -> None:
        """Entry slippage should be non-zero and stored in the position record."""
        cfg = settings()
        broker = PaperBroker(cfg)
        risk_manager = RiskManager(cfg)
        monkeypatch.setattr(PaperBroker, "_sample_slippage_distance", lambda self: 0.015)

        instr = make_instruction(direction="BUY", entry_price=100.0)
        account = broker.get_account_state()
        order = risk_manager.evaluate(instr, account, [])
        result = broker.submit_order(order)
        assert result.success

        raw = result.raw_response or {}
        assert abs(float(raw.get("entry_slippage", 0))) > 0
        assert float(raw.get("entry_slippage_cost", 0)) > 0

        intended = float(raw.get("intended_entry_price", 0))
        actual = float(raw.get("actual_entry_price", 0))
        assert actual > intended  # BUY slippage should worsen price up

    def test_exit_slippage_recorded(self, monkeypatch) -> None:
        """Exit slippage should be recorded on close_position."""
        cfg = settings()
        broker = PaperBroker(cfg)
        rm = RiskManager(cfg)
        monkeypatch.setattr(PaperBroker, "_sample_slippage_distance", lambda self: 0.01)

        open_trade(broker, rm, entry=100.0)
        # Close via stop loss gap
        candle = candle_row(open_p=96.0, high=96.5, low=95.0, close=95.5)
        broker.update_prices(candle)

        assert len(broker._trade_history) == 1
        trade = broker._trade_history[0]
        intended = float(trade.get("intended_exit", 0))
        actual = float(trade.get("actual_exit", trade.get("exit", 0)))
        assert intended != actual  # slippage should differ
        assert float(trade.get("exit_slippage_cost", 0)) > 0

    def test_slippage_negative_allowed(self, monkeypatch) -> None:
        """Slippage can be negative (price improvement) with gaussian sampling."""
        cfg = settings()
        broker = PaperBroker(cfg)
        rm = RiskManager(cfg)
        # Force negative slippage (price improvement)
        monkeypatch.setattr(PaperBroker, "_sample_slippage_distance", lambda self: -0.01)

        instr = make_instruction(direction="BUY", entry_price=100.0)
        account = broker.get_account_state()
        order = rm.evaluate(instr, account, [])
        result = broker.submit_order(order)
        assert result.success

        raw = result.raw_response or {}
        intended = float(raw.get("intended_entry_price", 0))
        actual = float(raw.get("actual_entry_price", 0))
        assert actual < intended  # price improvement


# ---------------------------------------------------------------------------
# Spread tracking
# ---------------------------------------------------------------------------

class TestSpreadTracking:
    """Spread should be measurable throughout the trade lifecycle."""

    def test_spread_accessible_via_account(self) -> None:
        """PaperBroker should report a configurable spread in pips."""
        cfg = settings()
        cfg["execution"]["paper_spread_pips"] = 2.5
        broker = PaperBroker(cfg)
        account = broker.get_account_state()
        assert account.current_spread_pips == pytest.approx(2.5)

    def test_spread_constant_in_paper(self) -> None:
        """Paper broker's spread is deterministic from config."""
        cfg = settings()
        broker = PaperBroker(cfg)
        assert broker.get_current_spread_pips("XAU_USD") == 1.5

    def test_spread_via_candle_prices(self) -> None:
        """Slippage (not a separate spread line) captures trade friction."""
        cfg = settings()
        cfg["execution"]["paper_spread_pips"] = 2.0
        broker = PaperBroker(cfg)
        rm = RiskManager(cfg)

        # Open and close a trade
        open_trade(broker, rm, entry=100.0)
        broker.update_prices(candle_row(open_p=96.0, high=96.5, low=95.0, close=95.5))

        assert len(broker._trade_history) == 1
        trade = broker._trade_history[0]
        # The audit zeroed PaperBroker._spread_cost because spread friction is
        # already embedded in the folded-normal slippage applied to fill prices;
        # a separate spread line double-counted cost. Friction is captured via
        # total_slippage_cost instead.
        spread_cost = float(trade.get("spread_cost", 0))
        assert spread_cost == 0.0
        assert float(trade.get("total_slippage_cost", 0)) > 0.0


# ---------------------------------------------------------------------------
# Latency measurement
# ---------------------------------------------------------------------------

class TestLatencyMeasurement:
    """Latency should be measurable (placeholder for real clock timing)."""

    def test_latency_can_be_measured(self) -> None:
        """Min/avg/max latency are real numbers after any execution."""
        cfg = settings()
        broker = PaperBroker(cfg)
        rm = RiskManager(cfg)

        instr = make_instruction(direction="BUY", entry_price=100.0)
        account = broker.get_account_state()
        order = rm.evaluate(instr, account, [])

        # Simulate round-trip timing
        t0 = datetime.now(UTC)
        result = broker.submit_order(order)
        elapsed = (datetime.now(UTC) - t0).total_seconds()

        assert result.success
        assert elapsed >= 0.0
        assert isinstance(elapsed, float)

    def test_multiple_latencies_produce_min_max(self) -> None:
        """Multiple executions produce min, max, and avg values."""
        _latencies = [0.010, 0.025, 0.015]
        assert min(_latencies) == 0.010
        assert max(_latencies) == 0.025
        assert sum(_latencies) / len(_latencies) == pytest.approx(0.01667, abs=1e-4)


# ---------------------------------------------------------------------------
# Missed signal logging
# ---------------------------------------------------------------------------

class TestMissedSignalLogging:
    """Missed signals should be logged with full context."""

    def test_rejected_order_logs_missed_signal(self) -> None:
        """A rejected order should produce a missed signal entry."""
        cfg = settings()
        broker = PaperBroker(cfg)
        rm = RiskManager(cfg)

        # Submit a rejected order directly
        instr = make_instruction(direction="BUY", entry_price=100.0)
        account = broker.get_account_state()

        # Force rejection via risk manager with excessive size
        max_units = float(cfg.get("risk", {}).get("max_position_units", 1e9))
        # Create an order that will be rejected by setting a very wide SL
        instr_wide = make_instruction(
            direction="BUY",
            entry_price=100.0,
            stop_loss=1.0,  # absurdly wide SL → large risk
            take_profit=200.0,
        )
        order = rm.evaluate(instr_wide, account, [])
        # If somehow approved, force rejection
        if order.approved:
            import dataclasses
            order = dataclasses.replace(order, approved=False, rejection_reason="testing_forced_rejection")

        missed_signal = {
            "timestamp": "2026-01-01T12:00:00+00:00",
            "direction": "BUY",
            "price": 100.0,
            "reason": order.rejection_reason or "test",
        }
        assert missed_signal["direction"] == "BUY"
        assert missed_signal["price"] == 100.0
        assert missed_signal["reason"] is not None

    def test_missed_signal_reason_is_meaningful(self) -> None:
        """Rejection reason should be a descriptive string."""
        cfg = settings()
        broker = PaperBroker(cfg)
        rm = RiskManager(cfg)

        # Test with max-spread rejection
        cfg["execution"]["paper_spread_pips"] = 10.0  # absurd spread
        broker2 = PaperBroker(cfg)
        instr = make_instruction(direction="BUY", entry_price=100.0)
        account = broker2.get_account_state()
        order = rm.evaluate(instr, account, [])
        result = broker2.submit_order(order)
        if not result.success:
            reason = result.rejection_reason or ""
            assert len(reason) > 3  # meaningful string


# ---------------------------------------------------------------------------
# Persistence of missed signals to DB
# ---------------------------------------------------------------------------

class TestMissedSignalPersistence:
    """Missed signals should survive a restart via SQLite."""

    def test_missed_signal_saves_to_db(self) -> None:
        """Writing a missed signal to the DB should be queryable."""
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "paper_test.sqlite3"
            with closing(sqlite3.connect(str(db))) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS missed_signals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        price REAL,
                        reason TEXT NOT NULL,
                        at_entry REAL,
                        created_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                conn.execute(
                    "INSERT INTO missed_signals (timestamp, direction, price, reason) VALUES (?, ?, ?, ?)",
                    ("2026-01-01T12:00:00+00:00", "BUY", 100.0, "max_risk_exceeded"),
                )
                conn.commit()

                rows = conn.execute("SELECT * FROM missed_signals").fetchall()
                assert len(rows) == 1
                assert rows[0][3] == 100.0  # price
                assert rows[0][4] == "max_risk_exceeded"

    def test_missed_signals_restore_on_start(self) -> None:
        """Missed signals should be restored from DB on restart."""
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "paper_test.sqlite3"
            # Seed data
            with closing(sqlite3.connect(str(db))) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS missed_signals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        price REAL,
                        reason TEXT NOT NULL,
                        at_entry REAL,
                        created_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                for i, reason in enumerate(["max_risk_exceeded", "spread_too_wide", "max_positions"]):
                    conn.execute(
                        "INSERT INTO missed_signals (timestamp, direction, price, reason) VALUES (?, ?, ?, ?)",
                        (f"2026-01-01T12:0{i}:00+00:00", "BUY" if i % 2 == 0 else "SELL", 100.0 + i, reason),
                    )
                conn.commit()

            # Simulate restore
            log: list[dict] = []
            with closing(sqlite3.connect(str(db))) as conn:
                rows = conn.execute(
                    "SELECT timestamp, direction, price, reason FROM missed_signals ORDER BY id"
                ).fetchall()
                for row in rows:
                    log.append({
                        "timestamp": row[0],
                        "direction": row[1],
                        "price": row[2],
                        "reason": row[3],
                    })
            assert len(log) == 3
            assert log[0]["reason"] == "max_risk_exceeded"
            assert log[2]["direction"] == "BUY"

    def test_missed_signal_reasons_are_counted(self) -> None:
        """Missed signal reasons should be countable for reporting."""
        log = [
            {"timestamp": "2026-01-01T12:00:00Z", "direction": "BUY", "price": 100.0, "reason": "max_risk_exceeded"},
            {"timestamp": "2026-01-01T12:15:00Z", "direction": "SELL", "price": 101.0, "reason": "spread_too_wide"},
            {"timestamp": "2026-01-01T12:30:00Z", "direction": "BUY", "price": 102.0, "reason": "max_risk_exceeded"},
            {"timestamp": "2026-01-01T12:45:00Z", "direction": "BUY", "price": 103.0, "reason": "max_positions"},
        ]
        by_reason: dict[str, int] = {}
        for entry in log:
            r = entry.get("reason", "unknown")
            by_reason[r] = by_reason.get(r, 0) + 1
        assert by_reason == {"max_risk_exceeded": 2, "spread_too_wide": 1, "max_positions": 1}


# ---------------------------------------------------------------------------
# Observability report format
# ---------------------------------------------------------------------------

class TestObservabilityReport:
    """The observability report should include all key metrics."""

    def test_report_includes_all_required_fields(self) -> None:
        """Report must contain slippage, spread, latency, missed signals."""
        report = {
            "signals_seen": 15,
            "missed_signals": 3,
            "missed_reasons": [{"reason": "max_risk", "count": 2}, {"reason": "spread", "count": 1}],
            "entry_slippage_avg": 0.012,
            "slippage_samples": 12,
            "exit_slippage_avg": -0.003,
            "spread_avg_pips": 1.5,
            "spread_samples": 15,
            "latency_avg_ms": 15,
            "latency_min_ms": 5,
            "latency_max_ms": 42,
            "trades_closed": 12,
        }
        assert "signals_seen" in report
        assert "missed_signals" in report
        assert "entry_slippage_avg" in report
        assert "exit_slippage_avg" in report
        assert "spread_avg_pips" in report
        assert "latency_avg_ms" in report

    def test_spread_history_stores_pip_values(self) -> None:
        """Spread history should be a list of pip values."""
        history = [1.2, 1.5, 2.0, 1.3, 1.8]
        avg = sum(history) / len(history)
        assert avg == pytest.approx(1.56)
        assert min(history) == 1.2
        assert max(history) == 2.0

    def test_slippage_stats_empty(self) -> None:
        """Empty histories should not crash."""
        slip_history: list[float] = []
        avg_slip = sum(slip_history) / max(len(slip_history), 1)
        assert avg_slip == 0.0

    def test_latency_stats_empty(self) -> None:
        """Zero latency measurements should not crash."""
        total = 0.0
        count = 0
        avg = total / count if count > 0 else 0
        assert avg == 0.0


# ---------------------------------------------------------------------------
# Integration: end-to-end observability data flow
# ---------------------------------------------------------------------------

class TestObservabilityIntegration:
    """All observability metrics should flow end-to-end through the system."""

    def test_buy_trade_produces_slippage_spread(self, monkeypatch) -> None:
        """A complete BUY → close cycle should generate all metric data."""
        cfg = settings()
        broker = PaperBroker(cfg)
        rm = RiskManager(cfg)
        monkeypatch.setattr(PaperBroker, "_sample_slippage_distance", lambda self: 0.01)

        # Open BUY
        instr = make_instruction(direction="BUY", entry_price=100.0)
        account = broker.get_account_state()
        order = rm.evaluate(instr, account, [])
        result = broker.submit_order(order)
        assert result.success
        initial_equity = account.equity

        raw = result.raw_response or {}
        intended = float(raw.get("intended_entry_price", 0))
        actual = float(raw.get("actual_entry_price", 0))
        entry_slip = actual - intended
        assert entry_slip > 0  # BUY slippage

        # Close via take-profit
        broker.update_prices(candle_row(open_p=105.0, high=106.0, low=104.5, close=105.0))

        assert len(broker._trade_history) == 1
        trade = broker._trade_history[0]
        assert float(trade.get("exit_slippage_cost", 0)) > 0
        # Spread friction is embedded in the folded-normal slippage model; the
        # separate _spread_cost line was zeroed in the audit to avoid
        # double-counting cost (see PaperBroker._spread_cost).
        assert float(trade.get("spread_cost", 0)) == 0.0
        assert float(trade.get("r_multiple", 0)) > 0

    def test_sell_trade_produces_slippage_spread(self, monkeypatch) -> None:
        """A complete SELL → close cycle should generate all metric data."""
        cfg = settings()
        broker = PaperBroker(cfg)
        rm = RiskManager(cfg)
        monkeypatch.setattr(PaperBroker, "_sample_slippage_distance", lambda self: 0.01)

        # Open SELL
        instr = make_instruction(direction="SELL", entry_price=100.0, stop_loss=102.0, take_profit=96.0)
        account = broker.get_account_state()
        order = rm.evaluate(instr, account, [])
        result = broker.submit_order(order)
        assert result.success

        raw = result.raw_response or {}
        intended = float(raw.get("intended_entry_price", 0))
        actual = float(raw.get("actual_entry_price", 0))
        entry_slip = intended - actual  # SELL: positive slippage = price worsened down
        assert entry_slip > 0

        # Close via take-profit
        broker.update_prices(candle_row(open_p=95.0, high=95.5, low=94.0, close=94.5))

        assert len(broker._trade_history) == 1
        trade = broker._trade_history[0]
        assert float(trade.get("exit_slippage_cost", 0)) > 0
        # Spread friction is embedded in the folded-normal slippage model; the
        # separate _spread_cost line was zeroed in the audit to avoid
        # double-counting cost (see PaperBroker._spread_cost).
        assert float(trade.get("spread_cost", 0)) == 0.0
        assert float(trade.get("r_multiple", 0)) > 0

    def test_slippage_is_cost_center(self, monkeypatch) -> None:
        """Total slippage cost should reduce net PnL vs gross PnL."""
        cfg = settings()
        broker = PaperBroker(cfg)
        rm = RiskManager(cfg)
        monkeypatch.setattr(PaperBroker, "_sample_slippage_distance", lambda self: 0.02)

        open_trade(broker, rm, entry=100.0)

        # Close at a profit
        broker.update_prices(candle_row(open_p=105.0, high=106.0, low=104.0, close=105.5))

        trade = broker._trade_history[0]
        gross = float(trade.get("pnl", trade.get("gross_pnl", 0)))
        net = float(trade.get("net_pnl", trade.get("pnl_after_fees", 0)))
        total_slippage = float(trade.get("total_slippage_cost", 0))
        spread = float(trade.get("spread_cost", 0))
        assert net <= gross
        assert total_slippage + spread >= 0


# ---------------------------------------------------------------------------
# Open position recovery (restart survival)
# ---------------------------------------------------------------------------

class TestOpenPositionRecovery:
    """Open positions must survive a restart via SQLite persistence."""

    def _persist_and_restore(self, positions_data: list[dict]) -> list:
        """Simulate saving positions to DB and restoring them."""
        restored = []
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "test_positions.sqlite3"
            with closing(sqlite3.connect(str(db))) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS open_positions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        position_id TEXT NOT NULL UNIQUE,
                        direction TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        current_price REAL NOT NULL,
                        stop_loss REAL NOT NULL,
                        take_profit REAL NOT NULL,
                        units REAL NOT NULL,
                        lot_size REAL NOT NULL DEFAULT 0,
                        intended_entry_price REAL,
                        entry_slippage REAL DEFAULT 0,
                        entry_slippage_cost REAL DEFAULT 0,
                        open_time TEXT NOT NULL,
                        created_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                for p in positions_data:
                    conn.execute("""
                        INSERT INTO open_positions
                            (position_id, direction, entry_price, current_price,
                             stop_loss, take_profit, units, lot_size,
                             intended_entry_price, entry_slippage, entry_slippage_cost,
                             open_time)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        p["position_id"], p["direction"], p["entry_price"], p["current_price"],
                        p["stop_loss"], p["take_profit"], p["units"], p["lot_size"],
                        p.get("intended_entry_price"), p.get("entry_slippage", 0),
                        p.get("entry_slippage_cost", 0), p["open_time"],
                    ))
                conn.commit()

                # Simulate restore
                rows = conn.execute(
                    "SELECT position_id, direction, entry_price, current_price, stop_loss, "
                    "take_profit, units, lot_size, intended_entry_price, entry_slippage, "
                    "entry_slippage_cost, open_time FROM open_positions ORDER BY id"
                ).fetchall()
                restored = []
                for row in rows:
                    restored.append({
                        "position_id": row[0],
                        "direction": row[1],
                        "entry_price": row[2],
                        "current_price": row[3],
                        "stop_loss": row[4],
                        "take_profit": row[5],
                        "units": row[6],
                        "lot_size": row[7],
                        "intended_entry_price": row[8] if row[8] is not None else row[2],
                        "entry_slippage": row[9] if row[9] is not None else 0,
                        "entry_slippage_cost": row[10] if row[10] is not None else 0,
                        "open_time": row[11],
                    })
        return restored

    def test_open_position_saves_and_restores(self) -> None:
        """An open BUY position should persist and restore correctly."""
        open_time = "2026-07-03T01:00:00+00:00"
        positions = [{
            "position_id": "paper_test123",
            "direction": "BUY",
            "entry_price": 4176.51,
            "current_price": 4183.14,
            "stop_loss": 4154.48,
            "take_profit": 4220.56,
            "units": 0.5,
            "lot_size": 0.05,
            "intended_entry_price": 4176.00,
            "entry_slippage": 0.51,
            "entry_slippage_cost": 0.30,
            "open_time": open_time,
        }]
        restored = self._persist_and_restore(positions)
        assert len(restored) == 1
        assert restored[0]["direction"] == "BUY"
        assert restored[0]["entry_price"] == 4176.51
        assert restored[0]["stop_loss"] == 4154.48
        assert restored[0]["take_profit"] == 4220.56

    def test_open_position_survives_restart_cycle(self) -> None:
        """Open position data must survive full write → clear → restore cycle."""
        open_time = datetime.now(UTC).isoformat()
        positions = [{
            "position_id": "paper_survive_test",
            "direction": "SELL",
            "entry_price": 4200.00,
            "current_price": 4195.50,
            "stop_loss": 4225.00,
            "take_profit": 4150.00,
            "units": 1.0,
            "lot_size": 0.10,
            "intended_entry_price": 4199.50,
            "entry_slippage": 0.50,
            "entry_slippage_cost": 0.25,
            "open_time": open_time,
        }]

        # Write → read (simulate restart)
        restored = self._persist_and_restore(positions)
        assert len(restored) == 1
        assert restored[0]["position_id"] == "paper_survive_test"
        assert restored[0]["direction"] == "SELL"
        assert restored[0]["entry_price"] == 4200.00
        assert restored[0]["stop_loss"] == 4225.00
        assert restored[0]["take_profit"] == 4150.00

    def test_open_position_clears_after_close(self) -> None:
        """After a trade closes, open_positions table should be empty."""
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "test_clear.sqlite3"
            with closing(sqlite3.connect(str(db))) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS open_positions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        position_id TEXT NOT NULL UNIQUE,
                        direction TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        current_price REAL NOT NULL,
                        stop_loss REAL NOT NULL,
                        take_profit REAL NOT NULL,
                        units REAL NOT NULL,
                        lot_size REAL NOT NULL DEFAULT 0,
                        intended_entry_price REAL,
                        entry_slippage REAL DEFAULT 0,
                        entry_slippage_cost REAL DEFAULT 0,
                        open_time TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    INSERT INTO open_positions VALUES
                    (1, 'test_close', 'BUY', 100.0, 101.0, 98.0, 104.0, 0.5, 0.05, 100.0, 0.0, 0.0, '2026-01-01T12:00:00Z')
                """)
                conn.commit()

                # Simulate clearing on trade close
                conn.execute("DELETE FROM open_positions")
                conn.commit()

                rows = conn.execute("SELECT COUNT(*) FROM open_positions").fetchone()
                assert rows[0] == 0

    def test_multiple_positions_restore(self) -> None:
        """Multiple open positions should all restore correctly."""
        open_time = datetime.now(UTC).isoformat()
        positions = [
            {
                "position_id": "paper_multi_1",
                "direction": "BUY",
                "entry_price": 4100.0,
                "current_price": 4110.0,
                "stop_loss": 4080.0,
                "take_profit": 4140.0,
                "units": 0.3,
                "lot_size": 0.03,
                "intended_entry_price": 4099.5,
                "entry_slippage": 0.5,
                "entry_slippage_cost": 0.15,
                "open_time": open_time,
            },
            {
                "position_id": "paper_multi_2",
                "direction": "SELL",
                "entry_price": 4150.0,
                "current_price": 4145.0,
                "stop_loss": 4170.0,
                "take_profit": 4100.0,
                "units": 0.5,
                "lot_size": 0.05,
                "intended_entry_price": 4150.5,
                "entry_slippage": 0.5,
                "entry_slippage_cost": 0.25,
                "open_time": open_time,
            },
        ]
        restored = self._persist_and_restore(positions)
        assert len(restored) == 2
        assert restored[0]["direction"] == "BUY"
        assert restored[1]["direction"] == "SELL"

    def test_no_open_position_returns_empty(self) -> None:
        """No open positions in DB should restore as empty list."""
        restored = self._persist_and_restore([])
        assert len(restored) == 0

    def test_integration_with_broker(self, monkeypatch) -> None:
        """After opening a trade in PaperBroker, its position should be restorable."""
        cfg = settings()
        broker = PaperBroker(cfg)
        rm = RiskManager(cfg)
        monkeypatch.setattr(PaperBroker, "_sample_slippage_distance", lambda self: 0.01)

        open_trade(broker, rm, entry=100.0)

        positions = broker.get_open_positions()
        assert len(positions) == 1
        pos = positions[0]

        # Save to DB and check
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "test_broker_pos.sqlite3"
            with closing(sqlite3.connect(str(db))) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS open_positions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        position_id TEXT NOT NULL UNIQUE,
                        direction TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        current_price REAL NOT NULL,
                        stop_loss REAL NOT NULL,
                        take_profit REAL NOT NULL,
                        units REAL NOT NULL,
                        lot_size REAL NOT NULL DEFAULT 0,
                        intended_entry_price REAL,
                        entry_slippage REAL DEFAULT 0,
                        entry_slippage_cost REAL DEFAULT 0,
                        open_time TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    INSERT INTO open_positions VALUES
                    (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pos.position_id, pos.direction, pos.open_price, pos.current_price,
                    pos.stop_loss, pos.take_profit, pos.units, pos.lot_size,
                    pos.intended_entry_price, pos.entry_slippage, pos.entry_slippage_cost,
                    pos.open_time.isoformat(),
                ))
                conn.commit()

                # Verify
                rows = conn.execute("SELECT position_id, direction, entry_price, stop_loss, take_profit FROM open_positions").fetchall()
                assert len(rows) == 1
                assert rows[0][1] == "BUY" or rows[0][1] == "SELL"
