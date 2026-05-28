from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aurum1.models.ensemble import SignalResult
from aurum1.signals import CandleRow, MachineMode, MachineState, StateMachine, TradeInstruction


def make_candle(
    close: float = 2330.0,
    open_: float = 2328.0,
    high: float = 2332.0,
    low: float = 2326.0,
    atr_14: float = 5.0,
    adx_14: float = 30.0,
    ema_9: float = 2329.0,
    ema_20: float = 2325.0,
    session_london: int = 1,
    session_ny: int = 0,
    timestamp: datetime | None = None,
) -> CandleRow:
    return CandleRow(
        timestamp=timestamp or datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
        atr_14=atr_14,
        adx_14=adx_14,
        ema_9=ema_9,
        ema_20=ema_20,
        session_london=session_london,
        session_ny=session_ny,
        session_overlap=1 if session_london and session_ny else 0,
    )


def make_signal(
    direction: str = "BUY",
    regime: str = "TRENDING_UP",
    raw_score: float = 0.75,
    regime_confidence: float = 0.80,
    sentiment_scalar: float = 0.2,
) -> SignalResult:
    return SignalResult(
        direction=direction,
        raw_score=raw_score,
        regime=regime,
        regime_confidence=regime_confidence,
        direction_signal=0.6 if direction == "BUY" else -0.6 if direction == "SELL" else 0.0,
        sentiment_scalar=sentiment_scalar,
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )


def make_sm(mode: MachineMode = MachineMode.RULE_REGIME) -> StateMachine:
    return StateMachine(
        {
            "signals": {
                "adx_threshold": 25,
                "min_pullback_candles": 1,
                "max_pullback_candles": 4,
                "armed_timeout_candles": 20,
                "window_expiry_candles": 6,
                "atr_sl_multiplier": 2.0,
                "atr_tp_multiplier": 3.0,
                "atr_breakout_buffer": 0.3,
                "require_session_filter": True,
            }
        },
        mode=mode,
    )


def arm_buy(sm: StateMachine, high: float = 2332.0) -> None:
    sm.on_candle(make_candle(high=high), make_signal(), is_blackout=False)


def arm_sell(sm: StateMachine, low: float = 2326.0) -> None:
    sm.on_candle(
        make_candle(open_=2334.0, close=2332.0, high=2336.0, low=low, ema_9=2325.0, ema_20=2329.0),
        make_signal(direction="SELL", regime="TRENDING_DOWN"),
        is_blackout=False,
    )


def open_buy_window(sm: StateMachine, high: float = 2332.0) -> None:
    arm_buy(sm, high=high)
    sm.on_candle(make_candle(open_=2332.0, close=2328.0), make_signal(), is_blackout=False)
    sm.on_candle(make_candle(open_=2328.0, close=2330.0), make_signal(), is_blackout=False)


def open_sell_window(sm: StateMachine, low: float = 2326.0) -> None:
    arm_sell(sm, low=low)
    sm.on_candle(
        make_candle(open_=2330.0, close=2332.0, ema_9=2325.0, ema_20=2329.0),
        make_signal(direction="SELL", regime="TRENDING_DOWN"),
        is_blackout=False,
    )
    sm.on_candle(
        make_candle(open_=2332.0, close=2330.0, ema_9=2325.0, ema_20=2329.0),
        make_signal(direction="SELL", regime="TRENDING_DOWN"),
        is_blackout=False,
    )


def test_scanning_to_armed_on_valid_buy_signal() -> None:
    sm = make_sm()

    sm.on_candle(make_candle(), make_signal(), is_blackout=False)

    assert sm.get_state() == MachineState.ARMED


def test_scanning_stays_on_flat_signal() -> None:
    sm = make_sm()

    sm.on_candle(make_candle(), make_signal(direction="FLAT"), is_blackout=False)

    assert sm.get_state() == MachineState.SCANNING


