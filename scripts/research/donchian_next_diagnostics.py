"""Second-pass diagnostics for the clean Donchian fixed-2R lead.

This script is intentionally diagnostic-only:
- no Donchian lookback optimization
- no SELL logic
- no ML
- no new strategy indicators beyond the requested ADX permission test
- no paper/live broker use
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.backtesting.engine import BacktestResult
from aurum1.data.ingestion import load_ohlcv, load_settings
from scripts.donchian_diagnostics import adx_wilder, group_trade_stats, month_key
from scripts.donchian_research_runner import (
    DonchianSignal,
    donchian_signals,
    result_summary,
    run_donchian_backtest,
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
    run_settings = settings_with_exit(settings, "FIXED")

    base_signals = donchian_signals(ohlcv, features, lookback=args.lookback, htf_filter=False)
    baseline = run_variant(
        base_signals,
        ohlcv,
        features,
        run_settings,
        "FIXED",
        initial_equity,
        args.lookback,
        "donchian_raw_fixed_2r_baseline",
    )

    adx_signals = filter_adx_permission(base_signals, features, threshold=args.adx_threshold)
    adx_result = run_variant(
        adx_signals,
        ohlcv,
        features,
        run_settings,
        "FIXED",
        initial_equity,
        args.lookback,
        "donchian_adx25_fixed_2r",
    )

    early_failure_result = run_donchian_backtest(
        "donchian_fixed_2r_donchian_low_early_failure",
        ohlcv,
        features,
        base_signals,
        run_settings,
        exit_mode="DONCHIAN_LOW",
        initial_equity=initial_equity,
        max_one_position=True,
        lookback=args.lookback,
    )

    diagnostics = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "diagnostic_only": True,
            "lookback": args.lookback,
            "ml_enabled": False,
            "sell_enabled": False,
            "paper_or_live_orders_sent": False,
            "parameter_optimization": False,
            "adx_permission": "latest_closed_signal_bar_adx_to_avoid_entry_bar_lookahead",
        },
        "market_db": str(market_db),
        "rows": len(ohlcv),
        "date_range": {"start": ohlcv.index.min().isoformat(), "end": ohlcv.index.max().isoformat()},
        "baseline": baseline_report(
            baseline,
            ohlcv,
            features,
            settings,
            args.lookback,
            initial_equity,
        ),
        "donchian_adx25_fixed_2r": variant_report(
            adx_result,
            ohlcv,
            features,
            settings,
            args.lookback,
            initial_equity,
            args.random_runs,
            args.random_seed,
            random_permission="adx25_latest_closed",
            exit_model="FIXED",
        ),
        "donchian_low_early_failure_fixed_2r": variant_report(
            early_failure_result,
            ohlcv,
            features,
            settings,
            args.lookback,
            initial_equity,
            args.random_runs,
            args.random_seed,
            random_permission="all_donchian_eligible",
            exit_model="DONCHIAN_LOW_WITH_FIXED_2R_TP",
        ),
    }
    diagnostics["comparison"] = compare_variants(diagnostics)
    diagnostics["classification"] = "research-only"
    diagnostics["paper_readiness"] = "failed"
    diagnostics["live_readiness"] = "failed"

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = args.output_dir / f"donchian_next_diagnostics_{stamp}.json"
    csv_path = args.output_dir / f"donchian_next_diagnostics_{stamp}.csv"
    json_path.write_text(json.dumps(diagnostics, indent=2, sort_keys=True, default=str), encoding="utf-8")
    write_csv(csv_path, diagnostics)
    print_summary(diagnostics, json_path, csv_path)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run second-pass Donchian diagnostics.")
    parser.add_argument("--market-db", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--lookback", type=int, default=20)
    parser.add_argument("--adx-threshold", type=float, default=25.0)
    parser.add_argument("--random-runs", type=int, default=100)
    parser.add_argument("--random-seed", type=int, default=4242)
    return parser.parse_args(argv)


def filter_adx_permission(
    signals: list[DonchianSignal],
    features: pd.DataFrame,
    *,
    threshold: float,
) -> list[DonchianSignal]:
    """Use the latest closed candle's ADX at the next-open entry decision."""
    output: list[DonchianSignal] = []
    adx = features["adx_14"]
    for signal in signals:
        if signal.signal_bar >= len(adx):
            continue
        value = float(adx.iloc[signal.signal_bar])
        if math.isfinite(value) and value > threshold:
            output.append(signal)
    return output


