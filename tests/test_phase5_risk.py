from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aurum1.instruments import InstrumentSpec
from aurum1.risk import AccountState, RiskManager, RiskOrder
from aurum1.signals import TradeInstruction


def make_account(
    equity: float = 10000.0,
    balance: float = 10000.0,
    open_trade_count: int = 0,
    daily_pnl: float = 0.0,
    peak_equity_30d: float = 10000.0,
    current_spread_pips: float = 1.5,
    open_risk_pct: float = 0.0,
) -> AccountState:
    return AccountState(
        equity=equity,
        balance=balance,
        open_trade_count=open_trade_count,
        daily_pnl=daily_pnl,
        peak_equity_30d=peak_equity_30d,
        current_spread_pips=current_spread_pips,
        open_risk_pct=open_risk_pct,
    )


def make_instruction(
    direction: str = "BUY",
    entry_price: float = 2330.0,
    stop_loss: float = 2320.0,
    take_profit: float = 2345.0,
    atr_at_entry: float = 5.0,
    regime: str = "TRENDING_UP",
    signal_score: float = 0.75,
    confidence: float = 0.80,
) -> TradeInstruction:
    return TradeInstruction(
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        atr_at_entry=atr_at_entry,
        signal_score=signal_score,
        regime=regime,
        confidence=confidence,
        machine_mode="rule_regime",
    )


def make_rm(overrides: dict | None = None) -> RiskManager:
    settings = {
        "risk": {
            "risk_per_trade_pct": 0.01,
            "kelly_min_trades": 20,
            "kelly_default_fraction": 0.25,
            "kelly_cap": 0.25,
            "kelly_max_fraction": 0.25,
            "max_spread_pips": 3.0,
            "daily_loss_kill_pct": 0.03,
            "total_drawdown_kill_pct": 0.08,
            "drawdown_recovery_threshold_pct": 0.05,
            "max_portfolio_risk_pct": 3.0,
            "pip_value_per_lot": 1.0,
            "pip_size": 0.01,
            "min_lot_size": 0.01,
            "max_lot_size": 10.0,
            "lot_step": 0.01,
        }
    }
    if overrides:
        settings["risk"].update(overrides)
    return RiskManager(settings)


def test_lot_size_calculated_correctly() -> None:
    order = make_rm().evaluate(make_instruction(), make_account(), [])

    expected = 0.03

    assert order.lot_size == pytest.approx(expected, abs=0.001)
    assert order.approved is True


def test_xauusd_one_unit_pnl_matches_oanda_convention() -> None:
    spec = InstrumentSpec.from_settings({})

    assert spec.pnl("BUY", 2300.0, 2301.0, 1.0) == pytest.approx(1.0)
    assert spec.pnl("SELL", 2300.0, 2299.0, 1.0) == pytest.approx(1.0)
    assert spec.pip_value_per_lot == pytest.approx(1.0)


def test_xauusd_one_unit_one_pip_pnl_matches_oanda_convention() -> None:
    spec = InstrumentSpec.from_settings({})

    assert spec.pip_size == pytest.approx(0.01)
    assert spec.pip_value_per_unit == pytest.approx(0.01)
    assert spec.pnl("BUY", 2300.00, 2300.01, 1.0) == pytest.approx(0.01)
    assert spec.pnl("SELL", 2300.00, 2299.99, 1.0) == pytest.approx(0.01)


def test_xauusd_risk_sizing_converts_to_oanda_units() -> None:
    order = make_rm().evaluate(make_instruction(entry_price=2330.0, stop_loss=2320.0), make_account(), [])

    assert order.units == pytest.approx(3.0)
    assert order.lot_size == pytest.approx(0.03)
    assert order.risk_amount == pytest.approx(30.0)


def test_kelly_uses_default_when_insufficient_history() -> None:
    order = make_rm().evaluate(make_instruction(), make_account(), [])

    assert order.kelly_fraction == pytest.approx(0.25)


def test_kelly_computed_from_trade_history() -> None:
    history = [{"pnl": 100.0, "direction": "BUY", "entry": 1.0, "exit": 2.0}] * 18
    history += [{"pnl": -80.0, "direction": "SELL", "entry": 2.0, "exit": 1.0}] * 12

    order = make_rm().evaluate(make_instruction(), make_account(), history)
    win_rate = 18 / 30
    win_loss_ratio = 100.0 / 80.0
    expected = min(max(0.0, win_rate - (1 - win_rate) / win_loss_ratio) * 0.25, 0.25)

    assert 0.0 < order.kelly_fraction <= 0.25
    assert order.kelly_fraction == pytest.approx(expected, abs=0.001)


