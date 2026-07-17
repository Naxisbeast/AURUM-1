"""Monte Carlo trade-sequence simulation for AURUM-1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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


@dataclass
class RegimeAwareMCResult:
    """Expanded result with regime-aware statistics."""
    n_simulations: int
    initial_equity: float
    median_final_equity: float
    pct5_final_equity: float
    pct95_final_equity: float
    median_max_drawdown: float
    pct95_max_drawdown: float
    pct99_max_drawdown: float
    worst_drawdown_observed: float
    pct5_sharpe: float
    median_sharpe: float
    ruin_probability: float
    n_blocks: int
    block_size_distribution: dict[str, float] = field(default_factory=dict)
    drawdown_percentiles: dict[str, float] = field(default_factory=dict)


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


__all__ = ["MonteCarloResult", "RegimeAwareMCResult", "regime_block_bootstrap", "run_monte_carlo"]


def regime_block_bootstrap(
    trades: list[dict[str, Any]],
    n_simulations: int = 10000,
    initial_equity: float = 10000.0,
    min_block_size: int = 3,
    max_block_size: int = 30,
) -> RegimeAwareMCResult:
    """Regime-aware Monte Carlo using block bootstrap.

    Instead of reshuffling individual trades (which breaks serial correlation
    and underestimates drawdown by 30-50%), this method:

    1. Tags each trade with its market regime at entry (from 'regime' field)
    2. Forms contiguous same-regime blocks
    3. Bootstraps entire blocks, preserving internal trade order
    4. Concatenates blocks to form simulated trade sequences

    This preserves the serial correlation of losses that CAUSES drawdowns
    in real trading — ranging markets produce clusters of stop-losses.

    Reference: Romano & Wolf (2006), "Improved inference for the Sharpe ratio"
    """
    if not trades:
        raise ValueError("Need at least one trade for regime-aware MC")

    pnl = np.asarray([
        float(t.get("net_pnl", t.get("pnl_after_fees", t.get("pnl", 0.0))))
        for t in trades
    ], dtype=float)
    if pnl.size == 0:
        pnl = np.asarray([0.0])

    regimes = [str(t.get("regime", t.get("regime_label", "RANGING"))) for t in trades]

    # Build contiguous regime blocks
    blocks: list[list[int]] = []
    current: list[int] = []
    for i in range(len(regimes)):
        if not current or regimes[current[-1]] == regimes[i]:
            current.append(i)
        else:
            if len(current) >= min_block_size:
                blocks.append(current)
            current = [i]
    if len(current) >= min_block_size:
        blocks.append(current)
    if not blocks:
        blocks = [[i] for i in range(len(trades))]

    n_blocks = len(blocks)
    block_sizes = [len(b) for b in blocks]
    block_pnl_groups = [pnl[b] for b in blocks]

    rng = np.random.RandomState(42)
    final_equities: list[float] = []
    max_drawdowns: list[float] = []
    sharpes: list[float] = []
    ruins = 0
    dd_list: list[list[float]] = [[], [], [], []]

    for _ in range(n_simulations):
        sim_blocks: list[np.ndarray] = []
        total = len(trades)
        while len(np.concatenate(sim_blocks)) < total if sim_blocks else True:
            idx = rng.randint(0, n_blocks)
            sim_blocks.append(block_pnl_groups[idx])
        simulated = np.concatenate(sim_blocks)[:total]

        equity = (initial_equity + np.cumsum(simulated)).tolist()
        equity = [initial_equity] + equity
        final_equities.append(float(equity[-1]))
        drawdowns = _drawdown_curve(equity)
        max_dd = abs(min(drawdowns)) if drawdowns else 0.0
        max_drawdowns.append(max_dd)
        start = pd.Timestamp("2026-01-01", tz="UTC").to_pydatetime()
        end = start + pd.Timedelta(days=max(1, len(equity) - 1)).to_pytimedelta()
        sharpes.append(_sharpe(equity, start, end))
        if min(equity) < initial_equity * 0.5:
            ruins += 1
        dds = [abs(d) for d in drawdowns if d < 0]
        for pct_idx, pct in enumerate([10, 50, 90, 99]):
            if dds:
                dd_list[pct_idx].append(float(np.percentile(dds, pct)))

    return RegimeAwareMCResult(
        n_simulations=n_simulations,
        initial_equity=initial_equity,
        median_final_equity=float(np.percentile(final_equities, 50)),
        pct5_final_equity=float(np.percentile(final_equities, 5)),
        pct95_final_equity=float(np.percentile(final_equities, 95)),
        median_max_drawdown=float(np.percentile(max_drawdowns, 50)),
        pct95_max_drawdown=float(np.percentile(max_drawdowns, 95)),
        pct99_max_drawdown=float(np.percentile(max_drawdowns, 99)),
        worst_drawdown_observed=float(np.max(max_drawdowns)),
        pct5_sharpe=float(np.percentile(sharpes, 5)),
        median_sharpe=float(np.percentile(sharpes, 50)),
        ruin_probability=ruins / n_simulations if n_simulations else 0.0,
        n_blocks=n_blocks,
        block_size_distribution={
            "min": int(min(block_sizes)),
            "max": int(max(block_sizes)),
            "mean": round(float(np.mean(block_sizes)), 2),
            "median": int(np.median(block_sizes)),
        },
        drawdown_percentiles={
            "p10_median": round(float(np.median(dd_list[0])), 4) if dd_list[0] else 0.0,
            "p50_median": round(float(np.median(dd_list[1])), 4) if dd_list[1] else 0.0,
            "p90_median": round(float(np.median(dd_list[2])), 4) if dd_list[2] else 0.0,
            "p99_median": round(float(np.median(dd_list[3])), 4) if dd_list[3] else 0.0,
        },
    )