def variant_report(
    result: BacktestResult,
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    lookback: int,
    initial_equity: float,
    random_runs: int,
    random_seed: int,
    *,
    random_permission: str,
    exit_model: str,
) -> dict[str, Any]:
    random_controls = matched_random_controls(
        result,
        ohlcv,
        features,
        settings,
        lookback,
        initial_equity,
        random_runs,
        random_seed,
        random_permission=random_permission,
        exit_model=exit_model,
    )
    yearly = yearly_breakdown(ohlcv, features, settings, lookback, initial_equity, random_permission, exit_model)
    monthly = monthly_breakdown(result)
    summary = result_summary(result)
    return {
        "summary": summary,
        "yearly_breakdown": yearly,
        "result_2021": yearly.get("2021", {}),
        "result_2025": yearly.get("2025", {}),
        "monthly_consistency": monthly,
        "random_controls": random_controls,
        "historical_gates": historical_gates(summary, yearly, monthly, random_controls),
    }


def baseline_report(
    result: BacktestResult,
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    lookback: int,
    initial_equity: float,
) -> dict[str, Any]:
    yearly = yearly_breakdown(ohlcv, features, settings, lookback, initial_equity, "all_donchian_eligible", "FIXED")
    monthly = monthly_breakdown(result)
    summary = result_summary(result)
    return {
        "summary": summary,
        "yearly_breakdown": yearly,
        "result_2021": yearly.get("2021", {}),
        "result_2025": yearly.get("2025", {}),
        "monthly_consistency": monthly,
        "random_controls": {
            "runs": 0,
            "target_trade_count": 0,
            "median_net_pnl": None,
            "mean_net_pnl": None,
            "pct95_net_pnl": None,
            "variant_percentile": None,
            "positive_net_runs": 0,
        },
        "historical_gates": historical_gates(
            summary,
            yearly,
            monthly,
            {
                "pct95_net_pnl": math.inf,
                "variant_percentile": 0.0,
            },
        ),
    }


def matched_random_controls(
    variant: BacktestResult,
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    lookback: int,
    initial_equity: float,
    runs: int,
    seed: int,
    *,
    random_permission: str,
    exit_model: str,
) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    target_count = max(1, variant.total_trades)
    for offset in range(runs):
        signals = random_signals_with_permission(
            ohlcv,
            features,
            count=target_count,
            lookback=lookback,
            seed=seed + offset,
            permission=random_permission,
        )
        result = run_exit_model(
            signals,
            ohlcv,
            features,
            settings,
            lookback,
            initial_equity,
            exit_model,
            f"random_{random_permission}_{offset}",
        )
        summaries.append(result_summary(result))
    net_values = [float(item["net_pnl"]) for item in summaries]
    variant_net = float(variant.total_net_pnl)
    below = sum(1 for value in net_values if value < variant_net)
    return {
        "runs": runs,
        "target_trade_count": target_count,
        "median_net_pnl": float(np.median(net_values)) if net_values else 0.0,
        "mean_net_pnl": float(np.mean(net_values)) if net_values else 0.0,
        "pct95_net_pnl": float(np.percentile(net_values, 95)) if net_values else 0.0,
        "variant_percentile": below / len(net_values) if net_values else 0.0,
        "positive_net_runs": sum(1 for value in net_values if value > 0.0),
        "best_random": max(summaries, key=lambda item: item["net_pnl"], default={}),
        "worst_random": min(summaries, key=lambda item: item["net_pnl"], default={}),
    }


def random_signals_with_permission(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    *,
    count: int,
    lookback: int,
    seed: int,
    permission: str,
) -> list[DonchianSignal]:
    eligible = features["atr_14"].notna() & features["high"].rolling(lookback, min_periods=lookback).max().notna()
    if permission == "adx25_latest_closed":
        eligible &= features["adx_14"].astype(float) > 25.0
    positions = [int(ohlcv.index.get_loc(ts)) for ts in features.index[eligible.fillna(False)]]
    positions = [pos for pos in positions if pos + 1 < len(ohlcv)]
    rng = random.Random(seed)
    chosen = set(rng.sample(positions, min(count, len(positions))))
    mask = pd.Series(False, index=features.index)
    if chosen:
        mask.iloc[list(chosen)] = True
    return donchian_signals(ohlcv, features, lookback=lookback, htf_filter=False, seed_mask=mask)


