from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aurum1.execution import ExecutionEngine, OandaBroker, PaperBroker
from aurum1.risk import RiskOrder
from aurum1.signals import CandleRow, TradeInstruction


def make_instruction(
    direction: str = "BUY",
    entry_price: float = 2330.0,
    stop_loss: float = 2320.0,
    take_profit: float = 2345.0,
) -> TradeInstruction:
    return TradeInstruction(
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        atr_at_entry=5.0,
        signal_score=0.75,
        regime="TRENDING_UP" if direction == "BUY" else "TRENDING_DOWN",
        confidence=0.8,
        machine_mode="rule_regime",
    )


def make_risk_order(
    approved: bool = True,
    direction: str = "BUY",
    entry_price: float = 2330.0,
    stop_loss: float = 2320.0,
    take_profit: float = 2345.0,
    lot_size: float = 0.10,
    rejection_reason: str | None = None,
) -> RiskOrder:
    return RiskOrder(
        instruction=make_instruction(direction, entry_price, stop_loss, take_profit),
        lot_size=lot_size,
        risk_amount=100.0,
        risk_pct=1.0,
        kelly_fraction=0.25,
        approved=approved,
        rejection_reason=rejection_reason,
        portfolio_risk_after=1.0,
        warnings=["low_confidence"] if not approved else [],
    )