def test_kelly_prefers_net_pnl_after_costs() -> None:
    history = [
        {"pnl": 10.0, "pnl_after_fees": -1.0, "net_pnl": -1.0, "direction": "BUY", "entry": 1.0, "exit": 2.0}
    ] * 25

    order = make_rm().evaluate(make_instruction(), make_account(), history)

    assert order.kelly_fraction == 0.0


def test_spread_kill_rejects_trade() -> None:
    order = make_rm().evaluate(make_instruction(), make_account(current_spread_pips=4.0), [])

    assert order.approved is False
    assert order.rejection_reason == "spread_too_wide"


def test_daily_loss_kill_rejects_trade() -> None:
    order = make_rm().evaluate(make_instruction(), make_account(daily_pnl=-350.0), [])

    assert order.approved is False
    assert order.rejection_reason == "daily_loss_kill"


def test_total_drawdown_kill_rejects_trade() -> None:
    order = make_rm().evaluate(make_instruction(), make_account(equity=9100.0, peak_equity_30d=10000.0), [])

    assert order.approved is False
    assert order.rejection_reason == "total_drawdown_kill"


def test_portfolio_risk_limit_rejects_trade() -> None:
    order = make_rm().evaluate(make_instruction(), make_account(open_risk_pct=2.8), [])

    assert order.approved is False
    assert order.rejection_reason == "portfolio_risk_limit"


def test_regime_correlation_conflict_rejects() -> None:
    instruction = make_instruction(direction="BUY", regime="TRENDING_DOWN")

    order = make_rm().evaluate(instruction, make_account(), [])

    assert order.approved is False
    assert order.rejection_reason == "regime_correlation_conflict"


def test_drawdown_recovery_halves_risk() -> None:
    order = make_rm().evaluate(make_instruction(), make_account(equity=9400.0, balance=9400.0), [])
    full_risk_amount = 9400.0 * 0.01 * 0.25

    assert order.approved is True
    assert order.recovery_mode is True
    assert order.risk_amount < full_risk_amount * 0.55


def test_lot_size_clamped_to_min() -> None:
    order = make_rm().evaluate(make_instruction(), make_account(equity=100.0, balance=100.0, peak_equity_30d=100.0), [])

    assert order.lot_size == pytest.approx(0.01)


def test_lot_size_clamped_to_max() -> None:
    order = make_rm().evaluate(
        make_instruction(),
        make_account(equity=10_000_000.0, balance=10_000_000.0, peak_equity_30d=10_000_000.0),
        [],
    )

    assert order.lot_size == pytest.approx(10.0)


def test_lot_size_rounded_to_lot_step() -> None:
    account = make_account(equity=50_800.0, balance=50_800.0, peak_equity_30d=50_800.0)

    order = make_rm().evaluate(make_instruction(), account, [])

    assert order.lot_size == pytest.approx(0.13)


def test_risk_order_fields_always_populated() -> None:
    account = make_account()

    order = make_rm().evaluate(make_instruction(), account, [])

    assert isinstance(order, RiskOrder)
    assert order.instruction is not None
    assert order.lot_size is not None
    assert order.risk_amount is not None
    assert order.risk_pct is not None
    assert order.kelly_fraction is not None
    assert order.approved is not None
    assert order.portfolio_risk_after > account.open_risk_pct
    assert isinstance(order.warnings, list)


def test_kill_switch_order_spread_first() -> None:
    account = make_account(current_spread_pips=4.0, daily_pnl=-1000.0)

    order = make_rm().evaluate(make_instruction(), account, [])

    assert order.rejection_reason == "spread_too_wide"


def test_approved_trade_risk_pct_within_limit() -> None:
    order = make_rm().evaluate(make_instruction(), make_account(), [])

    assert order.approved is True
    assert order.risk_pct <= 1.0
    assert order.portfolio_risk_after <= 3.0


def test_nonfatal_warnings_populated() -> None:
    history = [{"pnl": 10.0, "direction": "BUY", "entry": 1.0, "exit": 2.0}] * 2
    history += [{"pnl": -100.0, "direction": "SELL", "entry": 2.0, "exit": 1.0}] * 18
    account = make_account(current_spread_pips=2.5, open_risk_pct=1.95)
    instruction = make_instruction(confidence=0.60)

    order = make_rm().evaluate(instruction, account, history)

    assert order.approved is True
    assert {"kelly_below_default", "spread_elevated", "low_confidence", "portfolio_risk_elevated"}.issubset(
        set(order.warnings)
    )
