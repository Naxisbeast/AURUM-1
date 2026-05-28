"""Run backtests across AURUM-1 machine modes."""

from __future__ import annotations

from typing import Any

import pandas as pd

from aurum1.backtesting.engine import BacktestEngine, BacktestResult
from aurum1.signals import MachineMode


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
    }


__all__ = ["run_ablation_backtest"]
