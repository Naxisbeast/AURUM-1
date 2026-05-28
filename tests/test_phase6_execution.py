from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

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


def make_candle(high: float = 2332.0, low: float = 2326.0, close: float = 2330.0) -> CandleRow:
    return CandleRow(
        timestamp=datetime(2026, 1, 1, 12, 15, tzinfo=UTC),
        open=2328.0,
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

    broker.submit_order(order)
    positions = broker.get_open_positions()

    assert len(positions) == 1
    assert positions[0].direction == "BUY"
    assert positions[0].stop_loss == order.instruction.stop_loss


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


def test_paper_broker_sl_uses_exact_sl_price() -> None:
    broker = make_paper_engine().broker
    broker.submit_order(make_risk_order(direction="BUY", stop_loss=2320.0))

    broker.update_prices(make_candle(low=2310.0))

    assert broker._trade_history[-1]["exit"] == 2320.0


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