def test_rule_regime_blocks_buy_in_trending_down() -> None:
    sm = make_sm(MachineMode.RULE_REGIME)

    sm.on_candle(make_candle(), make_signal(direction="BUY", regime="TRENDING_DOWN"), is_blackout=False)

    assert sm.get_state() == MachineState.SCANNING


def test_rule_regime_allows_sell_in_trending_down() -> None:
    sm = make_sm(MachineMode.RULE_REGIME)

    sm.on_candle(
        make_candle(ema_9=2325.0, ema_20=2329.0),
        make_signal(direction="SELL", regime="TRENDING_DOWN"),
        is_blackout=False,
    )

    assert sm.get_state() == MachineState.ARMED


def test_rule_only_ignores_signal_uses_ema() -> None:
    sm = make_sm(MachineMode.RULE_ONLY)

    sm.on_candle(make_candle(ema_9=2329.0, ema_20=2325.0), make_signal(direction="FLAT"), is_blackout=False)

    assert sm.get_state() == MachineState.ARMED


def test_scanning_blocked_during_blackout() -> None:
    sm = make_sm()

    sm.on_candle(make_candle(), make_signal(), is_blackout=True)
    sm.on_candle(make_candle(timestamp=datetime(2026, 1, 1, 12, 15, tzinfo=UTC)), make_signal(), is_blackout=True)

    assert sm.get_state() == MachineState.SCANNING
    assert [entry["reason"] for entry in sm.get_cancellation_log()] == ["blackout_entry_blocked"]


def test_armed_counts_pullbacks() -> None:
    sm = make_sm()
    arm_buy(sm)

    sm.on_candle(make_candle(open_=2332.0, close=2328.0), make_signal(), is_blackout=False)
    sm.on_candle(make_candle(open_=2331.0, close=2329.0), make_signal(), is_blackout=False)

    assert sm.pullback_count == 2

    sm.on_candle(make_candle(open_=2329.0, close=2331.0), make_signal(), is_blackout=False)

    assert sm.get_state() == MachineState.WINDOW_OPEN


def test_armed_resets_on_max_pullbacks() -> None:
    sm = make_sm()
    arm_buy(sm)

    for index in range(5):
        sm.on_candle(
            make_candle(open_=2332.0 + index, close=2328.0 + index),
            make_signal(),
            is_blackout=False,
        )

    assert sm.get_state() == MachineState.SCANNING
    assert sm.get_cancellation_summary()["max_pullbacks_exceeded"] == 1


def test_armed_resets_on_timeout() -> None:
    sm = make_sm()
    arm_buy(sm)

    for index in range(21):
        sm.on_candle(
            make_candle(open_=2328.0, close=2330.0, timestamp=datetime(2026, 1, 1, 12, 15, tzinfo=UTC) + timedelta(minutes=15 * index)),
            make_signal(),
            is_blackout=False,
        )

    assert sm.get_state() == MachineState.SCANNING
    assert sm.get_cancellation_summary()["armed_timeout"] == 1


def test_window_open_breakout_buy_returns_instruction() -> None:
    sm = make_sm()
    open_buy_window(sm)

    instruction = sm.on_candle(make_candle(close=2335.0), make_signal(), is_blackout=False)

    assert isinstance(instruction, TradeInstruction)
    assert instruction.direction == "BUY"
    assert instruction.stop_loss < instruction.entry_price
    assert instruction.take_profit > instruction.entry_price
    assert sm.get_state() == MachineState.SCANNING


def test_window_open_breakout_sell_returns_instruction() -> None:
    sm = make_sm()
    open_sell_window(sm)

    instruction = sm.on_candle(
        make_candle(close=2324.0, ema_9=2325.0, ema_20=2329.0),
        make_signal(direction="SELL", regime="TRENDING_DOWN"),
        is_blackout=False,
    )

    assert isinstance(instruction, TradeInstruction)
    assert instruction.direction == "SELL"
    assert instruction.stop_loss > instruction.entry_price
    assert instruction.take_profit < instruction.entry_price


