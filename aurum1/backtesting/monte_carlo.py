"""Monte Carlo trade-sequence simulation for AURUM-1."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from aurum1.backtesting.engine import _drawdown_curve, _sharpe


@dataclass
class MonteCarloResult:
    n_simulations: int
    initial_equity: float
    median_final_equity: float
    pct5_final_equity: float
    pct95_final_equity: float
    median_max_drawdown: float
    pct95_max_drawdown: float
    pct5_sharpe: float
    median_sharpe: float
    ruin_probability: float


def run_monte_carlo(
    trades: list[dict],
    n_simulations: int = 1000,
    initial_equity: float = 10000.0,
) -> MonteCarloResult:
    pnl = np.asarray([float(trade.get("pnl_after_fees", trade.get("pnl", 0.0))) for trade in trades], dtype=float)
    if pnl.size == 0:
        pnl = np.asarray([0.0])
    rng = np.random.default_rng(42)
    final_equities: list[float] = []
    max_drawdowns: list[float] = []
    sharpes: list[float] = []
    ruins = 0
    for _ in range(n_simulations):
        sampled = rng.choice(pnl, size=len(pnl), replace=True)
        equity = (initial_equity + np.cumsum(sampled)).tolist()
        equity = [initial_equity] + equity
        final_equities.append(float(equity[-1]))
        drawdowns = _drawdown_curve(equity)
        max_drawdowns.append(abs(min(drawdowns)) if drawdowns else 0.0)
        start = pd.Timestamp("2026-01-01", tz="UTC").to_pydatetime()
        end = start + pd.Timedelta(days=max(1, len(equity) - 1)).to_pytimedelta()
        sharpes.append(_sharpe(equity, start, end))
        if min(equity) < initial_equity * 0.5:
            ruins += 1
    return MonteCarloResult(
        n_simulations=n_simulations,
        initial_equity=initial_equity,
        median_final_equity=float(np.percentile(final_equities, 50)),
        pct5_final_equity=float(np.percentile(final_equities, 5)),
        pct95_final_equity=float(np.percentile(final_equities, 95)),
        median_max_drawdown=float(np.percentile(max_drawdowns, 50)),
        pct95_max_drawdown=float(np.percentile(max_drawdowns, 95)),
        pct5_sharpe=float(np.percentile(sharpes, 5)),
        median_sharpe=float(np.percentile(sharpes, 50)),
        ruin_probability=ruins / n_simulations if n_simulations else 0.0,
    )


__all__ = ["MonteCarloResult", "run_monte_carlo"]
