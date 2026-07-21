"""Diagnostics for the clean Donchian fixed-2R research lead.

This is a diagnostic-only script:
- no parameter optimization
- no SELL logic
- no ML
- no paper/live broker use
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.backtesting.engine import BacktestResult, build_backtest_result
from aurum1.data.ingestion import load_ohlcv, load_settings
from scripts.research.donchian_research_runner import (
    donchian_signals,
    result_summary,
    run_variant,
    settings_with_exit,
)
from scripts.research.research_edge_prototypes import build_research_features


DEFAULT_OUTPUT_DIR = ROOT / "reports" / "research"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    market_db = args.market_db or Path(
        str(settings.get("backtesting", {}).get("market_data_db_path", "aurum1/data/backtest_market_cache.sqlite3"))
    )
    market_db = ROOT / market_db if not market_db.is_absolute() else market_db
    ohlcv = load_ohlcv("M15", market_db)
    if ohlcv.empty:
        raise RuntimeError(f"No M15 data available in {market_db}")
    features = build_research_features(ohlcv)
    features["adx_14"] = adx_wilder(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
    initial_equity = float(settings.get("broker", {}).get("paper_initial_equity", 10000.0))

    base_settings = settings_with_exit(settings, "FIXED")
    base_signals = donchian_signals(ohlcv, features, lookback=args.lookback, htf_filter=False)
    base = run_variant(
        base_signals,
        ohlcv,
        features,
        base_settings,
        "FIXED",
        initial_equity,
        args.lookback,
        "donchian_fixed_2r_diagnostic",
    )
    donchian_low = run_variant(
        base_signals,
        ohlcv,
        features,
        base_settings,
        "DONCHIAN_LOW",
        initial_equity,
        args.lookback,
        "donchian_low_diagnostic",
    )

    period_2021 = period_result(ohlcv, features, settings, args.lookback, 2021, initial_equity)
    diagnostics = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "diagnostic_only": True,
            "strategy": "donchian_raw_fixed_2r",
            "lookback": args.lookback,
            "ml_enabled": False,
            "sell_enabled": False,
            "paper_or_live_orders_sent": False,
        },
        "market_db": str(market_db),
        "rows": len(ohlcv),
        "date_range": {"start": ohlcv.index.min().isoformat(), "end": ohlcv.index.max().isoformat()},
        "base": enhanced_summary(base, ohlcv),
        "cost_stress_split": cost_stress_split(base, base_signals, ohlcv, features, settings, args.lookback, initial_equity),
        "monthly_consistency": monthly_consistency(base),
        "failure_2021": failure_2021(period_2021, ohlcv.loc[str(2021)], features.loc[str(2021)]),
        "exit_comparison": {
            "fixed_2r": exit_diagnostics(base, ohlcv),
            "donchian_low": exit_diagnostics(donchian_low, ohlcv),
            "explanation": explain_exit_difference(base, donchian_low, ohlcv),
        },
        "risk_survivability": {
            "risk_0_25_pct": risk_survivability(base, 0.0025, initial_equity),
            "risk_0_50_pct": risk_survivability(base, 0.005, initial_equity),
            "risk_1_00_pct": risk_survivability(base, 0.01, initial_equity),
        },
        "classification": "promising research candidate",
        "paper_readiness": "failed",
        "live_readiness": "failed",
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = args.output_dir / f"donchian_diagnostics_{stamp}.json"
    json_path.write_text(json.dumps(diagnostics, indent=2, sort_keys=True, default=str), encoding="utf-8")
    write_csv_artifacts(args.output_dir, stamp, diagnostics)
    print_summary(diagnostics, json_path)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run diagnostics for Donchian fixed-2R lead.")
    parser.add_argument("--market-db", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--lookback", type=int, default=20)
    return parser.parse_args(argv)


def period_result(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    lookback: int,
    year: int,
    initial_equity: float,
) -> BacktestResult:
    window = ohlcv.loc[str(year)].copy()
    window_features = features.loc[window.index].copy()
    signals = donchian_signals(window, window_features, lookback=lookback, htf_filter=False)
    return run_variant(
        signals,
        window,
        window_features,
        settings_with_exit(settings, "FIXED"),
        "FIXED",
        initial_equity,
        lookback,
        f"donchian_fixed_2r_{year}",
    )


def cost_stress_split(
    base: BacktestResult,
    signals: list[Any],
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    lookback: int,
    initial_equity: float,
) -> dict[str, Any]:
    accounting = {
        "base": result_summary(base),
        "2x": accounting_cost_result(base, 2.0),
        "3x": accounting_cost_result(base, 3.0),
    }
    execution: dict[str, Any] = {"base": result_summary(base)}
    for multiplier in (2.0, 3.0):
        stressed = settings_with_exit(settings, "FIXED")
        stressed.setdefault("execution", {})
        stressed.setdefault("risk", {})
        stressed["execution"]["paper_spread_pips"] = float(settings.get("execution", {}).get("paper_spread_pips", 1.5)) * multiplier
        stressed["execution"]["slippage_std_pips"] = float(settings.get("execution", {}).get("slippage_std_pips", 0.5)) * multiplier
        stressed["risk"]["max_spread_pips"] = max(
            float(stressed["execution"]["paper_spread_pips"]) + 0.1,
            float(stressed["risk"].get("max_spread_pips", 3.0)),
        )
        result = run_variant(
            signals,
            ohlcv,
            features,
            stressed,
            "FIXED",
            initial_equity,
            lookback,
            f"donchian_execution_slippage_{multiplier:g}x",
        )
        execution[f"{multiplier:g}x"] = {
            **result_summary(result),
            "changed_trade_reasons": compare_trade_sets(base, result),
        }
    return {"accounting_cost_stress": accounting, "execution_slippage_stress": execution}


def accounting_cost_result(base: BacktestResult, multiplier: float) -> dict[str, Any]:
    adjusted_trades: list[dict[str, Any]] = []
    for trade in base.trades:
        item = dict(trade)
        gross = float(item.get("gross_pnl", item.get("pnl", 0.0)))
        spread = float(item.get("spread_cost", item.get("fee", 0.0))) * multiplier
        entry_slip = float(item.get("entry_slippage_cost", 0.0)) * multiplier
        exit_slip = float(item.get("exit_slippage_cost", 0.0)) * multiplier
        original_entry = float(item.get("entry_slippage_cost", 0.0))
        original_exit = float(item.get("exit_slippage_cost", 0.0))
        adjusted_net = gross - spread - (entry_slip - original_entry) - (exit_slip - original_exit)
        item["fee"] = spread
        item["spread_cost"] = spread
        item["entry_slippage_cost"] = entry_slip
        item["exit_slippage_cost"] = exit_slip
        item["total_slippage_cost"] = entry_slip + exit_slip
        item["net_pnl"] = adjusted_net
        item["pnl_after_fees"] = adjusted_net
        item["fee_in_equity"] = True
        adjusted_trades.append(item)
    equity = equity_from_trades(adjusted_trades, base.initial_equity, base.total_bars)
    adjusted = build_backtest_result(
        equity_curve=equity,
        trades=adjusted_trades,
        start_date=base.start_date,
        end_date=base.end_date,
        instrument=base.instrument,
        mode=f"{base.mode}_accounting_{multiplier:g}x",
        initial_equity=base.initial_equity,
        total_bars=base.total_bars,
        total_signals=base.total_signals,
        signals_approved=base.signals_approved,
        signals_rejected=base.signals_rejected,
        rejection_reasons=base.rejection_reasons,
    )
    summary = result_summary(adjusted)
    summary["trade_count_identical"] = adjusted.total_trades == base.total_trades
    return summary


def compare_trade_sets(base: BacktestResult, stressed: BacktestResult) -> dict[str, Any]:
    base_keys = [str(trade.get("signal_time", trade.get("open_time", ""))) for trade in base.trades]
    stress_keys = [str(trade.get("signal_time", trade.get("open_time", ""))) for trade in stressed.trades]
    base_counter = Counter(base_keys)
    stress_counter = Counter(stress_keys)
    missing = list((base_counter - stress_counter).elements())
    added = list((stress_counter - base_counter).elements())
    base_reasons = Counter(str(trade.get("reason", "unknown")) for trade in base.trades)
    stress_reasons = Counter(str(trade.get("reason", "unknown")) for trade in stressed.trades)
    return {
        "trade_count_delta": stressed.total_trades - base.total_trades,
        "missing_base_trades": len(missing),
        "additional_stressed_trades": len(added),
        "exit_reason_delta": dict(stress_reasons - base_reasons),
        "exit_reason_removed": dict(base_reasons - stress_reasons),
        "rejection_delta": {
            key: int(stressed.rejection_reasons.get(key, 0) - base.rejection_reasons.get(key, 0))
            for key in sorted(set(base.rejection_reasons) | set(stressed.rejection_reasons))
        },
    }


def monthly_consistency(result: BacktestResult) -> dict[str, Any]:
    monthly = group_trade_stats(result.trades, lambda trade: month_key(trade))
    ordered = sorted(monthly.items())
    values = [item["net_pnl"] for _, item in ordered]
    total_net = sum(values)
    top3 = sorted(values, reverse=True)[:3]
    breakdown_2021 = {month: stats for month, stats in monthly.items() if month.startswith("2021-")}
    breakdown_2025 = {month: stats for month, stats in monthly.items() if month.startswith("2025-")}
    return {
        "positive_months": sum(1 for value in values if value > 0.0),
        "total_months": len(values),
        "months_pf_above_1": sum(1 for _, stats in ordered if float(stats["profit_factor"]) > 1.0),
        "max_consecutive_losing_months": max_consecutive([value < 0.0 for value in values]),
        "worst_month": min(monthly.items(), key=lambda item: item[1]["net_pnl"]) if monthly else None,
        "best_month": max(monthly.items(), key=lambda item: item[1]["net_pnl"]) if monthly else None,
        "top_3_month_net_pnl": sum(top3),
        "top_3_month_contribution": (sum(top3) / total_net if total_net > 0.0 else None),
        "breakdown_2021": breakdown_2021,
        "concentration_2025": {
            "net_pnl": sum(stats["net_pnl"] for stats in breakdown_2025.values()),
            "share_of_total_net": (
                sum(stats["net_pnl"] for stats in breakdown_2025.values()) / total_net if total_net > 0.0 else None
            ),
            "monthly": breakdown_2025,
        },
    }


def failure_2021(result: BacktestResult, ohlcv_2021: pd.DataFrame, features_2021: pd.DataFrame) -> dict[str, Any]:
    enriched = enrich_trades(result.trades, ohlcv_2021, features_2021)
    return {
        "overall": enhanced_summary(result, ohlcv_2021),
        "by_month": group_trade_stats(enriched, lambda trade: month_key(trade)),
        "by_session": group_trade_stats(enriched, lambda trade: str(trade.get("session", "unknown"))),
        "by_atr_percentile": group_trade_stats(enriched, lambda trade: str(trade.get("atr_bucket", "unknown"))),
        "by_adx_bucket": group_trade_stats(enriched, lambda trade: str(trade.get("adx_bucket", "unknown"))),
        "by_exit_reason": group_trade_stats(enriched, lambda trade: str(trade.get("reason", "unknown"))),
        "by_holding_time": group_trade_stats(enriched, lambda trade: str(trade.get("holding_bucket", "unknown"))),
        "mfe_mae": mfe_mae_summary(enriched),
        "interpretation": interpret_2021(enriched),
    }


def exit_diagnostics(result: BacktestResult, ohlcv: pd.DataFrame) -> dict[str, Any]:
    enriched = enrich_trades(result.trades, ohlcv, pd.DataFrame(index=ohlcv.index))
    summary = enhanced_summary(result, ohlcv)
    r_values = trade_r_values(result.trades)
    summary.update(
        {
            "average_r": float(np.mean(r_values)) if r_values else 0.0,
            "median_r": float(np.median(r_values)) if r_values else 0.0,
            "capital_weighted_r": capital_weighted_r(result.trades),
            "time_in_market_bars": int(sum(int(trade.get("duration_bars", 0)) for trade in result.trades)),
            "time_in_market_pct": (
                sum(int(trade.get("duration_bars", 0)) for trade in result.trades) / result.total_bars
                if result.total_bars
                else 0.0
            ),
            "profit_per_bar_in_market": profit_per_bar_in_market(result),
            "mfe_giveback": average_mfe_giveback(enriched),
            "mae": average_mae(enriched),
            "top_10_trades_contribution": top_n_contribution(result.trades, 10),
        }
    )
    return summary


def explain_exit_difference(fixed: BacktestResult, donchian_low: BacktestResult, ohlcv: pd.DataFrame) -> str:
    fixed_diag = exit_diagnostics(fixed, ohlcv)
    low_diag = exit_diagnostics(donchian_low, ohlcv)
    if low_diag["profit_factor"] > fixed_diag["profit_factor"] and low_diag["sharpe"] < fixed_diag["sharpe"]:
        return (
            "Donchian-low has higher PF because it reduces loss severity and/or filters exits through structure, "
            "but lower Sharpe because longer and more variable holding periods create lumpier daily equity returns."
        )
    return "Exit behavior does not match the expected higher-PF/lower-Sharpe pattern."


def risk_survivability(result: BacktestResult, risk_pct: float, initial_equity: float) -> dict[str, Any]:
    r_values = trade_r_values(result.trades)
    equity = [initial_equity]
    month_equity: dict[str, list[float]] = {}
    for trade, r_value in zip(result.trades, r_values):
        pnl = equity[-1] * risk_pct * r_value
        equity.append(equity[-1] + pnl)
        month_equity.setdefault(month_key(trade), []).append(pnl)
    drawdowns = drawdown_series(equity)
    monthly_pnl = {month: sum(values) for month, values in month_equity.items()}
    losing_months = [value < 0.0 for _, value in sorted(monthly_pnl.items())]
    max_loss_streak = max_consecutive([r <= 0.0 for r in r_values])
    worst_streak_cash = worst_consecutive_loss_cash(r_values, risk_pct, initial_equity)
    max_losing_months = max_consecutive(losing_months)
    max_losing_months_cash = worst_consecutive_month_cash(monthly_pnl)
    return {
        "risk_pct": risk_pct,
        "final_equity": equity[-1],
        "max_drawdown": abs(min(drawdowns)) if drawdowns else 0.0,
        "worst_month": min(monthly_pnl.items(), key=lambda item: item[1]) if monthly_pnl else None,
        "max_losing_streak_trades": max_loss_streak,
        "max_losing_streak_cash_impact": worst_streak_cash,
        "max_consecutive_losing_months": max_losing_months,
        "max_consecutive_losing_months_cash_impact": max_losing_months_cash,
        "time_to_recovery_trades": max_time_to_recovery(equity),
        "seventeen_loss_streak_survivable": (1.0 - risk_pct) ** 17 > 0.80,
        "seventeen_full_r_loss_equity_impact": 1.0 - (1.0 - risk_pct) ** 17,
    }


def enhanced_summary(result: BacktestResult, ohlcv: pd.DataFrame) -> dict[str, Any]:
    summary = result_summary(result)
    summary["time_in_market_pct"] = (
        sum(int(trade.get("duration_bars", 0)) for trade in result.trades) / result.total_bars if result.total_bars else 0.0
    )
    summary["profit_per_bar_in_market"] = profit_per_bar_in_market(result)
    return summary


def group_trade_stats(trades: list[dict[str, Any]], key_fn: Any) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        grouped.setdefault(str(key_fn(trade)), []).append(trade)
    return {key: simple_trade_stats(items) for key, items in sorted(grouped.items())}


def simple_trade_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))) for trade in trades]
    wins = [value for value in values if value > 0.0]
    losses = [value for value in values if value <= 0.0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trade_count": len(values),
        "net_pnl": sum(values),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else (10.0 if gross_profit > 0.0 else 0.0),
        "win_rate": len(wins) / len(values) if values else 0.0,
        "avg_win": float(np.mean(wins)) if wins else 0.0,
        "avg_loss": float(np.mean(losses)) if losses else 0.0,
    }


def enrich_trades(trades: list[dict[str, Any]], ohlcv: pd.DataFrame, features: pd.DataFrame) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    atr_series = features["atr_14"] if "atr_14" in features else pd.Series(dtype=float)
    adx_series = features["adx_14"] if "adx_14" in features else pd.Series(dtype=float)
    for trade in trades:
        item = dict(trade)
        open_bar = int(item.get("open_bar", 0))
        close_bar = int(item.get("close_bar", open_bar))
        if open_bar >= len(ohlcv):
            enriched.append(item)
            continue
        open_time = pd.Timestamp(item.get("market_open_time", item.get("open_time", ohlcv.index[min(open_bar, len(ohlcv) - 1)])))
        item["session"] = session_name(open_time.hour)
        item["holding_bucket"] = holding_bucket(int(item.get("duration_bars", 0)))
        if not atr_series.empty and open_bar < len(atr_series):
            atr_rank = float(atr_series.rank(pct=True).iloc[open_bar])
            item["atr_bucket"] = percentile_bucket(atr_rank)
        if not adx_series.empty and open_bar < len(adx_series):
            item["adx_bucket"] = adx_bucket(float(adx_series.iloc[open_bar]))
        window = ohlcv.iloc[max(0, open_bar) : min(len(ohlcv), close_bar + 1)]
        units = float(item.get("units", 0.0))
        entry = float(item.get("actual_entry", item.get("entry", 0.0)))
        risk_amount = float(item.get("risk_amount", 0.0))
        if not window.empty and units > 0.0:
            max_high = float(window["high"].max())
            min_low = float(window["low"].min())
            mfe = max(0.0, (max_high - entry) * units)
            mae = min(0.0, (min_low - entry) * units)
            item["mfe"] = mfe
            item["mae"] = mae
            item["mfe_r"] = mfe / risk_amount if risk_amount > 0.0 else None
            item["mae_r"] = mae / risk_amount if risk_amount > 0.0 else None
            item["mfe_giveback"] = mfe - float(item.get("net_pnl", item.get("pnl_after_fees", 0.0)))
        enriched.append(item)
    return enriched


def mfe_mae_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    mfe = [float(trade.get("mfe", 0.0)) for trade in trades]
    mae = [float(trade.get("mae", 0.0)) for trade in trades]
    giveback = [float(trade.get("mfe_giveback", 0.0)) for trade in trades]
    return {
        "avg_mfe": float(np.mean(mfe)) if mfe else 0.0,
        "median_mfe": float(np.median(mfe)) if mfe else 0.0,
        "avg_mae": float(np.mean(mae)) if mae else 0.0,
        "median_mae": float(np.median(mae)) if mae else 0.0,
        "avg_mfe_giveback": float(np.mean(giveback)) if giveback else 0.0,
    }


def interpret_2021(trades: list[dict[str, Any]]) -> str:
    by_month = group_trade_stats(trades, lambda trade: month_key(trade))
    by_exit = group_trade_stats(trades, lambda trade: str(trade.get("reason", "unknown")))
    by_atr = group_trade_stats(trades, lambda trade: str(trade.get("atr_bucket", "unknown")))
    worst_month = min(by_month.items(), key=lambda item: item[1]["net_pnl"]) if by_month else ("unknown", {})
    worst_exit = min(by_exit.items(), key=lambda item: item[1]["net_pnl"]) if by_exit else ("unknown", {})
    worst_atr = min(by_atr.items(), key=lambda item: item[1]["net_pnl"]) if by_atr else ("unknown", {})
    return (
        f"2021 weakness is concentrated most in month={worst_month[0]}, "
        f"exit_reason={worst_exit[0]}, ATR_bucket={worst_atr[0]}. "
        "Use the detailed buckets to distinguish chop/no-follow-through from cost or exit issues; "
        "no parameter change is recommended by this diagnostic."
    )


def equity_from_trades(trades: list[dict[str, Any]], initial_equity: float, total_bars: int) -> list[float]:
    deltas_by_bar: Counter[int] = Counter()
    for trade in trades:
        deltas_by_bar[int(trade.get("close_bar", total_bars - 1))] += float(
            trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))
        )
    equity: list[float] = []
    current = initial_equity
    for idx in range(total_bars):
        current += deltas_by_bar[idx]
        equity.append(float(current))
    return equity or [initial_equity]


def adx_wilder(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    high = high.astype(float)
    low = low.astype(float)
    close = close.astype(float)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0.0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0.0), 0.0)
    true_range = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)) * 100.0
    return dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def month_key(trade: dict[str, Any]) -> str:
    timestamp = str(trade.get("market_close_time", trade.get("closed_at", "")))
    return timestamp[:7] if len(timestamp) >= 7 else "unknown"


def session_name(hour: int) -> str:
    if 13 <= hour < 16:
        return "overlap"
    if 7 <= hour < 13:
        return "london"
    if 16 <= hour < 22:
        return "ny"
    if 0 <= hour < 7:
        return "asia"
    return "other"


def percentile_bucket(rank: float) -> str:
    if not math.isfinite(rank):
        return "unknown"
    if rank < 0.33:
        return "low"
    if rank < 0.67:
        return "mid"
    return "high"


def adx_bucket(value: float) -> str:
    if not math.isfinite(value):
        return "unknown"
    if value < 20.0:
        return "low_<20"
    if value < 25.0:
        return "mid_20_25"
    return "trend_25_plus"


def holding_bucket(duration: int) -> str:
    if duration <= 8:
        return "short_0_8"
    if duration <= 32:
        return "medium_9_32"
    return "long_33_plus"


def trade_r_values(trades: list[dict[str, Any]]) -> list[float]:
    return [
        float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))) / float(trade["risk_amount"])
        for trade in trades
        if float(trade.get("risk_amount", 0.0)) > 0.0
    ]


def capital_weighted_r(trades: list[dict[str, Any]]) -> float:
    risk = sum(float(trade.get("risk_amount", 0.0)) for trade in trades)
    net = sum(float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))) for trade in trades)
    return net / risk if risk > 0.0 else 0.0


def profit_per_bar_in_market(result: BacktestResult) -> float:
    bars = sum(int(trade.get("duration_bars", 0)) for trade in result.trades)
    return float(result.total_net_pnl) / bars if bars > 0 else 0.0


def average_mfe_giveback(trades: list[dict[str, Any]]) -> float:
    values = [float(trade.get("mfe_giveback", 0.0)) for trade in trades]
    return float(np.mean(values)) if values else 0.0


def average_mae(trades: list[dict[str, Any]]) -> float:
    values = [float(trade.get("mae", 0.0)) for trade in trades]
    return float(np.mean(values)) if values else 0.0


def top_n_contribution(trades: list[dict[str, Any]], count: int) -> float | None:
    values = sorted(
        [float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))) for trade in trades],
        reverse=True,
    )
    total = sum(values)
    if total <= 0.0:
        return None
    return sum(values[:count]) / total


def drawdown_series(equity: list[float]) -> list[float]:
    peak = -math.inf
    output = []
    for value in equity:
        peak = max(peak, value)
        output.append((value - peak) / peak if peak > 0.0 else 0.0)
    return output


def max_consecutive(flags: list[bool]) -> int:
    best = 0
    current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        best = max(best, current)
    return best


def worst_consecutive_loss_cash(r_values: list[float], risk_pct: float, initial_equity: float) -> float:
    worst = 0.0
    current = 0.0
    equity = initial_equity
    for r_value in r_values:
        pnl = equity * risk_pct * r_value
        equity += pnl
        if pnl <= 0.0:
            current += pnl
            worst = min(worst, current)
        else:
            current = 0.0
    return worst


def worst_consecutive_month_cash(monthly_pnl: dict[str, float]) -> float:
    worst = 0.0
    current = 0.0
    for _, value in sorted(monthly_pnl.items()):
        if value <= 0.0:
            current += value
            worst = min(worst, current)
        else:
            current = 0.0
    return worst


def max_time_to_recovery(equity: list[float]) -> int:
    peak = equity[0] if equity else 0.0
    current = 0
    longest = 0
    for value in equity:
        if value >= peak:
            peak = value
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def write_csv_artifacts(output_dir: Path, stamp: str, diagnostics: dict[str, Any]) -> None:
    monthly = diagnostics["monthly_consistency"]
    pd.DataFrame(monthly["breakdown_2021"]).T.to_csv(output_dir / f"donchian_diagnostics_2021_monthly_{stamp}.csv")
    pd.DataFrame(diagnostics["failure_2021"]["by_session"]).T.to_csv(output_dir / f"donchian_diagnostics_2021_sessions_{stamp}.csv")
    pd.DataFrame(diagnostics["failure_2021"]["by_exit_reason"]).T.to_csv(output_dir / f"donchian_diagnostics_2021_exits_{stamp}.csv")


def print_summary(diagnostics: dict[str, Any], json_path: Path) -> None:
    base = diagnostics["base"]
    monthly = diagnostics["monthly_consistency"]
    comparison = diagnostics["exit_comparison"]
    risk = diagnostics["risk_survivability"]
    print("AURUM-1 Donchian Fixed-2R Diagnostics")
    print("=" * 72)
    print(f"Base net={base['net_pnl']:.2f} PF={base['profit_factor']:.2f} Sharpe={base['sharpe']:.2f} trades={base['trade_count']}")
    print("-" * 72)
    print("Cost stress split:")
    for label, result in diagnostics["cost_stress_split"]["accounting_cost_stress"].items():
        print(f"  accounting {label}: gross={result['gross_pnl']:.2f} net={result['net_pnl']:.2f} trades={result['trade_count']} PF={result['profit_factor']:.2f} Sharpe={result['sharpe']:.2f}")
    for label, result in diagnostics["cost_stress_split"]["execution_slippage_stress"].items():
        if "net_pnl" in result:
            print(f"  execution {label}: gross={result['gross_pnl']:.2f} net={result['net_pnl']:.2f} trades={result['trade_count']} PF={result['profit_factor']:.2f} Sharpe={result['sharpe']:.2f}")
    print("-" * 72)
    print(
        f"Monthly: positive={monthly['positive_months']}/{monthly['total_months']} "
        f"PF>1={monthly['months_pf_above_1']}/{monthly['total_months']} "
        f"max_loss_months={monthly['max_consecutive_losing_months']}"
    )
    print(f"Worst month: {monthly['worst_month']}")
    print(f"Best month:  {monthly['best_month']}")
    print(f"Top 3 month contribution: {monthly['top_3_month_contribution']:.2%}")
    print("-" * 72)
    print("2021 interpretation:")
    print(diagnostics["failure_2021"]["interpretation"])
    print("-" * 72)
    print("Exit comparison:")
    for name in ("fixed_2r", "donchian_low"):
        item = comparison[name]
        print(f"  {name}: net={item['net_pnl']:.2f} PF={item['profit_factor']:.2f} Sharpe={item['sharpe']:.2f} cwR={item['capital_weighted_r']:.3f} profit/bar={item['profit_per_bar_in_market']:.4f}")
    print(comparison["explanation"])
    print("-" * 72)
    print("Risk survivability:")
    for label, item in risk.items():
        print(
            f"  {label}: maxDD={item['max_drawdown']:.2%} worstMonth={item['worst_month']} "
            f"maxLossStreakCash={item['max_losing_streak_cash_impact']:.2f} "
            f"17-loss impact={item['seventeen_full_r_loss_equity_impact']:.2%} "
            f"survivable={item['seventeen_loss_streak_survivable']}"
        )
    print("-" * 72)
    print(f"Final classification: {diagnostics['classification']}")
    print("Paper readiness: failed")
    print("Live readiness: failed")
    print(f"JSON: {json_path}")


if __name__ == "__main__":
    raise SystemExit(main())
