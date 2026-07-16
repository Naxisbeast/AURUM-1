"""Research AURUM-1 edge hypotheses without changing production strategy logic.

This script is intentionally separate from the live/paper orchestrator and the
main state machine. It tests explicit market-behaviour hypotheses and simple
benchmarks using the same paper broker, risk manager, costs, slippage, and
BacktestResult math used elsewhere.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.backtesting.engine import BacktestResult, _augment_trade, build_backtest_result
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.execution import PaperBroker
from aurum1.risk import RiskManager
from aurum1.signals import CandleRow, TradeInstruction


DEFAULT_OUTPUT_DIR = ROOT / "reports" / "research"


@dataclass(frozen=True)
class ResearchSignal:
    strategy: str
    signal_bar: int
    entry_bar: int
    signal_time: str
    entry_time: str
    direction: str
    atr_at_signal: float
    stop_loss: float
    take_profit: float
    entry_price: float
    reason: str


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    market_db = args.market_db or Path(
        str(settings.get("backtesting", {}).get("market_data_db_path", "aurum1/data/backtest_market_cache.sqlite3"))
    )
    market_db = ROOT / market_db if not market_db.is_absolute() else market_db
    ohlcv = load_ohlcv("M15", market_db)
    if ohlcv.empty:
        raise RuntimeError(f"No M15 OHLCV rows available in {market_db}")

    features = build_research_features(ohlcv)
    prototype_signals = trend_pullback_continuation_signals(ohlcv, features)
    ema_signals = ema_trend_long_signals(ohlcv, features)

    initial_equity = float(settings.get("broker", {}).get("paper_initial_equity", 10000.0))
    prototype_result = run_signal_backtest(
        "trend_pullback_continuation",
        ohlcv,
        prototype_signals,
        settings,
        initial_equity=initial_equity,
        max_one_position=True,
    )
    ema_result = run_signal_backtest(
        "ema_trend_long_next_open",
        ohlcv,
        ema_signals,
        settings,
        initial_equity=initial_equity,
        max_one_position=True,
    )
    random_signals = random_matched_long_signals(
        ohlcv,
        features,
        prototype_result.total_trades,
        seed=args.random_seed,
    )
    random_result = run_signal_backtest(
        "random_matched_long_next_open",
        ohlcv,
        random_signals,
        settings,
        initial_equity=initial_equity,
        max_one_position=True,
    )
    results = {
        "trend_pullback_continuation": prototype_result,
        "ema_trend_long_next_open": ema_result,
        "random_matched_long_next_open": random_result,
    }
    random_distribution = run_random_control_distribution(
        ohlcv,
        features,
        settings,
        target_trade_count=prototype_result.total_trades,
        runs=args.random_runs,
        seed=args.random_seed,
        initial_equity=initial_equity,
    )
    walk_forward = run_research_walk_forward(
        ohlcv,
        features,
        settings,
        random_seed=args.random_seed,
        initial_equity=initial_equity,
    )
    cost_stress = (
        run_cost_stress(ohlcv, features, settings, initial_equity=initial_equity)
        if args.include_cost_stress
        else {}
    )

    buy_hold = buy_and_hold_summary(ohlcv, initial_equity)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "market_db": str(market_db),
        "rows": len(ohlcv),
        "date_range": {
            "start": ohlcv.index.min().isoformat(),
            "end": ohlcv.index.max().isoformat(),
        },
        "research_scope": {
            "production_strategy_changed": False,
            "paper_or_live_orders_sent": False,
            "prototype": "Higher-timeframe bullish trend + M15 pullback continuation",
            "max_one_position": True,
            "ml_enabled": False,
        },
        "buy_and_hold": buy_hold,
        "results": {name: result_summary(result) for name, result in results.items()},
        "random_control_distribution": random_distribution,
        "walk_forward": walk_forward,
        "cost_stress": cost_stress,
    }
    summary["research_verdict"] = research_verdict(summary)

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"edge_prototypes_{stamp}.json"
    csv_path = output_dir / f"edge_prototypes_{stamp}.csv"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    write_summary_csv(csv_path, summary)

    print_summary(summary, json_path, csv_path)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AURUM-1 research edge prototypes.")
    parser.add_argument("--market-db", type=Path, default=None, help="SQLite market cache. Defaults to settings.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--random-runs", type=int, default=20, help="Repeated random matched-entry controls.")
    parser.add_argument(
        "--include-cost-stress",
        action="store_true",
        help="Run 2x/3x spread+slippage stress for the pullback prototype.",
    )
    return parser.parse_args(argv)


def build_research_features(ohlcv: pd.DataFrame) -> pd.DataFrame:
    frame = ohlcv[["open", "high", "low", "close", "volume"]].astype(float).copy()
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    frame["ema_9"] = ema(close, 9)
    frame["ema_20"] = ema(close, 20)
    frame["ema_50"] = ema(close, 50)
    frame["ema_200"] = ema(close, 200)
    frame["atr_14"] = atr_wilder(high, low, close, 14)
    frame["atr_percentile_100"] = frame["atr_14"].rolling(100, min_periods=100).apply(
        lambda values: pd.Series(values).rank(pct=True).iloc[-1]
    )
    frame["adx_14"] = adx_wilder(high, low, close, 14)
    frame["recent_low_5"] = low.rolling(5, min_periods=5).min()
    frame["recent_high_5"] = high.rolling(5, min_periods=5).max()
    hours = frame.index.hour
    frame["session_london"] = ((hours >= 7) & (hours < 16)).astype(int)
    frame["session_ny"] = ((hours >= 13) & (hours < 22)).astype(int)

    for timeframe, rule in (("H1", "1h"), ("H4", "4h")):
        htf = resample_ohlcv(frame, rule)
        htf_features = pd.DataFrame(index=htf.index)
        htf_features[f"{timeframe}_close"] = htf["close"]
        htf_features[f"{timeframe}_ema_50"] = ema(htf["close"], 50)
        htf_features[f"{timeframe}_ema_200"] = ema(htf["close"], 200)
        frame = merge_asof_features(frame, htf_features)
    return frame


def trend_pullback_continuation_signals(ohlcv: pd.DataFrame, features: pd.DataFrame) -> list[ResearchSignal]:
    """HTF bullish gold trend + M15 pullback into value + momentum resume."""

    htf_bull = (
        (features["H1_close"] > features["H1_ema_50"])
        & (features["H1_ema_50"] > features["H1_ema_200"])
        & (features["H4_close"] > features["H4_ema_50"])
    )
    m15_bull = (features["close"] > features["ema_200"]) & (features["ema_20"] > features["ema_50"])
    session = (features["session_london"] == 1) | (features["session_ny"] == 1)
    pullback_bar = (
        (features["low"] <= features["ema_20"] + 0.25 * features["atr_14"])
        & (features["low"] >= features["ema_50"] - 0.75 * features["atr_14"])
        & (features["close"] >= features["ema_50"])
    )
    recent_pullback = pullback_bar.shift(1).rolling(5, min_periods=1).max().fillna(False).astype(bool)
    continuation = (
        (features["close"] > features["open"])
        & (features["close"] > features["high"].shift(1))
        & (features["close"] > features["ema_9"])
        & (features["ema_9"] > features["ema_20"])
    )
    signal_mask = htf_bull & m15_bull & session & recent_pullback & continuation
    return signals_from_mask(
        "trend_pullback_continuation",
        ohlcv,
        features,
        signal_mask,
        stop_mode="structure_or_atr",
    )


def ema_trend_long_signals(ohlcv: pd.DataFrame, features: pd.DataFrame) -> list[ResearchSignal]:
    """Simple benchmark: long continuation when M15/H1 trend is bullish."""

    h1_bull = (features["H1_close"] > features["H1_ema_50"]) & (features["H1_ema_50"] > features["H1_ema_200"])
    m15_bull = (features["ema_50"] > features["ema_200"]) & (features["close"] > features["ema_20"])
    cross_resume = (features["close"] > features["ema_9"]) & (features["close"].shift(1) <= features["ema_9"].shift(1))
    session = (features["session_london"] == 1) | (features["session_ny"] == 1)
    signal_mask = h1_bull & m15_bull & cross_resume & session
    return signals_from_mask(
        "ema_trend_long_next_open",
        ohlcv,
        features,
        signal_mask,
        stop_mode="atr_only",
    )


def random_matched_long_signals(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    count: int,
    *,
    seed: int,
) -> list[ResearchSignal]:
    valid = features.index[
        (features["atr_14"].notna())
        & (features["recent_low_5"].notna())
        & (((features["session_london"] == 1) | (features["session_ny"] == 1)))
    ]
    positions = [ohlcv.index.get_loc(timestamp) for timestamp in valid if ohlcv.index.get_loc(timestamp) + 1 < len(ohlcv)]
    rng = random.Random(seed)
    chosen = set(rng.sample(positions, min(count, len(positions))))
    mask = pd.Series(False, index=features.index)
    if chosen:
        mask.iloc[list(chosen)] = True
    return signals_from_mask(
        "random_matched_long_next_open",
        ohlcv,
        features,
        mask,
        stop_mode="atr_only",
    )


def run_random_control_distribution(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    *,
    target_trade_count: int,
    runs: int,
    seed: int,
    initial_equity: float,
) -> dict[str, Any]:
    if runs <= 0:
        return {"runs": 0}
    summaries: list[dict[str, Any]] = []
    for offset in range(runs):
        signals = random_matched_long_signals(
            ohlcv,
            features,
            target_trade_count,
            seed=seed + offset,
        )
        result = run_signal_backtest(
            f"random_matched_long_next_open_seed_{seed + offset}",
            ohlcv,
            signals,
            settings,
            initial_equity=initial_equity,
            max_one_position=True,
        )
        summaries.append(result_summary(result))
    net_values = [float(item["net_pnl"]) for item in summaries]
    pf_values = [float(item["profit_factor"]) for item in summaries]
    sharpe_values = [float(item["sharpe"]) for item in summaries]
    return {
        "runs": runs,
        "target_trade_count": target_trade_count,
        "positive_net_runs": sum(1 for value in net_values if value > 0.0),
        "median_net_pnl": float(np.median(net_values)) if net_values else 0.0,
        "mean_net_pnl": float(np.mean(net_values)) if net_values else 0.0,
        "pct95_net_pnl": float(np.percentile(net_values, 95)) if net_values else 0.0,
        "median_profit_factor": float(np.median(pf_values)) if pf_values else 0.0,
        "median_sharpe": float(np.median(sharpe_values)) if sharpe_values else 0.0,
        "best_random": max(summaries, key=lambda item: item["net_pnl"], default={}),
        "worst_random": min(summaries, key=lambda item: item["net_pnl"], default={}),
    }


def run_research_walk_forward(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    *,
    random_seed: int,
    initial_equity: float,
) -> dict[str, Any]:
    backtesting = settings.get("backtesting", {})
    train_bars = int(backtesting.get("train_bars", 6552))
    test_bars = int(backtesting.get("test_bars", 1638))
    step_bars = int(backtesting.get("step_bars", test_bars))
    if step_bars < test_bars and not bool(backtesting.get("allow_overlap", False)):
        raise ValueError("Research walk-forward requires non-overlapping windows unless allow_overlap=true")

    windows: dict[str, list[dict[str, Any]]] = {
        "trend_pullback_continuation": [],
        "ema_trend_long_next_open": [],
        "random_matched_long_next_open": [],
    }
    start = 0
    window_index = 0
    while start + train_bars + test_bars <= len(ohlcv):
        test_start = start + train_bars
        test_end = test_start + test_bars
        window_ohlcv = ohlcv.iloc[test_start:test_end].copy()
        window_features = features.loc[window_ohlcv.index].copy()
        prototype_signals = trend_pullback_continuation_signals(window_ohlcv, window_features)
        ema_signals = ema_trend_long_signals(window_ohlcv, window_features)

        prototype = run_signal_backtest(
            "trend_pullback_continuation",
            window_ohlcv,
            prototype_signals,
            settings,
            initial_equity=initial_equity,
            max_one_position=True,
        )
        ema_result = run_signal_backtest(
            "ema_trend_long_next_open",
            window_ohlcv,
            ema_signals,
            settings,
            initial_equity=initial_equity,
            max_one_position=True,
        )
        random_signals = random_matched_long_signals(
            window_ohlcv,
            window_features,
            prototype.total_trades,
            seed=random_seed + window_index,
        )
        random_result = run_signal_backtest(
            "random_matched_long_next_open",
            window_ohlcv,
            random_signals,
            settings,
            initial_equity=initial_equity,
            max_one_position=True,
        )

        for name, result in (
            ("trend_pullback_continuation", prototype),
            ("ema_trend_long_next_open", ema_result),
            ("random_matched_long_next_open", random_result),
        ):
            item = result_summary(result)
            item["window"] = window_index + 1
            item["start"] = window_ohlcv.index.min().isoformat()
            item["end"] = window_ohlcv.index.max().isoformat()
            windows[name].append(item)

        window_index += 1
        start += step_bars

    return {
        "train_bars": train_bars,
        "test_bars": test_bars,
        "step_bars": step_bars,
        "allow_overlap": bool(backtesting.get("allow_overlap", False)),
        "window_count": window_index,
        "strategies": {name: aggregate_window_summaries(items) for name, items in windows.items()},
        "windows": windows,
    }


def run_cost_stress(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    *,
    initial_equity: float,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    signals = trend_pullback_continuation_signals(ohlcv, features)
    for multiplier in (2.0, 3.0):
        stressed = json.loads(json.dumps(settings))
        stressed.setdefault("execution", {})
        stressed.setdefault("risk", {})
        stressed["execution"]["paper_spread_pips"] = float(settings.get("execution", {}).get("paper_spread_pips", 1.5)) * multiplier
        stressed["execution"]["slippage_std_pips"] = float(settings.get("execution", {}).get("slippage_std_pips", 0.5)) * multiplier
        stressed["risk"]["max_spread_pips"] = max(
            float(stressed["execution"]["paper_spread_pips"]) + 0.1,
            float(stressed["risk"].get("max_spread_pips", 3.0)),
        )
        result = run_signal_backtest(
            f"trend_pullback_continuation_cost_{multiplier:g}x",
            ohlcv,
            signals,
            stressed,
            initial_equity=initial_equity,
            max_one_position=True,
        )
        output[f"{multiplier:g}x"] = result_summary(result)
    return output


def signals_from_mask(
    strategy: str,
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    signal_mask: pd.Series,
    *,
    stop_mode: str,
) -> list[ResearchSignal]:
    signals: list[ResearchSignal] = []
    for signal_time in features.index[signal_mask.fillna(False)]:
        signal_bar = int(ohlcv.index.get_loc(signal_time))
        entry_bar = signal_bar + 1
        if entry_bar >= len(ohlcv):
            continue
        entry_time = ohlcv.index[entry_bar]
        entry = float(ohlcv.iloc[entry_bar]["open"])
        atr_value = float(features.loc[signal_time, "atr_14"])
        if not math.isfinite(atr_value) or atr_value <= 0.0:
            continue
        if stop_mode == "structure_or_atr":
            recent_low = float(features.loc[signal_time, "recent_low_5"])
            stop_loss = min(recent_low, entry - 1.5 * atr_value)
        else:
            stop_loss = entry - 1.5 * atr_value
        if stop_loss >= entry:
            continue
        risk_distance = entry - stop_loss
        take_profit = entry + 2.0 * risk_distance
        signals.append(
            ResearchSignal(
                strategy=strategy,
                signal_bar=signal_bar,
                entry_bar=entry_bar,
                signal_time=signal_time.isoformat(),
                entry_time=entry_time.isoformat(),
                direction="BUY",
                atr_at_signal=atr_value,
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                entry_price=entry,
                reason=stop_mode,
            )
        )
    return signals


def run_signal_backtest(
    name: str,
    ohlcv: pd.DataFrame,
    signals: list[ResearchSignal],
    settings: dict[str, Any],
    *,
    initial_equity: float,
    max_one_position: bool,
) -> BacktestResult:
    run_settings = prepare_research_settings(settings)
    broker = PaperBroker(run_settings)
    risk_manager = RiskManager(run_settings)
    signals_by_entry: dict[int, list[ResearchSignal]] = {}
    for signal in signals:
        signals_by_entry.setdefault(signal.entry_bar, []).append(signal)

    equity_curve: list[float] = []
    trade_history_cursor = 0
    open_meta: dict[str, dict[str, Any]] = {}
    closed_trades: list[dict[str, Any]] = []
    rejected = 0
    approved = 0
    rejection_reasons: Counter[str] = Counter()

    for bar_index, (timestamp, row) in enumerate(ohlcv.iterrows()):
        candle = basic_candle(timestamp, row)
        broker.update_prices(candle)
        new_closed = broker._trade_history[trade_history_cursor:]
        for trade in new_closed:
            meta = open_meta.pop(str(trade.get("position_id")), {})
            closed_trades.append(_augment_trade(trade, meta, bar_index, run_settings, close_timestamp=timestamp))
        trade_history_cursor = len(broker._trade_history)

        for signal in signals_by_entry.get(bar_index, []):
            if max_one_position and broker.get_open_positions():
                rejected += 1
                rejection_reasons["open_position_skip"] += 1
                continue
            instruction = TradeInstruction(
                timestamp=pd.Timestamp(signal.signal_time).to_pydatetime(),
                direction=signal.direction,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                atr_at_entry=signal.atr_at_signal,
                signal_score=1.0,
                regime="TRENDING_UP",
                confidence=1.0,
                machine_mode=name,
            )
            account = broker.get_account_state()
            risk_order = risk_manager.evaluate(instruction, account, list(broker._trade_history))
            if not risk_order.approved:
                rejected += 1
                rejection_reasons[risk_order.rejection_reason or "unknown"] += 1
                continue
            result = broker.submit_order(risk_order)
            if result.success and result.order_id:
                approved += 1
                open_meta[result.order_id] = {
                    "strategy": name,
                    "regime": "TRENDING_UP",
                    "signal_bar": signal.signal_bar,
                    "signal_time": signal.signal_time,
                    "open_bar": bar_index,
                    "open_time": pd.Timestamp(timestamp).isoformat(),
                    "market_open_time": pd.Timestamp(timestamp).isoformat(),
                    "requested_entry_price": signal.entry_price,
                    "entry_fill_basis_price": signal.entry_price,
                    "entry_gap_fill": False,
                    "fill_type": "next_open",
                    "actual_entry_price": result.fill_price,
                    "atr_at_entry": signal.atr_at_signal,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "risk_amount": risk_order.risk_amount,
                    "research_reason": signal.reason,
                }
            else:
                rejected += 1
                rejection_reasons[result.rejection_reason or "unknown"] += 1

        equity_curve.append(float(broker.get_account_state().equity))

    final_timestamp = ohlcv.index[-1]
    for position in list(broker.get_open_positions()):
        result = broker.close_position(position.position_id, "backtest_end")
        if result.success:
            trade = broker._trade_history[-1]
            meta = open_meta.pop(str(trade.get("position_id")), {})
            closed_trades.append(_augment_trade(trade, meta, len(ohlcv) - 1, run_settings, close_timestamp=final_timestamp))
    if equity_curve:
        equity_curve[-1] = float(broker.get_account_state().equity)

    return build_backtest_result(
        equity_curve=equity_curve,
        trades=closed_trades,
        start_date=pd.Timestamp(ohlcv.index[0]).to_pydatetime(),
        end_date=pd.Timestamp(final_timestamp).to_pydatetime(),
        instrument="XAU_USD",
        mode=name,
        initial_equity=initial_equity,
        total_bars=len(ohlcv),
        total_signals=len(signals),
        signals_approved=approved,
        signals_rejected=rejected,
        rejection_reasons=dict(rejection_reasons),
    )


def prepare_research_settings(settings: dict[str, Any]) -> dict[str, Any]:
    prepared = json.loads(json.dumps(settings))
    prepared.setdefault("broker", {})
    prepared["broker"]["paper_trade"] = True
    prepared.setdefault("backtesting", {})
    prepared.setdefault("execution", {})
    return prepared


def result_summary(result: BacktestResult) -> dict[str, Any]:
    monthly = monthly_net_pnl(result.trades)
    yearly = yearly_net_pnl(result.trades)
    sessions = session_net_pnl(result.trades)
    return {
        "mode": result.mode,
        "trade_count": result.total_trades,
        "signals": result.total_signals,
        "approved": result.signals_approved,
        "rejected": result.signals_rejected,
        "rejection_reasons": result.rejection_reasons,
        "net_pnl": result.total_net_pnl,
        "gross_pnl": result.total_gross_pnl,
        "spread_cost": result.total_spread_cost,
        "slippage_cost": result.total_slippage_cost,
        "profit_factor": result.profit_factor if result.total_trades else 0.0,
        "win_rate": result.win_rate,
        "sharpe": result.sharpe_ratio,
        "sortino": result.sortino_ratio,
        "max_drawdown": result.max_drawdown_pct,
        "avg_holding_bars": result.avg_trade_duration_bars,
        "avg_r": average_r(result.trades),
        "capital_weighted_r": capital_weighted_r(result.trades),
        "median_r": median_r(result.trades),
        "exit_reasons": dict(Counter(str(trade.get("reason", "unknown")) for trade in result.trades)),
        "fill_types": dict(Counter(str(trade.get("fill_type", "unknown")) for trade in result.trades)),
        "positive_months": sum(1 for value in monthly.values() if value > 0.0),
        "negative_months": sum(1 for value in monthly.values() if value < 0.0),
        "top_3_month_net_pnl": sum(value for _, value in sorted(monthly.items(), key=lambda item: item[1], reverse=True)[:3]),
        "top_3_month_share_of_net": (
            sum(value for _, value in sorted(monthly.items(), key=lambda item: item[1], reverse=True)[:3]) / result.total_net_pnl
            if result.total_net_pnl > 0.0
            else None
        ),
        "monthly_net_pnl": monthly,
        "yearly_net_pnl": yearly,
        "session_net_pnl": sessions,
    }


def research_verdict(summary: dict[str, Any]) -> dict[str, Any]:
    """Fail closed: identify promising research, but never grant deployment approval."""

    prototype = summary["results"]["trend_pullback_continuation"]
    ema = summary["results"]["ema_trend_long_next_open"]
    random_dist = summary.get("random_control_distribution", {})
    wf = summary["walk_forward"]["strategies"]
    prototype_wf = wf["trend_pullback_continuation"]
    random_wf = wf["random_matched_long_next_open"]
    cost_stress = summary.get("cost_stress", {})

    gates = {
        "positive_net_pnl": float(prototype["net_pnl"]) > 0.0,
        "profit_factor_above_1_20": float(prototype["profit_factor"]) >= 1.20,
        "sharpe_above_1_00": float(prototype["sharpe"]) >= 1.00,
        "beats_ema_net_pnl": float(prototype["net_pnl"]) > float(ema["net_pnl"]),
        "beats_random_95th_percentile_net": (
            bool(random_dist.get("runs", 0))
            and float(prototype["net_pnl"]) > float(random_dist.get("pct95_net_pnl", math.inf))
        ),
        "walk_forward_positive_net_at_least_16_of_24": int(prototype_wf["positive_net_windows"]) >= 16,
        "walk_forward_mean_pf_above_1_20": float(prototype_wf["mean_profit_factor"]) >= 1.20,
        "walk_forward_beats_random_total_net": float(prototype_wf["total_net_pnl"])
        > float(random_wf["total_net_pnl"]),
        "month_concentration_below_50pct": (
            prototype["top_3_month_share_of_net"] is not None
            and float(prototype["top_3_month_share_of_net"]) <= 0.50
        ),
        "positive_months_at_least_16_of_24": int(prototype["positive_months"]) >= 16,
        "cost_stress_3x_pf_above_1_10": (
            "3x" in cost_stress and float(cost_stress["3x"]["profit_factor"]) >= 1.10
        ),
    }
    failed = [name for name, passed in gates.items() if not passed]
    passed_count = len(gates) - len(failed)
    if (
        gates["positive_net_pnl"]
        and gates["profit_factor_above_1_20"]
        and gates["sharpe_above_1_00"]
        and gates["walk_forward_mean_pf_above_1_20"]
        and gates["cost_stress_3x_pf_above_1_10"]
    ):
        status = "promising_research_lead_not_paper_ready"
    else:
        status = "not_validated"
    return {
        "status": status,
        "passed_gates": passed_count,
        "total_gates": len(gates),
        "gates": gates,
        "failed_gates": failed,
        "paper_readiness": "failed",
        "live_readiness": "failed",
        "reason": (
            "Prototype is promising but still concentration-sensitive and not cleanly separated "
            "from bullish gold beta/random long controls."
        ),
    }


def buy_and_hold_summary(ohlcv: pd.DataFrame, initial_equity: float) -> dict[str, Any]:
    start = float(ohlcv["close"].iloc[0])
    end = float(ohlcv["close"].iloc[-1])
    pct_return = end / start - 1.0
    return {
        "start_close": start,
        "end_close": end,
        "price_return_pct": pct_return,
        "one_ounce_pnl": end - start,
        "initial_equity_equivalent_pnl": initial_equity * pct_return,
    }


def monthly_net_pnl(trades: list[dict[str, Any]]) -> dict[str, float]:
    monthly: dict[str, float] = {}
    for trade in trades:
        timestamp = str(trade.get("market_close_time", trade.get("closed_at", "")))
        month = timestamp[:7] if len(timestamp) >= 7 else "unknown"
        monthly[month] = monthly.get(month, 0.0) + float(
            trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))
        )
    return dict(sorted(monthly.items()))


def yearly_net_pnl(trades: list[dict[str, Any]]) -> dict[str, float]:
    yearly: dict[str, float] = {}
    for trade in trades:
        timestamp = str(trade.get("market_close_time", trade.get("closed_at", "")))
        year = timestamp[:4] if len(timestamp) >= 4 else "unknown"
        yearly[year] = yearly.get(year, 0.0) + float(
            trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))
        )
    return dict(sorted(yearly.items()))


def session_net_pnl(trades: list[dict[str, Any]]) -> dict[str, float]:
    sessions: dict[str, float] = {"asia": 0.0, "london": 0.0, "ny": 0.0, "overlap": 0.0, "other": 0.0}
    for trade in trades:
        timestamp = pd.Timestamp(str(trade.get("market_open_time", trade.get("open_time", ""))))
        hour = int(timestamp.hour)
        session = "other"
        if 13 <= hour < 16:
            session = "overlap"
        elif 7 <= hour < 13:
            session = "london"
        elif 16 <= hour < 22:
            session = "ny"
        elif 0 <= hour < 7:
            session = "asia"
        sessions[session] += float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0))))
    return {key: value for key, value in sessions.items() if abs(value) > 1e-12}


def aggregate_window_summaries(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {
            "windows": 0,
            "positive_sharpe_windows": 0,
            "positive_net_windows": 0,
            "mean_sharpe": 0.0,
            "mean_profit_factor": 0.0,
            "mean_net_pnl": 0.0,
            "total_net_pnl": 0.0,
            "median_net_pnl": 0.0,
            "mean_trade_count": 0.0,
        }
    return {
        "windows": len(items),
        "positive_sharpe_windows": sum(1 for item in items if float(item["sharpe"]) > 0.0),
        "positive_net_windows": sum(1 for item in items if float(item["net_pnl"]) > 0.0),
        "mean_sharpe": float(np.mean([float(item["sharpe"]) for item in items])),
        "mean_profit_factor": float(np.mean([float(item["profit_factor"]) for item in items])),
        "mean_net_pnl": float(np.mean([float(item["net_pnl"]) for item in items])),
        "total_net_pnl": float(np.sum([float(item["net_pnl"]) for item in items])),
        "median_net_pnl": float(np.median([float(item["net_pnl"]) for item in items])),
        "mean_trade_count": float(np.mean([float(item["trade_count"]) for item in items])),
        "worst_window_net_pnl": float(np.min([float(item["net_pnl"]) for item in items])),
        "best_window_net_pnl": float(np.max([float(item["net_pnl"]) for item in items])),
    }


def average_r(trades: list[dict[str, Any]]) -> float:
    values = r_values(trades)
    return float(np.mean(values)) if values else 0.0


def median_r(trades: list[dict[str, Any]]) -> float:
    values = r_values(trades)
    return float(np.median(values)) if values else 0.0


def capital_weighted_r(trades: list[dict[str, Any]]) -> float:
    risk = sum(float(trade.get("risk_amount", 0.0)) for trade in trades)
    net = sum(float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))) for trade in trades)
    return net / risk if risk > 0.0 else 0.0


def r_values(trades: list[dict[str, Any]]) -> list[float]:
    return [
        float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))) / float(trade["risk_amount"])
        for trade in trades
        if float(trade.get("risk_amount", 0.0)) > 0.0
    ]


def write_summary_csv(path: Path, summary: dict[str, Any]) -> None:
    rows = []
    for name, result in summary["results"].items():
        rows.append({"strategy": name, **{key: value for key, value in result.items() if not isinstance(value, dict)}})
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def print_summary(summary: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    print("AURUM-1 Edge Prototype Research")
    print("=" * 68)
    print(f"Rows:              {summary['rows']}")
    print(f"Date range:        {summary['date_range']['start']} -> {summary['date_range']['end']}")
    print(f"Buy & hold return: {summary['buy_and_hold']['price_return_pct']:.2%}")
    print("-" * 68)
    print(f"{'strategy':<34}{'trades':>8}{'net':>12}{'PF':>8}{'Sharpe':>9}{'maxDD':>9}{'cwR':>8}")
    for name, result in summary["results"].items():
        concentration = result.get("top_3_month_share_of_net")
        concentration_text = "n/a" if concentration is None else f"{concentration:.0%}"
        print(
            f"{name:<34}"
            f"{result['trade_count']:>8}"
            f"{result['net_pnl']:>12.2f}"
            f"{result['profit_factor']:>8.2f}"
            f"{result['sharpe']:>9.2f}"
            f"{result['max_drawdown']:>9.2%}"
            f"{result['capital_weighted_r']:>8.3f}"
        )
        print(
            f"{'  months/year/session':<34}"
            f"{str(result['positive_months']) + '/' + str(result['positive_months'] + result['negative_months']):>8}"
            f"{'top3=' + concentration_text:>12}"
            f"{str(result['yearly_net_pnl']):>42}"
        )
    print("-" * 68)
    print("Walk-forward:")
    for name, stats in summary["walk_forward"]["strategies"].items():
        print(
            f"{name:<34}"
            f"pos_net={stats['positive_net_windows']:>2}/{stats['windows']:<2} "
            f"pos_sharpe={stats['positive_sharpe_windows']:>2}/{stats['windows']:<2} "
            f"meanPF={stats['mean_profit_factor']:.2f} "
            f"meanSharpe={stats['mean_sharpe']:.2f} "
            f"totalNet={stats['total_net_pnl']:.2f}"
        )
    random_dist = summary.get("random_control_distribution", {})
    if random_dist.get("runs", 0):
        print("-" * 68)
        print(
            "Random control distribution: "
            f"runs={random_dist['runs']} "
            f"positive={random_dist['positive_net_runs']}/{random_dist['runs']} "
            f"medianNet={random_dist['median_net_pnl']:.2f} "
            f"p95Net={random_dist['pct95_net_pnl']:.2f}"
        )
    if summary.get("cost_stress"):
        print("-" * 68)
        print("Cost stress (trend_pullback_continuation):")
        for label, result in summary["cost_stress"].items():
            print(f"  {label}: net={result['net_pnl']:.2f} PF={result['profit_factor']:.2f} Sharpe={result['sharpe']:.2f}")
    verdict = summary.get("research_verdict", {})
    if verdict:
        print("-" * 68)
        print(
            "Research verdict: "
            f"{verdict['status']} "
            f"({verdict['passed_gates']}/{verdict['total_gates']} gates passed)"
        )
        if verdict.get("failed_gates"):
            print("Failed gates:")
            for gate in verdict["failed_gates"]:
                print(f"  - {gate}")
    print("-" * 68)
    print("Research status: no paper/live approval from this script.")
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")


def basic_candle(timestamp: Any, row: pd.Series) -> CandleRow:
    return CandleRow(
        timestamp=pd.Timestamp(timestamp).to_pydatetime(),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        atr_14=max(1e-9, float(row["high"] - row["low"])),
        adx_14=0.0,
        ema_9=float(row["close"]),
        ema_20=float(row["close"]),
        session_london=1,
        session_ny=0,
        session_overlap=0,
    )


def resample_ohlcv(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (
        frame[["open", "high", "low", "close", "volume"]]
        .resample(rule, label="right", closed="right")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


def merge_asof_features(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    merged = pd.merge_asof(
        left.sort_index().reset_index(names="timestamp"),
        right.sort_index().reset_index(names="timestamp"),
        on="timestamp",
        direction="backward",
    )
    return merged.set_index("timestamp")


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def adx_wilder(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Compute Wilder's ADX (Average Directional Index)."""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0),
        index=high.index,
    )
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr.replace(0.0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


if __name__ == "__main__":
    raise SystemExit(main())
