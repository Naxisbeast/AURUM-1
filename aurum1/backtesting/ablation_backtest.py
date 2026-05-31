"""Run backtests across AURUM-1 machine modes."""

from __future__ import annotations

import copy
from typing import Any

import pandas as pd

from aurum1.backtesting.engine import BacktestEngine, BacktestResult
from aurum1.signals import MachineMode


RULE_REGIME_BUY_NEXT_OPEN = "rule_regime_buy_next_open"


def run_ablation_backtest(
    ohlcv: pd.DataFrame,
    macro: pd.DataFrame,
    cot: pd.DataFrame,
    settings: dict[str, Any],
    initial_equity: float = 10000.0,
) -> dict[str, BacktestResult]:
    modes = [
        MachineMode.RULE_ONLY,
        MachineMode.RULE_REGIME,
        MachineMode.RULE_REGIME_SENT,
        MachineMode.FULL_ENSEMBLE,
    ]
    return {
        mode.value: BacktestEngine(settings).run(ohlcv, macro, cot, mode=mode, initial_equity=initial_equity)
        for mode in modes
    } | {
        RULE_REGIME_BUY_NEXT_OPEN: BacktestEngine(rule_regime_buy_next_open_settings(settings)).run(
            ohlcv,
            macro,
            cot,
            mode=MachineMode.RULE_REGIME,
            initial_equity=initial_equity,
        )
    }


def rule_regime_buy_next_open_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Return research-only settings for BUY-only next-open RULE_REGIME testing."""

    variant = copy.deepcopy(settings)
    variant.setdefault("backtesting", {})
    variant["backtesting"].update(
        {
            "entry_type": "next_open",
            "direction_filter": ["BUY"],
            "result_mode_name": RULE_REGIME_BUY_NEXT_OPEN,
            "disable_ml": True,
        }
    )
    return variant


__all__ = ["RULE_REGIME_BUY_NEXT_OPEN", "rule_regime_buy_next_open_settings", "run_ablation_backtest"]
