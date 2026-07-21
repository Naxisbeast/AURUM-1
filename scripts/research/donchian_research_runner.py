"""Dedicated Donchian breakout research runner for AURUM-1.

This script is research-only. It does not touch live/paper orchestrator state
or submit external broker orders. It tests whether clean Donchian breakout
timing adds value beyond random long exposure and simple trend filters.
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

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.backtesting.engine import BacktestResult, _augment_trade, build_backtest_result
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.execution import PaperBroker
from aurum1.risk import RiskManager
from aurum1.signals import CandleRow, TradeInstruction
from scripts.research.research_edge_prototypes import build_research_features


DEFAULT_OUTPUT_DIR = ROOT / "reports" / "research"


@dataclass(frozen=True)
class DonchianSignal:
    strategy: str
    signal_bar: int
    entry_bar: int
    signal_time: str
    entry_time: str
    entry_price: float
    atr_at_signal: float
    stop_loss: float
    take_profit: float
    reason: str
    direction: str = "BUY"


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
    initial_equity = float(settings.get("broker", {}).get("paper_initial_equity", 10000.0))

    variants = run_exit_comparison(
        ohlcv,
        features,
        settings,
        lookback=args.lookback,
        htf_filter=False,
        initial_equity=initial_equity,
    )
    htf_variants = run_exit_comparison(
        ohlcv,
        features,
        settings,
        lookback=args.lookback,
        htf_filter=True,
        initial_equity=initial_equity,
    )
    best_name, best_result, best_filter, best_exit = choose_best_variant(variants, htf_variants)
    random_distribution = run_random_controls(
        ohlcv,
        features,
        settings,
        lookback=args.lookback,
        target_trade_count=best_result.total_trades,
        htf_filter=best_filter,
        exit_mode=best_exit,
        runs=args.random_runs,
        seed=args.random_seed,
        initial_equity=initial_equity,
    )
    yearly = yearly_decomposition(
        ohlcv,
        features,
        settings,
        lookback=args.lookback,
        htf_filter=best_filter,
        exit_mode=best_exit,
        initial_equity=initial_equity,
    )
    cost_stress = run_cost_stress(
        ohlcv,
        features,
        settings,
        lookback=args.lookback,
        htf_filter=best_filter,
        exit_mode=best_exit,
        initial_equity=initial_equity,
    )

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "market_db": str(market_db),
        "rows": len(ohlcv),
        "date_range": {"start": ohlcv.index.min().isoformat(), "end": ohlcv.index.max().isoformat()},
        "research_scope": {
            "production_strategy_changed": False,
            "paper_or_live_orders_sent": False,
            "strategy_family": "Donchian M15 long-only breakout",
            "lookback": args.lookback,
            "ml_enabled": False,
        },
        "exit_comparison_raw": {name: result_summary(result) for name, result in variants.items()},
        "exit_comparison_htf": {name: result_summary(result) for name, result in htf_variants.items()},
        "best_variant": {
            "name": best_name,
            "htf_filter": best_filter,
            "exit_mode": best_exit,
            "summary": result_summary(best_result),
        },
        "random_control_distribution": random_distribution,
        "yearly": yearly,
        "cost_stress": cost_stress,
    }
    summary["verdict"] = verdict(summary)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = args.output_dir / f"donchian_research_{stamp}.json"
    csv_path = args.output_dir / f"donchian_research_{stamp}.csv"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    write_variant_csv(csv_path, summary)
    print_summary(summary, json_path, csv_path)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Donchian breakout research validation.")
    parser.add_argument("--market-db", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--lookback", type=int, default=20)
    parser.add_argument("--random-runs", type=int, default=100)
    parser.add_argument("--random-seed", type=int, default=42)
    return parser.parse_args(argv)


def run_exit_comparison(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    *,
    lookback: int,
    htf_filter: bool,
    initial_equity: float,
) -> dict[str, BacktestResult]:
    signals = donchian_signals(ohlcv, features, lookback=lookback, htf_filter=htf_filter)
    prefix = "donchian_htf" if htf_filter else "donchian_raw"
    return {
        f"{prefix}_fixed_2r": run_donchian_backtest(
            f"{prefix}_fixed_2r",
            ohlcv,
            features,
            signals,
            settings_with_exit(settings, "FIXED"),
            exit_mode="FIXED",
            initial_equity=initial_equity,
            max_one_position=True,
        ),
        f"{prefix}_atr_trail": run_donchian_backtest(
            f"{prefix}_atr_trail",
            ohlcv,
            features,
            signals_without_take_profit(signals),
            settings_with_exit(settings, "ATR_TRAIL"),
            exit_mode="ATR_TRAIL",
            initial_equity=initial_equity,
            max_one_position=True,
        ),
        f"{prefix}_donchian_low": run_donchian_backtest(
            f"{prefix}_donchian_low",
            ohlcv,
            features,
            signals_without_take_profit(signals),
            settings_with_exit(settings, "FIXED"),
            exit_mode="DONCHIAN_LOW",
            initial_equity=initial_equity,
            max_one_position=True,
            lookback=lookback,
        ),
        f"{prefix}_atr_or_donchian": run_donchian_backtest(
            f"{prefix}_atr_or_donchian",
            ohlcv,
            features,
            signals_without_take_profit(signals),
            settings_with_exit(settings, "ATR_TRAIL"),
            exit_mode="ATR_OR_DONCHIAN",
            initial_equity=initial_equity,
            max_one_position=True,
            lookback=lookback,
        ),
    }


def choose_best_variant(
    raw: dict[str, BacktestResult],
    htf: dict[str, BacktestResult],
) -> tuple[str, BacktestResult, bool, str]:
    all_items = {**raw, **htf}
    name, result = max(all_items.items(), key=lambda item: (float(item[1].sharpe_ratio), float(item[1].profit_factor)))
    htf_filter = name.startswith("donchian_htf")
    if name.endswith("fixed_2r"):
        exit_mode = "FIXED"
    elif name.endswith("atr_trail"):
        exit_mode = "ATR_TRAIL"
    elif name.endswith("donchian_low"):
        exit_mode = "DONCHIAN_LOW"
    else:
        exit_mode = "ATR_OR_DONCHIAN"
    return name, result, htf_filter, exit_mode


def donchian_signals(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    *,
    lookback: int,
    htf_filter: bool,
    seed_mask: pd.Series | None = None,
) -> list[DonchianSignal]:
    high_break = features["close"] > features["high"].rolling(lookback, min_periods=lookback).max().shift(1)
    low_break = features["close"] < features["low"].rolling(lookback, min_periods=lookback).min().shift(1)
    valid = features["atr_14"].notna()
    buy_valid = high_break & valid
    sell_valid = low_break & valid
    if htf_filter:
        buy_valid &= htf_bull_slope_filter(features)
        sell_valid &= htf_bear_slope_filter(features)
    if seed_mask is not None:
        buy_valid = seed_mask & valid
        sell_valid = seed_mask & valid
        if htf_filter:
            buy_valid &= htf_bull_slope_filter(features)
            sell_valid &= htf_bear_slope_filter(features)
    signals: list[DonchianSignal] = []
    for direction, mask in [("BUY", buy_valid), ("SELL", sell_valid)]:
        for signal_time in features.index[mask.fillna(False)]:
            signal_bar = int(ohlcv.index.get_loc(signal_time))
            entry_bar = signal_bar + 1
            if entry_bar >= len(ohlcv):
                continue
            entry_time = ohlcv.index[entry_bar]
            entry = float(ohlcv.iloc[entry_bar]["open"])
            atr_value = float(features.loc[signal_time, "atr_14"])
            if not math.isfinite(atr_value) or atr_value <= 0.0:
                continue
            if direction == "BUY":
                stop_loss = entry - 2.0 * atr_value
                if stop_loss >= entry:
                    continue
                take_profit = entry + 2.0 * (entry - stop_loss)
            else:
                stop_loss = entry + 2.0 * atr_value
                if stop_loss <= entry:
                    continue
                take_profit = entry - 2.0 * (stop_loss - entry)
            signals.append(
                DonchianSignal(
                    strategy="donchian_long_short",
                    signal_bar=signal_bar,
                    entry_bar=entry_bar,
                    signal_time=signal_time.isoformat(),
                    entry_time=entry_time.isoformat(),
                    entry_price=entry,
                    atr_at_signal=atr_value,
                    stop_loss=float(stop_loss),
                    take_profit=float(take_profit),
                    direction=direction,
                    reason=f"donchian_20_{direction.lower()}_breakout",
                )
            )
    return signals


def htf_bull_slope_filter(features: pd.DataFrame) -> pd.Series:
    h4_step = 16 * 5
    return (
        features["H4_ema_200"].notna()
        & (features["H4_ema_200"] > features["H4_ema_200"].shift(h4_step))
        & (features["H4_close"] > features["H4_ema_200"])
    )


def htf_bear_slope_filter(features: pd.DataFrame) -> pd.Series:
    h4_step = 16 * 5
    return (
        features["H4_ema_200"].notna()
        & (features["H4_ema_200"] < features["H4_ema_200"].shift(h4_step))
        & (features["H4_close"] < features["H4_ema_200"])
    )


def random_matched_signals(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    *,
    count: int,
    lookback: int,
    htf_filter: bool,
    seed: int,
) -> list[DonchianSignal]:
    eligible = features["atr_14"].notna() & features["high"].rolling(lookback, min_periods=lookback).max().notna()
    if htf_filter:
        eligible &= htf_bull_slope_filter(features)
    positions = [int(ohlcv.index.get_loc(ts)) for ts in features.index[eligible.fillna(False)]]
    positions = [pos for pos in positions if pos + 1 < len(ohlcv)]
    rng = random.Random(seed)
    chosen = set(rng.sample(positions, min(count, len(positions))))
    mask = pd.Series(False, index=features.index)
    if chosen:
        mask.iloc[list(chosen)] = True
    return donchian_signals(ohlcv, features, lookback=lookback, htf_filter=htf_filter, seed_mask=mask)


def signals_without_take_profit(signals: list[DonchianSignal]) -> list[DonchianSignal]:
    return [
        DonchianSignal(
            **{
                **signal.__dict__,
                "take_profit": signal.entry_price + 1_000_000.0,
            }
        )
        for signal in signals
    ]


def run_donchian_backtest(
    name: str,
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    signals: list[DonchianSignal],
    settings: dict[str, Any],
    *,
    exit_mode: str,
    initial_equity: float,
    max_one_position: bool,
    lookback: int = 20,
) -> BacktestResult:
    broker = PaperBroker(settings)
    risk_manager = RiskManager(settings)
    signals_by_entry: dict[int, list[DonchianSignal]] = {}
    for signal in signals:
        signals_by_entry.setdefault(signal.entry_bar, []).append(signal)

    equity_curve: list[float] = []
    trade_history_cursor = 0
    open_meta: dict[str, dict[str, Any]] = {}
    closed_trades: list[dict[str, Any]] = []
    pending_exits: set[str] = set()
    rejected = 0
    approved = 0
    rejection_reasons: Counter[str] = Counter()
    timestamps = ohlcv.index
    opens = ohlcv["open"].astype(float).to_numpy()
    highs = ohlcv["high"].astype(float).to_numpy()
    lows = ohlcv["low"].astype(float).to_numpy()
    closes = ohlcv["close"].astype(float).to_numpy()
    volumes = ohlcv["volume"].astype(float).to_numpy()
    atr_values = features["atr_14"].astype(float).fillna(0.0).to_numpy()
    ema_9 = features["ema_9"].astype(float).fillna(0.0).to_numpy() if "ema_9" in features else closes
    ema_20 = features["ema_20"].astype(float).fillna(0.0).to_numpy() if "ema_20" in features else closes
    exit_lows = features["low"].rolling(lookback, min_periods=lookback).min().shift(1).astype(float).to_numpy()

    for bar_index, timestamp in enumerate(timestamps):
        candle = CandleRow(
            timestamp=pd.Timestamp(timestamp).to_pydatetime(),
            open=float(opens[bar_index]),
            high=float(highs[bar_index]),
            low=float(lows[bar_index]),
            close=float(closes[bar_index]),
            volume=float(volumes[bar_index]),
            atr_14=max(1e-9, float(atr_values[bar_index])),
            adx_14=0.0,
            ema_9=float(ema_9[bar_index]),
            ema_20=float(ema_20[bar_index]),
            session_london=1,
            session_ny=0,
            session_overlap=0,
        )

        if pending_exits:
            for position in list(broker.get_open_positions()):
                if position.position_id in pending_exits:
                    broker._close_position_at_price(position.position_id, float(opens[bar_index]), "donchian_low_exit")
            pending_exits.clear()

        broker.update_prices(candle)
        open_position_ids = {position.position_id for position in broker.get_open_positions()}
        new_closed = broker._trade_history[trade_history_cursor:]
        for trade in new_closed:
            position_id = str(trade.get("position_id"))
            meta = open_meta.get(position_id, {})
            if position_id not in open_position_ids:
                open_meta.pop(position_id, None)
            closed_trades.append(_augment_trade(trade, meta, bar_index, settings, close_timestamp=timestamp))
        trade_history_cursor = len(broker._trade_history)

        if exit_mode in {"DONCHIAN_LOW", "ATR_OR_DONCHIAN"} and bar_index >= lookback:
            exit_level = float(exit_lows[bar_index])
            if math.isfinite(exit_level) and float(closes[bar_index]) < exit_level:
                for position in broker.get_open_positions():
                    pending_exits.add(position.position_id)

        for signal in signals_by_entry.get(bar_index, []):
            if max_one_position and broker.get_open_positions():
                rejected += 1
                rejection_reasons["open_position_skip"] += 1
                continue
            instruction = TradeInstruction(
                timestamp=pd.Timestamp(signal.signal_time).to_pydatetime(),
                direction="BUY",
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                atr_at_entry=signal.atr_at_signal,
                signal_score=1.0,
                regime="TRENDING_UP",
                confidence=1.0,
                machine_mode=name,
            )
            risk_order = risk_manager.evaluate(instruction, broker.get_account_state(), list(broker._trade_history))
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
                    "exit_mode": exit_mode,
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
            closed_trades.append(_augment_trade(trade, meta, len(ohlcv) - 1, settings, close_timestamp=final_timestamp))
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


def run_random_controls(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    *,
    lookback: int,
    target_trade_count: int,
    htf_filter: bool,
    exit_mode: str,
    runs: int,
    seed: int,
    initial_equity: float,
) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    for offset in range(runs):
        random_signals = random_matched_signals(
            ohlcv,
            features,
            count=target_trade_count,
            lookback=lookback,
            htf_filter=htf_filter,
            seed=seed + offset,
        )
        result = run_variant(random_signals, ohlcv, features, settings, exit_mode, initial_equity, lookback, f"random_{offset}")
        summaries.append(result_summary(result))
    net_values = [float(item["net_pnl"]) for item in summaries]
    return {
        "runs": runs,
        "target_trade_count": target_trade_count,
        "positive_net_runs": sum(1 for value in net_values if value > 0.0),
        "median_net_pnl": float(np.median(net_values)) if net_values else 0.0,
        "mean_net_pnl": float(np.mean(net_values)) if net_values else 0.0,
        "pct95_net_pnl": float(np.percentile(net_values, 95)) if net_values else 0.0,
        "best_random": max(summaries, key=lambda item: item["net_pnl"], default={}),
        "worst_random": min(summaries, key=lambda item: item["net_pnl"], default={}),
    }


def yearly_decomposition(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    *,
    lookback: int,
    htf_filter: bool,
    exit_mode: str,
    initial_equity: float,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for year in range(int(ohlcv.index.min().year), int(ohlcv.index.max().year) + 1):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = pd.Timestamp(f"{year}-12-31 23:59:59", tz="UTC")
        window = ohlcv.loc[(ohlcv.index >= start) & (ohlcv.index <= end)].copy()
        if len(window) < 500:
            continue
        window_features = features.loc[window.index].copy()
        signals = donchian_signals(window, window_features, lookback=lookback, htf_filter=htf_filter)
        result = run_variant(signals, window, window_features, settings, exit_mode, initial_equity, lookback, f"donchian_{year}")
        output[str(year)] = result_summary(result)
    return output


def run_cost_stress(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    *,
    lookback: int,
    htf_filter: bool,
    exit_mode: str,
    initial_equity: float,
) -> dict[str, Any]:
    signals = donchian_signals(ohlcv, features, lookback=lookback, htf_filter=htf_filter)
    base = run_variant(signals, ohlcv, features, settings, exit_mode, initial_equity, lookback, "donchian_cost_base")
    output: dict[str, Any] = {"base": result_summary(base), "trade_count_locked": True, "trade_count_mismatches": {}}
    for multiplier in (2.0, 3.0):
        stressed = settings_with_exit(settings, "ATR_TRAIL" if exit_mode in {"ATR_TRAIL", "ATR_OR_DONCHIAN"} else "FIXED")
        stressed.setdefault("execution", {})
        stressed.setdefault("risk", {})
        stressed["execution"]["paper_spread_pips"] = float(settings.get("execution", {}).get("paper_spread_pips", 1.5)) * multiplier
        stressed["execution"]["slippage_std_pips"] = float(settings.get("execution", {}).get("slippage_std_pips", 0.5)) * multiplier
        stressed["risk"]["max_spread_pips"] = max(
            float(stressed["execution"]["paper_spread_pips"]) + 0.1,
            float(stressed["risk"].get("max_spread_pips", 3.0)),
        )
        result = run_variant(signals, ohlcv, features, stressed, exit_mode, initial_equity, lookback, f"donchian_cost_{multiplier:g}x")
        if result.total_trades != base.total_trades:
            output["trade_count_locked"] = False
            output["trade_count_mismatches"][f"{multiplier:g}x"] = {"base": base.total_trades, "stressed": result.total_trades}
        output[f"{multiplier:g}x"] = result_summary(result)
    return output


def run_variant(
    signals: list[DonchianSignal],
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    exit_mode: str,
    initial_equity: float,
    lookback: int,
    name: str,
) -> BacktestResult:
    if exit_mode == "FIXED":
        run_settings = settings_with_exit(settings, "FIXED")
        run_signals = signals
    elif exit_mode == "ATR_TRAIL":
        run_settings = settings_with_exit(settings, "ATR_TRAIL")
        run_signals = signals_without_take_profit(signals)
    elif exit_mode == "DONCHIAN_LOW":
        run_settings = settings_with_exit(settings, "FIXED")
        run_signals = signals_without_take_profit(signals)
    else:
        run_settings = settings_with_exit(settings, "ATR_TRAIL")
        run_signals = signals_without_take_profit(signals)
    return run_donchian_backtest(
        name,
        ohlcv,
        features,
        run_signals,
        run_settings,
        exit_mode=exit_mode,
        initial_equity=initial_equity,
        max_one_position=True,
        lookback=lookback,
    )


def settings_with_exit(settings: dict[str, Any], exit_mode: str) -> dict[str, Any]:
    result = json.loads(json.dumps(settings))
    result.setdefault("broker", {})
    result["broker"]["paper_trade"] = True
    result.setdefault("backtesting", {})
    result["backtesting"]["exit_mode"] = exit_mode
    result.setdefault("risk", {})
    result["risk"]["max_spread_pips"] = max(float(result["risk"].get("max_spread_pips", 3.0)), 10.0)
    return result


def feature_candle(timestamp: Any, row: pd.Series, feature_row: pd.Series) -> CandleRow:
    return CandleRow(
        timestamp=pd.Timestamp(timestamp).to_pydatetime(),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        atr_14=max(1e-9, float(feature_row.get("atr_14", row["high"] - row["low"]))),
        adx_14=0.0,
        ema_9=float(feature_row.get("ema_9", row["close"])),
        ema_20=float(feature_row.get("ema_20", row["close"])),
        session_london=1,
        session_ny=0,
        session_overlap=0,
    )


def result_summary(result: BacktestResult) -> dict[str, Any]:
    pnl = [float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))) for trade in result.trades]
    wins = [value for value in pnl if value > 0.0]
    losses = [value for value in pnl if value <= 0.0]
    return {
        "mode": result.mode,
        "trade_count": result.total_trades,
        "net_pnl": result.total_net_pnl,
        "gross_pnl": result.total_gross_pnl,
        "profit_factor": result.profit_factor if result.total_trades else 0.0,
        "win_rate": result.win_rate,
        "sharpe": result.sharpe_ratio,
        "sortino": result.sortino_ratio,
        "max_drawdown": result.max_drawdown_pct,
        "spread_cost": result.total_spread_cost,
        "slippage_cost": result.total_slippage_cost,
        "avg_win": float(np.mean(wins)) if wins else 0.0,
        "avg_loss": float(np.mean(losses)) if losses else 0.0,
        "largest_win": max(wins, default=0.0),
        "largest_loss": min(losses, default=0.0),
        "max_consecutive_losses": max_consecutive_losses(pnl),
        "capital_weighted_r": capital_weighted_r(result.trades),
        "exit_reasons": dict(Counter(str(trade.get("reason", "unknown")) for trade in result.trades)),
    }


def max_consecutive_losses(values: list[float]) -> int:
    longest = 0
    current = 0
    for value in values:
        if value <= 0.0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def capital_weighted_r(trades: list[dict[str, Any]]) -> float:
    risk = sum(float(trade.get("risk_amount", 0.0)) for trade in trades)
    net = sum(float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))) for trade in trades)
    return net / risk if risk > 0.0 else 0.0


def verdict(summary: dict[str, Any]) -> dict[str, Any]:
    best = summary["best_variant"]["summary"]
    random_dist = summary["random_control_distribution"]
    yearly = summary["yearly"]
    cost = summary["cost_stress"]
    positive_years = sum(1 for item in yearly.values() if float(item.get("net_pnl", 0.0)) > 0.0)
    year_2022 = yearly.get("2022", {})
    base_net = float(cost.get("base", {}).get("net_pnl", 0.0))
    stressed_3x = float(cost.get("3x", {}).get("net_pnl", 0.0))
    degradation = (base_net - stressed_3x) / abs(base_net) if base_net else math.inf
    gates = {
        "beats_random_95th_pct_net_pnl": float(best["net_pnl"]) > float(random_dist.get("pct95_net_pnl", math.inf)),
        "sharpe_above_0_70": float(best["sharpe"]) > 0.70,
        "profit_factor_above_1_20": float(best["profit_factor"]) > 1.20,
        "positive_in_5_plus_years": positive_years >= 5,
        "period_2022_pf_above_1_00": float(year_2022.get("profit_factor", 0.0)) > 1.00,
        "cost_stress_3x_degrades_under_20pct": degradation < 0.20 and bool(cost.get("trade_count_locked", False)),
    }
    passed = sum(1 for value in gates.values() if value)
    return {
        "status": "validated_research_lead_not_paper_ready" if passed == len(gates) else "not_validated",
        "passed_gates": passed,
        "total_gates": len(gates),
        "gates": gates,
        "positive_years": positive_years,
        "cost_3x_degradation": degradation,
        "paper_readiness": "failed",
        "live_readiness": "failed",
    }


def write_variant_csv(path: Path, summary: dict[str, Any]) -> None:
    rows = []
    for group_name in ("exit_comparison_raw", "exit_comparison_htf"):
        for name, result in summary[group_name].items():
            rows.append({"group": group_name, "strategy": name, **{k: v for k, v in result.items() if not isinstance(v, dict)}})
    rows.append({"group": "best_variant", "strategy": summary["best_variant"]["name"], **{k: v for k, v in summary["best_variant"]["summary"].items() if not isinstance(v, dict)}})
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def print_summary(summary: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    print("AURUM-1 Donchian Research")
    print("=" * 76)
    print(f"Rows:       {summary['rows']}")
    print(f"Date range: {summary['date_range']['start']} -> {summary['date_range']['end']}")
    print("-" * 76)
    print(f"{'variant':<34}{'trades':>8}{'net':>12}{'PF':>8}{'Sharpe':>9}{'maxDD':>9}{'maxL':>7}")
    for group_name in ("exit_comparison_raw", "exit_comparison_htf"):
        print(group_name + ":")
        for name, result in summary[group_name].items():
            print(
                f"  {name:<32}"
                f"{result['trade_count']:>8}"
                f"{result['net_pnl']:>12.2f}"
                f"{result['profit_factor']:>8.2f}"
                f"{result['sharpe']:>9.2f}"
                f"{result['max_drawdown']:>9.2%}"
                f"{result['max_consecutive_losses']:>7}"
            )
    best = summary["best_variant"]
    print("-" * 76)
    print(f"Best variant: {best['name']} exit={best['exit_mode']} htf_filter={best['htf_filter']}")
    random_dist = summary["random_control_distribution"]
    print(
        f"Random controls: runs={random_dist['runs']} positive={random_dist['positive_net_runs']}/{random_dist['runs']} "
        f"medianNet={random_dist['median_net_pnl']:.2f} p95Net={random_dist['pct95_net_pnl']:.2f}"
    )
    print("Yearly:")
    for year, result in summary["yearly"].items():
        print(f"  {year}: net={result['net_pnl']:.2f} PF={result['profit_factor']:.2f} Sharpe={result['sharpe']:.2f} trades={result['trade_count']}")
    print("Cost stress:")
    for label, result in summary["cost_stress"].items():
        if isinstance(result, dict) and "net_pnl" in result:
            print(f"  {label}: net={result['net_pnl']:.2f} PF={result['profit_factor']:.2f} Sharpe={result['sharpe']:.2f}")
    if not summary["cost_stress"].get("trade_count_locked", True):
        print(f"  WARNING trade-count mismatch: {summary['cost_stress'].get('trade_count_mismatches', {})}")
    verdict_data = summary["verdict"]
    print("-" * 76)
    print(f"Verdict: {verdict_data['status']} ({verdict_data['passed_gates']}/{verdict_data['total_gates']} gates passed)")
    for gate, passed in verdict_data["gates"].items():
        print(f"  {'PASS' if passed else 'FAIL'} {gate}")
    print("Research status: no paper/live approval from this script.")
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")


if __name__ == "__main__":
    raise SystemExit(main())
