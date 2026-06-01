from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aurum1.execution import PaperBroker
from aurum1.risk.manager import RiskManager
from aurum1.signals import TradeInstruction
from aurum1.backtesting.engine import PendingBacktestOrder, _process_pending_orders
from aurum1.signals import CandleRow
import importlib.util
from pathlib import Path

# Load the settings() helper from the existing test file to avoid package import issues
spec = importlib.util.spec_from_file_location(
    "phase7", Path(__file__).parent / "test_phase7_backtest.py"
)
phase7 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase7)
settings = phase7.settings


def test_slippage_rebases_sl_tp(monkeypatch) -> None:
    cfg = settings()
    broker = PaperBroker(cfg)
    risk_manager = RiskManager(cfg)
    # Make slippage deterministic and non-zero
    monkeypatch.setattr(PaperBroker, "_sample_slippage_distance", lambda self: 0.015)

    instruction = TradeInstruction(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        direction="BUY",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        atr_at_entry=2.0,
        signal_score=0.8,
        regime="TRENDING_UP",
        confidence=0.9,
        machine_mode="rule_regime",
    )

    account = broker.get_account_state()
    order = risk_manager.evaluate(instruction, account, [])
    result = broker.submit_order(order)
    assert result.success is True
    pos = broker.get_open_positions()[0]
    # original SL distance = 5.0
    assert pos.stop_loss == pytest.approx(result.fill_price - 5.0)
    assert pos.take_profit == pytest.approx(result.fill_price + 10.0)


def test_sizing_uses_final_pending_fill_price(monkeypatch) -> None:
    cfg = settings()
    broker = PaperBroker(cfg)
    risk_manager = RiskManager(cfg)

    # Build an initial instruction where entry is 100 and stop 95
    instruction = TradeInstruction(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        direction="BUY",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        atr_at_entry=2.0,
        signal_score=0.8,
        regime="TRENDING_UP",
        confidence=0.9,
        machine_mode="rule_regime",
    )

    account = broker.get_account_state()
    pre_order = risk_manager.evaluate(instruction, account, [])

    pending = PendingBacktestOrder(order=pre_order, signal_bar=0, signal_time=instruction.timestamp, meta={})

    # simulate a candle that gaps the open above entry so fill_price = open
    candle = CandleRow(
        timestamp=datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
        open=103.0,
        high=105.0,
        low=102.0,
        close=104.0,
        volume=1.0,
        atr_14=2.0,
        adx_14=30.0,
        ema_9=101.0,
        ema_20=100.0,
        session_london=1,
        session_ny=0,
        session_overlap=0,
    )

    pending_orders = [pending]
    open_meta = {}
    rejection_reasons = {}

    # process pending orders; our patched engine should re-evaluate sizing at fill
    rejected = _process_pending_orders(
        pending_orders=pending_orders,
        candle=candle,
        bar_index=1,
        timestamp=candle.timestamp,
        execution=type("Exec", (), {"broker": broker, "execute": lambda self, o: broker.submit_order(o)})(),
        open_meta=open_meta,
        rejection_reasons=rejection_reasons,
        fill_timeout_candles=5,
        risk_manager=risk_manager,
    )

    # After processing, one position should be open
    positions = broker.get_open_positions()
    assert len(positions) == 1
    pos = positions[0]
    # Units should match the risk manager recomputed size using the final entry/SL
    # compute expected units via risk manager evaluate on adjusted instruction
    # emulate final fill at 103.0 -> rebased stop should be 98.0 (entry - 5)
    adjusted_instruction = TradeInstruction(
        timestamp=instruction.timestamp,
        direction=instruction.direction,
        entry_price=103.0,
        stop_loss=98.0,
        take_profit=113.0,
        atr_at_entry=instruction.atr_at_entry,
        signal_score=instruction.signal_score,
        regime=instruction.regime,
        confidence=instruction.confidence,
        machine_mode=instruction.machine_mode,
    )
    expected = risk_manager.evaluate(adjusted_instruction, broker.get_account_state(), list(broker._trade_history))
    assert pos.units == pytest.approx(expected.units)