def test_window_expires_without_breakout() -> None:
    sm = make_sm()
    open_buy_window(sm)

    for index in range(7):
        sm.on_candle(
            make_candle(close=2330.0, timestamp=datetime(2026, 1, 1, 13, 0, tzinfo=UTC) + timedelta(minutes=15 * index)),
            make_signal(),
            is_blackout=False,
        )

    assert sm.get_state() == MachineState.SCANNING
    assert sm.get_cancellation_summary()["window_expired"] == 1


def test_blackout_freezes_armed_state() -> None:
    sm = make_sm()
    arm_buy(sm)
    sm.on_candle(make_candle(open_=2332.0, close=2328.0), make_signal(), is_blackout=False)

    for index in range(3):
        sm.on_candle(
            make_candle(open_=2330.0, close=2332.0, timestamp=datetime(2026, 1, 1, 12, 30, tzinfo=UTC) + timedelta(minutes=15 * index)),
            make_signal(),
            is_blackout=True,
        )

    assert sm.get_state() == MachineState.ARMED

    sm.on_candle(make_candle(open_=2328.0, close=2330.0), make_signal(), is_blackout=False)

    assert sm.get_state() == MachineState.WINDOW_OPEN


def test_sl_tp_math_buy() -> None:
    sm = make_sm()
    open_buy_window(sm, high=2328.5)

    instruction = sm.on_candle(make_candle(close=2332.0, atr_14=5.0), make_signal(), is_blackout=False)

    assert instruction is not None
    assert instruction.entry_price == pytest.approx(2330.0)
    assert instruction.stop_loss == pytest.approx(2320.0)
    assert instruction.take_profit == pytest.approx(2345.0)


def test_sl_tp_math_sell() -> None:
    sm = make_sm()
    open_sell_window(sm, low=2331.5)

    instruction = sm.on_candle(
        make_candle(close=2328.0, atr_14=5.0, ema_9=2325.0, ema_20=2329.0),
        make_signal(direction="SELL", regime="TRENDING_DOWN"),
        is_blackout=False,
    )

    assert instruction is not None
    assert instruction.entry_price == pytest.approx(2330.0)
    assert instruction.stop_loss == pytest.approx(2340.0)
    assert instruction.take_profit == pytest.approx(2315.0)


def test_cancellation_summary_counts() -> None:
    sm = make_sm()
    for _ in range(2):
        open_buy_window(sm)
        for _ in range(7):
            sm.on_candle(make_candle(close=2330.0), make_signal(), is_blackout=False)
    arm_buy(sm)
    for _ in range(5):
        sm.on_candle(make_candle(open_=2332.0, close=2328.0), make_signal(), is_blackout=False)

    summary = sm.get_cancellation_summary()

    assert summary == {"window_expired": 2, "max_pullbacks_exceeded": 1}


def test_trade_instruction_includes_machine_mode() -> None:
    sm = make_sm(MachineMode.RULE_REGIME)
    open_buy_window(sm)

    instruction = sm.on_candle(make_candle(close=2335.0), make_signal(), is_blackout=False)

    assert instruction is not None
    assert instruction.machine_mode == "rule_regime"


def test_full_cycle_scan_arm_window_execute() -> None:
    sm = make_sm()

    arm_buy(sm)
    sm.on_candle(make_candle(open_=2332.0, close=2328.0), make_signal(), is_blackout=False)
    sm.on_candle(make_candle(open_=2328.0, close=2330.0), make_signal(), is_blackout=False)
    instruction = sm.on_candle(make_candle(close=2335.0), make_signal(), is_blackout=False)

    assert isinstance(instruction, TradeInstruction)
    assert sm.get_state() == MachineState.SCANNING
    assert instruction.regime is not None
    assert instruction.confidence > 0.0
    assert instruction.timestamp is not None
    assert instruction.signal_score > 0.0


def test_reset_returns_to_scanning() -> None:
    sm = make_sm()
    arm_buy(sm)

    sm.reset()

    assert sm.get_state() == MachineState.SCANNING
    assert sm.armed_candle is None