def run_exit_model(
    signals: list[DonchianSignal],
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    lookback: int,
    initial_equity: float,
    exit_model: str,
    name: str,
) -> BacktestResult:
    run_settings = settings_with_exit(settings, "FIXED")
    if exit_model == "DONCHIAN_LOW_WITH_FIXED_2R_TP":
        return run_donchian_backtest(
            name,
            ohlcv,
            features,
            signals,
            run_settings,
            exit_mode="DONCHIAN_LOW",
            initial_equity=initial_equity,
            max_one_position=True,
            lookback=lookback,
        )
    return run_variant(signals, ohlcv, features, run_settings, "FIXED", initial_equity, lookback, name)


def yearly_breakdown(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    lookback: int,
    initial_equity: float,
    permission: str,
    exit_model: str,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for year in range(int(ohlcv.index.min().year), int(ohlcv.index.max().year) + 1):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = pd.Timestamp(f"{year}-12-31 23:59:59", tz="UTC")
        window = ohlcv.loc[(ohlcv.index >= start) & (ohlcv.index <= end)].copy()
        if len(window) < 500:
            continue
        window_features = features.loc[window.index].copy()
        signals = donchian_signals(window, window_features, lookback=lookback, htf_filter=False)
        if permission == "adx25_latest_closed":
            signals = filter_adx_permission(signals, window_features, threshold=25.0)
        result = run_exit_model(
            signals,
            window,
            window_features,
            settings,
            lookback,
            initial_equity,
            exit_model,
            f"donchian_{permission}_{exit_model}_{year}",
        )
        output[str(year)] = result_summary(result)
    return output


def monthly_breakdown(result: BacktestResult) -> dict[str, Any]:
    monthly = group_trade_stats(result.trades, lambda trade: month_key(trade))
    values = [float(stats["net_pnl"]) for _, stats in sorted(monthly.items())]
    return {
        "positive_months": sum(1 for value in values if value > 0.0),
        "total_months": len(values),
        "positive_month_ratio": sum(1 for value in values if value > 0.0) / len(values) if values else 0.0,
        "months_pf_above_1": sum(1 for stats in monthly.values() if float(stats["profit_factor"]) > 1.0),
        "monthly": monthly,
    }


def historical_gates(
    summary: dict[str, Any],
    yearly: dict[str, dict[str, Any]],
    monthly: dict[str, Any],
    random_controls: dict[str, Any],
) -> dict[str, Any]:
    positive_years = sum(1 for stats in yearly.values() if float(stats.get("net_pnl", 0.0)) > 0.0)
    gates = {
        "pf_toward_1_18": float(summary["profit_factor"]) >= 1.18,
        "pf_above_1_20": float(summary["profit_factor"]) >= 1.20,
        "sharpe_at_or_above_0_85": float(summary["sharpe"]) >= 0.85,
        "beats_random_95th_percentile": float(summary["net_pnl"]) > float(random_controls["pct95_net_pnl"]),
        "positive_in_6_plus_years": positive_years >= 6,
        "monthly_positive_ratio_above_50pct": float(monthly["positive_month_ratio"]) > 0.50,
        "trade_count_meaningful": int(summary["trade_count"]) >= 300,
    }
    return {
        "passed": sum(1 for value in gates.values() if value),
        "total": len(gates),
        "positive_years": positive_years,
        "gates": gates,
    }


def compare_variants(diagnostics: dict[str, Any]) -> dict[str, Any]:
    base = diagnostics["baseline"]["summary"]
    rows = {}
    for key in ("donchian_adx25_fixed_2r", "donchian_low_early_failure_fixed_2r"):
        item = diagnostics[key]
        summary = item["summary"]
        yearly = item["yearly_breakdown"]
        rows[key] = {
            "net_pnl_delta_vs_baseline": float(summary["net_pnl"]) - float(base["net_pnl"]),
            "pf_delta_vs_baseline": float(summary["profit_factor"]) - float(base["profit_factor"]),
            "sharpe_delta_vs_baseline": float(summary["sharpe"]) - float(base["sharpe"]),
            "max_dd_delta_vs_baseline": float(summary["max_drawdown"]) - float(base["max_drawdown"]),
            "trade_count_delta_vs_baseline": int(summary["trade_count"]) - int(base["trade_count"]),
            "2021_delta_vs_baseline": float(yearly.get("2021", {}).get("net_pnl", 0.0))
            - float(diagnostics["baseline"]["yearly_breakdown"].get("2021", {}).get("net_pnl", 0.0)),
            "2025_delta_vs_baseline": float(yearly.get("2025", {}).get("net_pnl", 0.0))
            - float(diagnostics["baseline"]["yearly_breakdown"].get("2025", {}).get("net_pnl", 0.0)),
        }
    return rows


def write_csv(path: Path, diagnostics: dict[str, Any]) -> None:
    rows = []
    for key in ("baseline", "donchian_adx25_fixed_2r", "donchian_low_early_failure_fixed_2r"):
        item = diagnostics[key]
        summary = item["summary"]
        random_controls = item["random_controls"]
        gates = item["historical_gates"]
        rows.append(
            {
                "variant": key,
                "net_pnl": summary["net_pnl"],
                "profit_factor": summary["profit_factor"],
                "sharpe": summary["sharpe"],
                "max_drawdown": summary["max_drawdown"],
                "trade_count": summary["trade_count"],
                "win_rate": summary["win_rate"],
                "year_2021_net": item["result_2021"].get("net_pnl"),
                "year_2025_net": item["result_2025"].get("net_pnl"),
                "monthly_positive_ratio": item["monthly_consistency"]["positive_month_ratio"],
                "random_p95_net": random_controls["pct95_net_pnl"],
                "random_percentile": random_controls["variant_percentile"],
                "historical_gates_passed": gates["passed"],
                "historical_gates_total": gates["total"],
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def print_summary(diagnostics: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    print("AURUM-1 Donchian Next Diagnostics")
    print("=" * 88)
    print(f"Rows:       {diagnostics['rows']}")
    print(f"Date range: {diagnostics['date_range']['start']} -> {diagnostics['date_range']['end']}")
    print("Scope: diagnostic-only; no ML, no SELL, no paper/live orders, no Donchian optimization")
    print("ADX permission uses latest closed signal-bar ADX to avoid entry-bar lookahead.")
    print("-" * 88)
    print(
        f"{'variant':<42}{'trades':>8}{'net':>12}{'PF':>8}{'Sharpe':>9}"
        f"{'maxDD':>9}{'2021':>11}{'2025':>11}{'rand%':>8}"
    )
    for key in ("baseline", "donchian_adx25_fixed_2r", "donchian_low_early_failure_fixed_2r"):
        item = diagnostics[key]
        summary = item["summary"]
        random_controls = item["random_controls"]
        rand_pct = random_controls["variant_percentile"]
        rand_label = f"{rand_pct:>8.1%}" if rand_pct is not None else f"{'n/a':>8}"
        print(
            f"{key:<42}"
            f"{summary['trade_count']:>8}"
            f"{summary['net_pnl']:>12.2f}"
            f"{summary['profit_factor']:>8.2f}"
            f"{summary['sharpe']:>9.2f}"
            f"{summary['max_drawdown']:>9.2%}"
            f"{float(item['result_2021'].get('net_pnl', 0.0)):>11.2f}"
            f"{float(item['result_2025'].get('net_pnl', 0.0)):>11.2f}"
            f"{rand_label}"
        )
        monthly = item["monthly_consistency"]
        gates = item["historical_gates"]
        random_p95 = random_controls["pct95_net_pnl"]
        random_p95_label = f"{random_p95:.2f}" if random_p95 is not None else "n/a"
        print(
            f"  monthly positive={monthly['positive_months']}/{monthly['total_months']} "
            f"random_p95={random_p95_label} "
            f"gates={gates['passed']}/{gates['total']}"
        )
        print("  yearly:")
        for year, stats in item["yearly_breakdown"].items():
            print(
                f"    {year}: net={stats['net_pnl']:.2f} PF={stats['profit_factor']:.2f} "
                f"Sharpe={stats['sharpe']:.2f} trades={stats['trade_count']}"
            )
    print("-" * 88)
    print("Comparison vs raw Donchian fixed 2R baseline:")
    for key, row in diagnostics["comparison"].items():
        print(
            f"  {key}: net_delta={row['net_pnl_delta_vs_baseline']:.2f} "
            f"PF_delta={row['pf_delta_vs_baseline']:.3f} "
            f"Sharpe_delta={row['sharpe_delta_vs_baseline']:.3f} "
            f"DD_delta={row['max_dd_delta_vs_baseline']:.2%} "
            f"2021_delta={row['2021_delta_vs_baseline']:.2f} "
            f"2025_delta={row['2025_delta_vs_baseline']:.2f}"
        )
    print("Final classification: research-only")
    print("Paper readiness: failed")
    print("Live readiness: failed")
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")


if __name__ == "__main__":
    raise SystemExit(main())