def settings_for(db_path: Path, overrides: dict | None = None) -> dict:
    settings = {
        "general": {"random_seed": 7},
        "data": {"db_path": str(db_path)},
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
    if overrides:
        for section, values in overrides.items():
            settings.setdefault(section, {}).update(values)
    return settings


def make_paper_engine(overrides: dict | None = None) -> ExecutionEngine:
    tempdir = tempfile.TemporaryDirectory()
    engine = ExecutionEngine(settings_for(Path(tempdir.name) / "aurum.sqlite3", overrides))
    engine._tempdir = tempdir  # type: ignore[attr-defined]
    return engine


def make_candle(
    high: float = 2332.0,
    low: float = 2326.0,
    close: float = 2330.0,
    open_: float = 2328.0,
) -> CandleRow:
    return CandleRow(
        timestamp=datetime(2026, 1, 1, 12, 15, tzinfo=UTC),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
        atr_14=5.0,
        adx_14=30.0,
        ema_9=2329.0,
        ema_20=2325.0,
        session_london=1,
        session_ny=0,
        session_overlap=0,
    )


def test_paper_broker_fills_buy_order() -> None:
    engine = make_paper_engine()
    broker = engine.broker

    result = broker.submit_order(make_risk_order(direction="BUY"))

    assert isinstance(broker, PaperBroker)
    assert result.success is True
    assert result.direction == "BUY"
    assert result.fill_price is not None and result.fill_price >= 2330.0
    assert result.broker == "paper"


def test_paper_broker_fills_sell_order() -> None:
    broker = make_paper_engine().broker

    result = broker.submit_order(make_risk_order(direction="SELL", stop_loss=2340.0, take_profit=2315.0))

    assert result.success is True
    assert result.fill_price is not None and result.fill_price <= 2330.0


def test_paper_broker_rejects_unapproved_order() -> None:
    broker = make_paper_engine().broker

    result = broker.submit_order(make_risk_order(approved=False, rejection_reason="daily_loss_kill"))

    assert result.success is False
    assert result.rejection_reason == "daily_loss_kill"


def test_paper_broker_records_position() -> None:
    broker = make_paper_engine().broker
    order = make_risk_order(direction="BUY")

    result = broker.submit_order(order)
    positions = broker.get_open_positions()

    assert len(positions) == 1
    assert positions[0].direction == "BUY"
    original_sl_distance = abs(order.instruction.entry_price - order.instruction.stop_loss)
    original_tp_distance = abs(order.instruction.take_profit - order.instruction.entry_price)
    assert positions[0].stop_loss == pytest.approx(float(result.fill_price) - original_sl_distance)
    assert positions[0].take_profit == pytest.approx(float(result.fill_price) + original_tp_distance)
    assert positions[0].intended_entry_price == pytest.approx(order.instruction.entry_price)


def test_paper_broker_tp_closes_position() -> None:
    broker = make_paper_engine().broker
    broker.submit_order(make_risk_order(direction="BUY", take_profit=2345.0))

    broker.update_prices(make_candle(high=2346.0))

    assert broker.get_open_positions() == []
    assert broker.get_account_state().equity > 10000.0


def test_paper_broker_sl_closes_position() -> None:
    broker = make_paper_engine().broker
    broker.submit_order(make_risk_order(direction="BUY", stop_loss=2320.0))

    broker.update_prices(make_candle(low=2319.0))

    assert broker.get_open_positions() == []
    assert broker.get_account_state().equity < 10000.0


def test_paper_broker_sl_records_intended_and_actual_exit_price() -> None:
    broker = make_paper_engine({"execution": {"slippage_std_pips": 0.0}}).broker
    broker.submit_order(make_risk_order(direction="BUY", stop_loss=2320.0))

    broker.update_prices(make_candle(low=2310.0))

    assert broker._trade_history[-1]["intended_exit"] == 2320.0
    assert broker._trade_history[-1]["exit"] == 2320.0


def test_paper_broker_exit_slippage_worsens_buy_exit() -> None:
    broker = make_paper_engine().broker
    order = make_risk_order(direction="BUY", stop_loss=2320.0)
    broker.submit_order(order)

    broker.update_prices(make_candle(low=2310.0))

    trade = broker._trade_history[-1]
    original_sl_distance = abs(order.instruction.entry_price - order.instruction.stop_loss)
    assert trade["intended_exit"] == pytest.approx(trade["actual_entry"] - original_sl_distance)
    assert trade["actual_exit"] <= trade["intended_exit"]
    assert trade["exit_slippage"] >= 0.0
    assert trade["exit_slippage_cost"] >= 0.0
    assert trade["total_slippage_cost"] == pytest.approx(trade["entry_slippage_cost"] + trade["exit_slippage_cost"])


def test_paper_broker_exit_slippage_worsens_sell_exit() -> None:
    broker = make_paper_engine().broker
    order = make_risk_order(direction="SELL", stop_loss=2340.0, take_profit=2315.0)
    broker.submit_order(order)

    broker.update_prices(make_candle(high=2341.0, low=2328.0, close=2335.0))

    trade = broker._trade_history[-1]
    original_sl_distance = abs(order.instruction.entry_price - order.instruction.stop_loss)
    assert trade["intended_exit"] == pytest.approx(trade["actual_entry"] + original_sl_distance)
    assert trade["actual_exit"] >= trade["intended_exit"]
    assert trade["exit_slippage"] >= 0.0
    assert trade["exit_slippage_cost"] >= 0.0


def test_paper_broker_buy_stop_gap_exits_at_adverse_open() -> None:
    broker = make_paper_engine({"execution": {"slippage_std_pips": 0.0, "paper_spread_pips": 0.0}}).broker
    order = make_risk_order(direction="BUY", entry_price=100.0, stop_loss=95.0, take_profit=110.0, lot_size=0.01)
    order.units = 1.0
    broker.submit_order(order)

    broker.update_prices(make_candle(open_=90.0, high=93.0, low=89.0, close=91.0))

    trade = broker._trade_history[-1]
    assert trade["reason"] == "stop_loss_gap"
    assert trade["intended_exit"] == pytest.approx(90.0)
    assert trade["gross_pnl"] == pytest.approx(-10.0)


def test_paper_broker_sell_stop_gap_exits_at_adverse_open() -> None:
    broker = make_paper_engine({"execution": {"slippage_std_pips": 0.0, "paper_spread_pips": 0.0}}).broker
    order = make_risk_order(direction="SELL", entry_price=100.0, stop_loss=105.0, take_profit=90.0, lot_size=0.01)
    order.units = 1.0
    broker.submit_order(order)

    broker.update_prices(make_candle(open_=110.0, high=112.0, low=107.0, close=110.0))

    trade = broker._trade_history[-1]
    assert trade["reason"] == "stop_loss_gap"
    assert trade["intended_exit"] == pytest.approx(110.0)
    assert trade["gross_pnl"] == pytest.approx(-10.0)


def test_paper_broker_spread_cost_zeroed_after_audit() -> None:
    """Spread cost is zeroed out — slippage model captures all friction.

    As of Jul 20, 2026, _spread_cost() returns 0.0 because the folded-normal
    slippage model (always adverse) on entry AND exit prices already captures
    crossing the spread. Adding a separate spread line item double-counts
    friction. See broker.py:_spread_cost docstring for the full reasoning.
    """
    broker = make_paper_engine({"execution": {"slippage_std_pips": 0.0, "paper_spread_pips": 1.5}}).broker
    broker.submit_order(make_risk_order(direction="BUY", entry_price=2330.0, take_profit=2345.0, lot_size=0.10))

    broker.update_prices(make_candle(high=2346.0))

    trade = broker._trade_history[-1]
    assert trade["gross_pnl"] == pytest.approx(150.0)
    assert trade["spread_cost"] == 0.0, "Spread cost zeroed after audit (slippage model captures friction)"
    assert trade["net_pnl"] == trade["gross_pnl"], "net_pnl = gross_pnl when slippage=0"
    assert broker.get_account_state().equity == pytest.approx(10150.0)


def test_paper_broker_one_unit_one_pip_pnl_atomic_sanity() -> None:
    broker = make_paper_engine({"execution": {"slippage_std_pips": 0.0, "paper_spread_pips": 0.0}}).broker
    order = make_risk_order(
        direction="BUY",
        entry_price=2300.00,
        stop_loss=2299.00,
        take_profit=2301.00,
        lot_size=0.01,
    )
    order.units = 1.0
    result = broker.submit_order(order)

    assert result.order_id is not None
    broker._close_position_at_price(result.order_id, 2300.01, "atomic_unit_test")

    trade = broker._trade_history[-1]
    assert trade["units"] == pytest.approx(1.0)
    assert trade["lot_size"] == pytest.approx(0.01)
    assert trade["gross_pnl"] == pytest.approx(0.01)
    assert trade["net_pnl"] == pytest.approx(0.01)
    assert broker.get_account_state().equity == pytest.approx(10000.01)


def test_paper_broker_spread_check_at_execution() -> None:
    broker = make_paper_engine({"execution": {"paper_spread_pips": 5.0}}).broker

    result = broker.submit_order(make_risk_order())

    assert result.success is False
    assert result.rejection_reason == "spread_too_wide_at_execution"


def test_paper_account_state_reflects_open_positions() -> None:
    broker = make_paper_engine().broker
    broker.submit_order(make_risk_order(direction="BUY"))
    broker.submit_order(make_risk_order(direction="SELL", stop_loss=2340.0, take_profit=2315.0))

    account = broker.get_account_state()

    assert account.open_trade_count == 2


def test_paper_daily_pnl_updates_on_close() -> None:
    broker = make_paper_engine().broker
    broker.submit_order(make_risk_order(direction="BUY", take_profit=2345.0))

    broker.update_prices(make_candle(high=2346.0))

    assert broker.get_account_state().daily_pnl > 0


def test_execution_engine_routes_to_paper() -> None:
    engine = make_paper_engine()

    result = engine.execute(make_risk_order())

    assert result.broker == "paper"


def test_execution_engine_logs_to_sqlite() -> None:
    engine = make_paper_engine()

    engine.execute(make_risk_order(direction="BUY"))
    with closing(sqlite3.connect(engine.db_path)) as conn:
        rows = conn.execute("SELECT direction, status FROM trades_log").fetchall()

    assert rows == [("BUY", "filled")]


def test_execution_engine_logs_rejection() -> None:
    engine = make_paper_engine()

    engine.execute(make_risk_order(approved=False, rejection_reason="daily_loss_kill"))
    with closing(sqlite3.connect(engine.db_path)) as conn:
        rows = conn.execute("SELECT status, payload_json FROM trades_log").fetchall()

    assert rows[0][0] == "rejected"
    assert "daily_loss_kill" in rows[0][1]
    assert "low_confidence" in rows[0][1]


def test_oanda_broker_submit_mocked(monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    broker.get_current_spread_pips = lambda instrument: 1.0  # type: ignore[method-assign]
    broker._submit_limit_order = lambda data: {  # type: ignore[method-assign]
        "orderFillTransaction": {"id": "123", "price": "2330.12", "time": "2026-01-01T12:00:00Z"}
    }

    result = broker.submit_order(make_risk_order())

    assert result.success is True
    assert result.order_id == "123"
    assert result.broker == "oanda"


def test_oanda_sell_order_payload_uses_negative_units(monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    order = make_risk_order(direction="SELL", stop_loss=2340.0, take_profit=2315.0, lot_size=0.10)
    order.units = 10.0

    payload = broker._order_payload(order)

    assert payload["units"] == "-10"


def test_oanda_practice_requires_oanda_orders_interlock(monkeypatch) -> None:
    monkeypatch.delenv("ALLOW_OANDA_ORDERS", raising=False)
    monkeypatch.setenv("OANDA_ENV", "practice")

    try:
        OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    except RuntimeError as exc:
        assert "ALLOW_OANDA_ORDERS" in str(exc)
    else:
        raise AssertionError("Expected OandaBroker practice mode to require ALLOW_OANDA_ORDERS")


def test_oanda_live_requires_live_trading_interlock(monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    monkeypatch.delenv("ALLOW_LIVE_TRADING", raising=False)
    monkeypatch.setenv("OANDA_ENV", "live")

    try:
        OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    except RuntimeError as exc:
        assert "ALLOW_LIVE_TRADING" in str(exc)
    else:
        raise AssertionError("Expected OandaBroker live mode to require ALLOW_LIVE_TRADING")


# ---------------------------------------------------------------------------
# Extended OandaBroker tests
# ---------------------------------------------------------------------------


def test_oanda_broker_rejects_unapproved_order(monkeypatch) -> None:
    """OandaBroker should reject orders not approved by RiskManager."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    broker.get_current_spread_pips = lambda instrument: 1.0  # type: ignore[method-assign]

    result = broker.submit_order(make_risk_order(approved=False, rejection_reason="daily_loss_kill"))

    assert result.success is False
    assert result.rejection_reason is not None  # broker preserves original rejection reason


def test_oanda_broker_rejects_wide_spread(monkeypatch) -> None:
    """OandaBroker should reject when spread exceeds max."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}, "risk": {"max_spread_pips": 0.5}}))
    broker.get_current_spread_pips = lambda instrument: 2.0  # type: ignore[method-assign]

    result = broker.submit_order(make_risk_order())
    assert result.success is False
    assert result.rejection_reason == "spread_too_wide_at_execution"


def test_oanda_broker_handles_fill_timeout(monkeypatch) -> None:
    """OandaBroker should handle missing orderFillTransaction."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    broker.get_current_spread_pips = lambda instrument: 1.0  # type: ignore[method-assign]
    broker._submit_limit_order = lambda data: {}  # type: ignore[method-assign]  # no fill transaction

    result = broker.submit_order(make_risk_order())
    assert result.success is False
    assert result.rejection_reason == "fill_timeout"


def test_oanda_broker_buy_payload(monkeypatch) -> None:
    """BUY order payload should have positive units."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    order = make_risk_order(direction="BUY", stop_loss=2320.0, take_profit=2345.0)
    order.units = 10.0

    payload = broker._order_payload(order)

    assert payload["type"] == "LIMIT"
    assert payload["instrument"] == "XAU_USD"
    assert payload["units"] == "10"
    assert "stopLossOnFill" in payload
    assert "takeProfitOnFill" in payload


def test_oanda_broker_sell_payload_negative_units(monkeypatch) -> None:
    """SELL order payload should have negative units."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    order = make_risk_order(direction="SELL", stop_loss=2340.0, take_profit=2315.0)
    order.units = 10.0

    payload = broker._order_payload(order)
    assert payload["units"] == "-10"


def test_oanda_broker_payload_time_in_force_is_gtc(monkeypatch) -> None:
    """OANDA orders should use GTC time-in-force."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    payload = broker._order_payload(make_risk_order())
    assert payload["timeInForce"] == "GTC"


def test_oanda_broker_close_position_mocked(monkeypatch) -> None:
    """OandaBroker should handle close_position with mocked response."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    broker._close_oanda_position = lambda position_id: {  # type: ignore[method-assign]
        "longOrderFillTransaction": {
            "id": "close_123",
            "price": "2335.00",
            "units": "10",
            "time": "2026-01-01T13:00:00Z",
        }
    }

    result = broker.close_position("some_position", "manual_close")

    assert result.success is True
    assert result.order_id == "close_123"
    assert result.fill_price == 2335.00
    assert result.broker == "oanda"


def test_oanda_broker_get_account_state_mocked(monkeypatch) -> None:
    """OandaBroker should return AccountState from mocked API."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    monkeypatch.setenv("OANDA_API_KEY", "test_key_12345")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    broker._account_summary = lambda: {  # type: ignore[method-assign]
        "account": {
            "NAV": "10500.00",
            "balance": "10500.00",
            "openTradeCount": 1,
        }
    }
    broker._open_positions = lambda: {"positions": []}  # type: ignore[method-assign]
    broker._pricing = lambda instrument: {  # type: ignore[method-assign]
        "prices": [{"bids": [{"price": "2330.00"}], "asks": [{"price": "2331.50"}]}]
    }

    state = broker.get_account_state()

    assert state.equity == 10500.0
    assert state.balance == 10500.0
    assert state.open_trade_count == 1


def test_oanda_broker_spread_from_pricing(monkeypatch) -> None:
    """OandaBroker should compute spread from bid/ask pricing."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    broker._pricing = lambda instrument: {  # type: ignore[method-assign]
        "prices": [{
            "bids": [{"price": "2330.00"}],
            "asks": [{"price": "2331.50"}],
        }]
    }

    spread = broker.get_current_spread_pips("XAU_USD")
    # (2331.50 - 2330.00) / 0.01 = 150 pips
    assert spread == 150.0


def test_oanda_broker_get_open_positions_mocked(monkeypatch) -> None:
    """OandaBroker should parse open positions from API response."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    broker._open_positions = lambda: {  # type: ignore[method-assign]
        "positions": [{
            "instrument": "XAU_USD",
            "long": {"units": "10", "averagePrice": "2330.00", "unrealizedPL": "50.00"},
            "short": {"units": "0", "averagePrice": "0.00", "unrealizedPL": "0.00"},
        }]
    }

    positions = broker.get_open_positions()

    assert len(positions) == 1
    assert positions[0].direction == "BUY"
    assert positions[0].units == 10.0
    assert positions[0].open_price == 2330.0


def test_oanda_broker_open_positions_empty(monkeypatch) -> None:
    """OandaBroker should return empty list when no positions."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    broker._open_positions = lambda: {"positions": []}  # type: ignore[method-assign]

    positions = broker.get_open_positions()
    assert len(positions) == 0


def test_oanda_broker_close_position_no_fill(monkeypatch) -> None:
    """OandaBroker should handle close when no fill transaction."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    broker = OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    broker._close_oanda_position = lambda position_id: {}  # type: ignore[method-assign]

    result = broker.close_position("some_pos", "test")
    assert result.success is True  # still succeeds
    assert result.fill_price is None


def test_oanda_broker_live_mode_interlock(monkeypatch) -> None:
    """Live mode without ALLOW_LIVE_TRADING should be blocked."""
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")
    monkeypatch.delenv("ALLOW_LIVE_TRADING", raising=False)
    monkeypatch.setenv("OANDA_ENV", "live")

    try:
        OandaBroker(settings_for(Path("unused.sqlite3"), {"broker": {"paper_trade": False}}))
    except RuntimeError as exc:
        assert "ALLOW_LIVE_TRADING" in str(exc)
    else:
        raise AssertionError("Expected live mode to be blocked")


def test_close_all_positions_paper() -> None:
    engine = make_paper_engine()
    for _ in range(3):
        engine.execute(make_risk_order(direction="BUY"))

    results = engine.close_all_positions("shutdown")

    assert engine.broker.get_open_positions() == []
    assert len(results) == 3
    with closing(sqlite3.connect(engine.db_path)) as conn:
        closed_count = conn.execute("SELECT COUNT(*) FROM trades_log WHERE status='closed'").fetchone()[0]
    assert closed_count == 3
