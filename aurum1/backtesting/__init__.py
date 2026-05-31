"""Event-driven backtesting tools for AURUM-1."""

from aurum1.backtesting.ablation_backtest import (
    RULE_REGIME_BUY_NEXT_OPEN,
    rule_regime_buy_next_open_settings,
    run_ablation_backtest,
)
from aurum1.backtesting.engine import BacktestEngine, BacktestResult
from aurum1.backtesting.monte_carlo import MonteCarloResult, run_monte_carlo
from aurum1.backtesting.walk_forward import WalkForwardResult, WalkForwardValidator


__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "MonteCarloResult",
    "WalkForwardResult",
    "WalkForwardValidator",
    "RULE_REGIME_BUY_NEXT_OPEN",
    "rule_regime_buy_next_open_settings",
    "run_ablation_backtest",
    "run_monte_carlo",
]
