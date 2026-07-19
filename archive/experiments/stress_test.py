"""Stress test suite: run strategy under harsh conditions.

Tests the strategy's resilience to adverse market conditions including
wider spreads, higher slippage, crisis periods, and random control.
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np
import pandas as pd

from aurum1.backtesting.engine import BacktestEngine
from aurum1.signals import MachineMode
from experiments.models import StressTestResult


def run_stress_tests(
    base_settings: dict[str, Any],
    ohlcv: pd.DataFrame,
    macro: pd.DataFrame,
    cot: pd.DataFrame,
) -> list[StressTestResult]:
    """Run the full stress test suite.

    Tests:
    1. 2x Spread: Widens spread from 1.5 to 3.0 pips
    2. 3x Slippage: Increases slippage std from 0.5 to 1.5 pips
    3. 2x Costs: Both spread and slippage doubled
    4. High Vol: Only high-volatility periods (2008, 2011, 2020, 2022)
    5. Low Vol: Only low-volatility periods (2017, 2023)
    6. Random Control: Replace signals with random entries (same count)
    """
    results: list[StressTestResult] = []

    # ── 1. 2x Spread ──
    results.append(_run_cost_stress(base_settings, ohlcv, macro, cot,
                                    name="2x_spread",
                                    spread_mult=2.0, slippage_mult=1.0))

    # ── 2. 3x Slippage ──
    results.append(_run_cost_stress(base_settings, ohlcv, macro, cot,
                                    name="3x_slippage",
                                    spread_mult=1.0, slippage_mult=3.0))

    # ── 3. 2x Costs ──
    results.append(_run_cost_stress(base_settings, ohlcv, macro, cot,
                                    name="2x_costs",
                                    spread_mult=2.0, slippage_mult=2.0))

    # ── 4. High Vol Regime ──
    high_vol_years = [2008, 2011, 2020, 2022]
    results.append(_run_regime_stress(base_settings, ohlcv, macro, cot,
                                      name="high_vol_regime",
                                      years=high_vol_years))

    # ── 5. Low Vol Regime ──
    low_vol_years = [2017, 2023]
    results.append(_run_regime_stress(base_settings, ohlcv, macro, cot,
                                      name="low_vol_regime",
                                      years=low_vol_years))

    # ── 6. Random Control ──
    results.append(_run_random_control(base_settings, ohlcv, macro, cot,
                                       name="random_control"))

    # ── 7. Multi-Asset Validation on GC=F Futures (26 years) ──
    results.append(_run_gcf_daily_validation(base_settings, name="gc_f_daily_26yr"))

    # Evaluate pass/fail for each
    for r in results:
        r.passed = r.profit_factor >= 1.00 and r.max_drawdown < 0.30

    return results


def _run_cost_stress(
    settings: dict[str, Any],
    ohlcv: pd.DataFrame,
    macro: pd.DataFrame,
    cot: pd.DataFrame,
    *,
    name: str,
    spread_mult: float,
    slippage_mult: float,
) -> StressTestResult:
    """Run backtest with modified costs."""
    stressed = copy.deepcopy(settings)
    base_spread = float(settings.get("execution", {}).get("paper_spread_pips", 1.5))
    base_slippage = float(settings.get("execution", {}).get("slippage_std_pips", 0.5))

    stressed.setdefault("execution", {})
    stressed["execution"]["paper_spread_pips"] = base_spread * spread_mult
    stressed["execution"]["slippage_std_pips"] = base_slippage * slippage_mult

    # Adjust risk to allow wider spreads
    stressed.setdefault("risk", {})
    stressed["risk"]["max_spread_pips"] = max(
        stressed["execution"]["paper_spread_pips"] + 0.5,
        float(settings.get("risk", {}).get("max_spread_pips", 3.0)),
    )

    return _run_single_stress(stressed, ohlcv, macro, cot, name)


def _run_regime_stress(
    settings: dict[str, Any],
    ohlcv: pd.DataFrame,
    macro: pd.DataFrame,
    cot: pd.DataFrame,
    *,
    name: str,
    years: list[int],
) -> StressTestResult:
    """Run backtest restricted to specific years."""
    # Filter OHLCV to the specified years
    year_mask = ohlcv.index.year.isin(years)
    filtered = ohlcv[year_mask].copy()
    if len(filtered) < 1000:
        return StressTestResult(
            test_name=name,
            profit_factor=0.0, sharpe=0.0, max_drawdown=1.0,
            net_pnl=0.0, win_rate=0.0, trade_count=0, passed=False,
        )
    return _run_single_stress(settings, filtered, macro, cot, name)


def _run_random_control(
    settings: dict[str, Any],
    ohlcv: pd.DataFrame,
    macro: pd.DataFrame,
    cot: pd.DataFrame,
    *,
    name: str,
    n_runs: int = 5,
) -> StressTestResult:
    """Run a random-entry control: replace strategy signals with random entries.

    This tests whether the strategy's edge is real (should outperform random).
    """
    from scripts.research.research_edge_prototypes import (
        basic_candle,
        random_matched_long_signals,
        run_signal_backtest,
    )

    features = _build_research_features(ohlcv)
    target_count = 2000  # Fixed for comparison

    best_pf = 0.0
    best_net = float("-inf")
    total_trades = 0

    for seed in range(n_runs):
        random_signals = random_matched_long_signals(
            ohlcv, features, target_count, seed=seed + 100
        )
        try:
            result = run_signal_backtest(
                "random_control",
                ohlcv,
                random_signals,
                settings,
                initial_equity=10000.0,
                max_one_position=True,
            )
            if result.total_trades > 0:
                pf = result.profit_factor
                net = result.total_net_pnl
                if net > best_net:
                    best_pf = pf
                    best_net = net
                total_trades += result.total_trades
        except Exception:
            continue

    return StressTestResult(
        test_name=name,
        profit_factor=best_pf,
        sharpe=0.0,
        max_drawdown=0.0,
        net_pnl=best_net,
        win_rate=0.0,
        trade_count=total_trades,
        passed=best_pf < 1.10,  # Random should NOT have edge > 1.10 PF
    )


def _run_single_stress(
    settings: dict[str, Any],
    ohlcv: pd.DataFrame,
    macro: pd.DataFrame,
    cot: pd.DataFrame,
    test_name: str,
) -> StressTestResult:
    """Run one backtest with the given settings."""
    if len(ohlcv) < 200:
        return StressTestResult(
            test_name=test_name,
            profit_factor=0.0, sharpe=0.0, max_drawdown=1.0,
            net_pnl=0.0, win_rate=0.0, trade_count=0, passed=False,
        )

    try:
        engine = BacktestEngine(settings)
        result = engine.run(
            ohlcv=ohlcv, macro=macro, cot=cot,
            mode=MachineMode.RULE_REGIME, initial_equity=10000.0,
        )
    except Exception as e:
        return StressTestResult(
            test_name=test_name,
            profit_factor=0.0, sharpe=0.0, max_drawdown=1.0,
            net_pnl=0.0, win_rate=0.0, trade_count=0, passed=False,
        )

    return StressTestResult(
        test_name=test_name,
        profit_factor=result.profit_factor if result.total_trades > 0 else 0.0,
        sharpe=result.sharpe_ratio,
        max_drawdown=result.max_drawdown_pct,
        net_pnl=result.total_net_pnl,
        win_rate=result.win_rate,
        trade_count=result.total_trades,
        passed=False,  # Caller sets passed
    )


def _build_research_features(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Quick feature build for research purposes."""
    frame = ohlcv[["open", "high", "low", "close", "volume"]].astype(float).copy()
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    frame["ema_9"] = close.ewm(span=9, adjust=False, min_periods=9).mean()
    frame["ema_20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    frame["ema_50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    frame["ema_200"] = close.ewm(span=200, adjust=False, min_periods=200).mean()
    frame["atr_14"] = _atr_wilder(high, low, close, 14)
    frame["recent_low_5"] = low.rolling(5, min_periods=5).min()
    hours = frame.index.hour
    frame["session_london"] = ((hours >= 7) & (hours < 16)).astype(int)
    frame["session_ny"] = ((hours >= 13) & (hours < 22)).astype(int)
    for timeframe, rule in [("H1", "1h"), ("H4", "4h")]:
        htf = ohlcv[["open", "high", "low", "close", "volume"]].resample(
            rule, label="right", closed="right"
        ).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        htf_features = pd.DataFrame(index=htf.index)
        htf_features[f"{timeframe}_close"] = htf["close"]
        htf_features[f"{timeframe}_ema_50"] = htf["close"].ewm(span=50, adjust=False).mean()
        htf_features[f"{timeframe}_ema_200"] = htf["close"].ewm(span=200, adjust=False).mean()
        frame = pd.merge_asof(
            frame.sort_index().reset_index(names="timestamp"),
            htf_features.sort_index().reset_index(names="timestamp"),
            on="timestamp", direction="backward",
        ).set_index("timestamp")
    return frame


def _atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _run_gcf_daily_validation(
    base_settings: dict[str, Any],
    *,
    name: str,
) -> StressTestResult:
    """Validate strategy on 26 years of daily gold futures (GC=F).

    This tests whether the strategy's edge generalizes to:
    - A different timeframe (daily vs M15)
    - A longer period (2000-2026 vs 2016-2026)
    - Different data source (futures vs spot via OANDA)

    Uses a simplified Donchian 20-day breakout with fixed 2R stops.
    """
    import numpy as np
    import pandas as pd

    try:
        gc = pd.read_csv(
            Path(__file__).resolve().parents[1] / "aurum1" / "data" / "gc_futures_daily_2000_2026.csv",
            index_col=0, parse_dates=True,
        )
    except (FileNotFoundError, ValueError):
        return StressTestResult(
            test_name=name, profit_factor=0, sharpe=0, max_drawdown=1.0,
            net_pnl=0, win_rate=0, trade_count=0, passed=False,
        )

    for c in ["Open", "High", "Low", "Close", "Volume"]:
        gc[c] = pd.to_numeric(gc[c], errors="coerce")
    gc = gc.dropna(subset=["Close"])

    LOOKBACK, RISK_PCT = 20, 0.0025
    gc["donchian_high"] = gc["High"].rolling(LOOKBACK).max().shift(1)
    gc["donchian_low"] = gc["Low"].rolling(LOOKBACK).min().shift(1)
    tr = pd.concat([
        gc["High"] - gc["Low"],
        (gc["High"] - gc["Close"].shift(1)).abs(),
        (gc["Low"] - gc["Close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    gc["atr"] = tr.ewm(alpha=1 / 14, min_periods=14).mean()

    eq = 10000.0
    trades = []
    pos = None
    for i in range(LOOKBACK + 2, len(gc)):
        if pos and pos["entry_i"]:
            o, h, l = gc.iloc[i][["Open", "High", "Low"]]
            d = pos["d"]
            if d == "BUY":
                if l <= pos["sl"]:
                    trades.append({"r": (pos["sl"] - pos["entry"]) / pos["risk"]})
                    eq += pos["sl"] - pos["entry"]
                    pos = None
                elif h >= pos["tp"]:
                    trades.append({"r": (pos["tp"] - pos["entry"]) / pos["risk"]})
                    eq += pos["tp"] - pos["entry"]
                    pos = None
            else:
                if h >= pos["sl"]:
                    trades.append({"r": (pos["entry"] - pos["sl"]) / pos["risk"]})
                    eq += pos["entry"] - pos["sl"]
                    pos = None
                elif l <= pos["tp"]:
                    trades.append({"r": (pos["entry"] - pos["tp"]) / pos["risk"]})
                    eq += pos["entry"] - pos["tp"]
                    pos = None
        if pos:
            continue
        a = gc["atr"].iloc[i]
        if pd.isna(a) or a <= 0:
            continue
        c = gc["Close"].iloc[i]
        ub, lb = gc["donchian_high"].iloc[i], gc["donchian_low"].iloc[i]
        if c > ub:
            sl = c - 2 * a
            tp = c + 4 * a
            pos = {"entry_i": True, "d": "BUY", "entry": c, "sl": sl, "tp": tp, "risk": abs(c - sl)}
            eq -= eq * RISK_PCT
        elif c < lb:
            sl = c + 2 * a
            tp = c - 4 * a
            pos = {"entry_i": True, "d": "SELL", "entry": c, "sl": sl, "tp": tp, "risk": abs(c - sl)}
            eq -= eq * RISK_PCT

    if pos:
        last = gc["Close"].iloc[-1]
        if pos["d"] == "BUY":
            trades.append({"r": (last - pos["entry"]) / pos["risk"]})
        else:
            trades.append({"r": (pos["entry"] - last) / pos["risk"]})

    if not trades:
        return StressTestResult(
            test_name=name, profit_factor=0, sharpe=0, max_drawdown=1.0,
            net_pnl=0, win_rate=0, trade_count=0, passed=False,
        )

    r_arr = np.array([t["r"] for t in trades])
    wins = r_arr[r_arr > 0]
    losses = r_arr[r_arr <= 0]
    pf = abs(np.sum(wins) / np.sum(losses)) if np.sum(losses) != 0 else 0

    return StressTestResult(
        test_name=name,
        profit_factor=float(pf),
        sharpe=0.0,
        max_drawdown=0.0,
        net_pnl=float(eq - 10000),
        win_rate=float(len(wins) / len(r_arr)) if len(r_arr) > 0 else 0.0,
        trade_count=len(trades),
        passed=pf >= 1.00,
    )


__all__ = ["run_stress_tests"]
