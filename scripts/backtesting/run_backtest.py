"""Run AURUM-1 Phase 7 walk-forward, Monte Carlo, and ablation backtests."""

from __future__ import annotations

import copy
import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.backtesting import (
    RULE_REGIME_BUY_NEXT_OPEN,
    BacktestEngine,
    WalkForwardValidator,
    rule_regime_buy_next_open_settings,
    run_ablation_backtest,
    run_monte_carlo,
)
from aurum1.backtesting.report import plot_equity_curve, print_backtest_report, save_backtest_report
from aurum1.data.ingestion import (
    DEFAULT_OANDA_BACKTEST_COUNT,
    MACRO_NUMERIC_COLUMNS,
    AurumDataIngestor,
    load_cot,
    load_macro,
    load_ohlcv,
    load_settings,
)
from aurum1.features.engineer import FeatureEngineer
from aurum1.models.regime_classifier import REGIME_LABELS, RegimeClassifier
from aurum1.signals import MachineMode

MIN_BACKTEST_HISTORY_BARS = 20000
MIN_BACKTEST_HISTORY_DAYS = 250.0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args(argv)
    load_dotenv(ROOT / ".env")

    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    settings = configure_backtest_isolation(settings)
    ohlcv, data_label = load_backtest_ohlcv(
        settings,
        allow_proxy=args.allow_gold_futures_proxy,
        allow_synthetic=args.allow_synthetic,
    )
    try:
        history_status = validate_backtest_history(
            ohlcv,
            settings,
            allow_short_history=args.allow_short_history,
            min_bars=args.min_history_bars,
            min_days=args.min_history_days,
        )
    except RuntimeError as exc:
        print("Quantitative readiness: not verified")
        print(f"Reason: {exc}")
        return 2

    macro, macro_status = load_backtest_macro(settings, ohlcv, allow_placeholder=args.allow_placeholder_data)
    cot, cot_status = load_backtest_cot(settings, ohlcv, allow_placeholder=args.allow_placeholder_data)
    settings = tune_backtest_windows(settings, len(ohlcv))

    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    print_header(settings, ohlcv, data_label, macro_status, cot_status, history_status)
    print_signal_audit(ohlcv, macro, cot, settings)

    walk_forward = WalkForwardValidator(settings).run(
        ohlcv,
        macro,
        cot,
        mode=MachineMode.RULE_REGIME,
        initial_equity=float(settings.get("broker", {}).get("paper_initial_equity", 10000.0)),
    )
    variant_settings = rule_regime_buy_next_open_settings(settings)
    variant_walk_forward = WalkForwardValidator(variant_settings).run(
        ohlcv,
        macro,
        cot,
        mode=MachineMode.RULE_REGIME,
        initial_equity=float(settings.get("broker", {}).get("paper_initial_equity", 10000.0)),
    )
    combined_trades = [trade for window in walk_forward.windows for trade in window.trades]
    variant_combined_trades = [trade for window in variant_walk_forward.windows for trade in window.trades]
    monte_carlo = run_monte_carlo(
        combined_trades,
        n_simulations=int(settings.get("backtesting", {}).get("n_monte_carlo", 1000)),
        initial_equity=float(settings.get("broker", {}).get("paper_initial_equity", 10000.0)),
    )
    variant_monte_carlo = run_monte_carlo(
        variant_combined_trades,
        n_simulations=int(settings.get("backtesting", {}).get("n_monte_carlo", 1000)),
        initial_equity=float(settings.get("broker", {}).get("paper_initial_equity", 10000.0)),
    )

    ablation = run_ablation_backtest(
        ohlcv,
        macro,
        cot,
        settings,
        initial_equity=float(settings.get("broker", {}).get("paper_initial_equity", 10000.0)),
    )

    for mode_name, result in ablation.items():
        print_backtest_report(result)
        save_backtest_report(result, reports_dir / f"backtest_{mode_name}.json")
        plot_equity_curve(result, reports_dir / f"equity_{mode_name}.png")
    print_mode_diagnostics(ablation)
    print_research_variant_comparison(ablation, walk_forward, variant_walk_forward)

    best_mode, best_result = max(ablation.items(), key=lambda item: item[1].sharpe_ratio)
    variant_result = ablation.get(RULE_REGIME_BUY_NEXT_OPEN)
    recommendation = "strategy research required; no paper trading approval"
    if history_status["short_history"]:
        recommendation = "short-history plumbing run; quantitative readiness not verified"
    elif (
        variant_result is not None
        and variant_result.total_net_pnl > 0.0
        and variant_walk_forward.promotion_gate_passed
        and variant_monte_carlo.ruin_probability < 0.05
    ):
        recommendation = "research candidate only; benchmark and cost-stress validation still required before paper review"

    print("\nWalk-forward summary (baseline rule_regime):")
    print(f"  Windows: {len(walk_forward.windows)}")
    print(f"  Mean Sharpe: {walk_forward.mean_sharpe:.2f}")
    print(f"  Mean Profit Factor: {walk_forward.mean_profit_factor:.2f}")
    print(f"  Mean Win Rate: {walk_forward.mean_win_rate:.2%}")
    print(f"  Mean Max Drawdown: {walk_forward.mean_max_drawdown:.2%}")
    print_promotion_gate(walk_forward)
    print_walk_forward_detail(walk_forward.windows)
    print("\nWalk-forward summary (variant rule_regime_buy_next_open):")
    print(f"  Windows: {len(variant_walk_forward.windows)}")
    print(f"  Mean Sharpe: {variant_walk_forward.mean_sharpe:.2f}")
    print(f"  Mean Profit Factor: {variant_walk_forward.mean_profit_factor:.2f}")
    print(f"  Mean Win Rate: {variant_walk_forward.mean_win_rate:.2%}")
    print(f"  Mean Max Drawdown: {variant_walk_forward.mean_max_drawdown:.2%}")
    print_promotion_gate(variant_walk_forward)
    print_walk_forward_detail(variant_walk_forward.windows)
    print("\nMonte Carlo summary (baseline rule_regime walk-forward trades):")
    print(f"  Median final equity: {monte_carlo.median_final_equity:.2f}")
    print(f"  5th percentile final equity: {monte_carlo.pct5_final_equity:.2f}")
    print(f"  95th percentile drawdown: {monte_carlo.pct95_max_drawdown:.2%}")
    print(f"  Ruin probability: {monte_carlo.ruin_probability:.2%}")
    print("\nMonte Carlo summary (variant rule_regime_buy_next_open walk-forward trades):")
    print(f"  Median final equity: {variant_monte_carlo.median_final_equity:.2f}")
    print(f"  5th percentile final equity: {variant_monte_carlo.pct5_final_equity:.2f}")
    print(f"  95th percentile drawdown: {variant_monte_carlo.pct95_max_drawdown:.2%}")
    print(f"  Ruin probability: {variant_monte_carlo.ruin_probability:.2%}")
    print("\nFinal summary:")
    print(f"  Best mode by Sharpe: {best_mode} ({best_result.sharpe_ratio:.2f})")
    print(
        "  Baseline promotion gate: "
        f"{'PASSED' if walk_forward.promotion_gate_passed else 'FAILED'} "
        f"({walk_forward.criteria_passed}/6 criteria met)"
    )
    print(
        "  Variant promotion gate: "
        f"{'PASSED' if variant_walk_forward.promotion_gate_passed else 'FAILED'} "
        f"({variant_walk_forward.criteria_passed}/6 criteria met)"
    )
    print(f"  Baseline Monte Carlo ruin probability: {monte_carlo.ruin_probability:.2%}")
    print(f"  Variant Monte Carlo ruin probability: {variant_monte_carlo.ruin_probability:.2%}")
    print("  Paper readiness: FAILED")
    print("  Live readiness: FAILED")
    print(f"  Recommendation: {recommendation}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AURUM-1 real-market backtest.")
    parser.add_argument(
        "--allow-gold-futures-proxy",
        action="store_true",
        help="Allow GC=F futures proxy when real OANDA XAU_USD is unavailable.",
    )
    parser.add_argument(
        "--allow-placeholder-data",
        action="store_true",
        help="Allow synthetic macro/COT placeholders for plumbing checks.",
    )
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Allow synthetic OHLCV fallback for plumbing checks only.",
    )
    parser.add_argument(
        "--allow-short-history",
        action="store_true",
        help="Allow a short-history plumbing run; quantitative readiness remains not verified.",
    )
    parser.add_argument(
        "--min-history-bars",
        type=int,
        default=None,
        help="Minimum M15 bars required for quantitative readiness.",
    )
    parser.add_argument(
        "--min-history-days",
        type=float,
        default=None,
        help="Minimum calendar-day span required for quantitative readiness.",
    )
    return parser.parse_args(argv)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def configure_backtest_isolation(settings: dict[str, Any]) -> dict[str, Any]:
    isolated = copy.deepcopy(settings)
    runtime_db = str(isolated.get("data", {}).get("db_path", "aurum1/data/aurum1.sqlite3"))
    isolated.setdefault("backtesting", {})
    isolated.setdefault("data", {})
    isolated.setdefault("execution", {})
    market_db = str(isolated["backtesting"].get("market_data_db_path", "aurum1/data/backtest_market_cache.sqlite3"))
    execution_db = isolated["backtesting"].get("execution_db_path")
    if not execution_db:
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        execution_db = str(Path("reports") / f"backtest_execution_{stamp}.sqlite3")
        isolated["backtesting"]["execution_db_path"] = execution_db
    isolated["backtesting"]["runtime_db_path"] = runtime_db
    isolated["data"]["db_path"] = market_db
    isolated["execution"]["db_path"] = str(execution_db)
    return isolated


def validate_backtest_history(
    ohlcv: pd.DataFrame,
    settings: dict[str, Any],
    *,
    allow_short_history: bool = False,
    min_bars: int | None = None,
    min_days: float | None = None,
) -> dict[str, Any]:
    backtesting = settings.get("backtesting", {})
    required_bars = int(min_bars or backtesting.get("min_history_bars", MIN_BACKTEST_HISTORY_BARS))
    required_days = float(min_days or backtesting.get("min_history_days", MIN_BACKTEST_HISTORY_DAYS))
    bars = int(len(ohlcv))
    if bars == 0:
        span_days = 0.0
    else:
        index = pd.DatetimeIndex(ohlcv.index)
        start = pd.Timestamp(index.min())
        end = pd.Timestamp(index.max())
        span_days = max(0.0, float((end - start).total_seconds() / 86400.0))
    short_reasons: list[str] = []
    if bars < required_bars:
        short_reasons.append(f"{bars} bars < required {required_bars}")
    if span_days < required_days:
        short_reasons.append(f"{span_days:.1f} days < required {required_days:.1f}")
    if short_reasons and not allow_short_history:
        raise RuntimeError(
            "Insufficient backtest history for quantitative readiness: "
            + "; ".join(short_reasons)
            + ". Rebuild the real OANDA cache or rerun with --allow-short-history for plumbing only."
        )
    return {
        "bars": bars,
        "span_days": span_days,
        "min_bars": required_bars,
        "min_days": required_days,
        "short_history": bool(short_reasons),
        "short_reasons": short_reasons,
        "quantitative_readiness": "not_verified" if short_reasons else "eligible",
    }


def print_promotion_gate(walk_forward: Any) -> None:
    status = "PASSED" if walk_forward.promotion_gate_passed else "FAILED"
    detail = walk_forward.criteria_detail
    branch = "\u251c\u2500\u2500"
    last = "\u2514\u2500\u2500"
    print(f"  Promotion gate: {status} ({walk_forward.criteria_passed}/6 criteria met)")
    print(f"  {branch} mean_sharpe > 0.50:                  {walk_forward.mean_sharpe:>5.2f}  {_mark(detail['mean_sharpe'])}")
    print(f"  {branch} mean_profit_factor > 1.30:           {walk_forward.mean_profit_factor:>5.2f}  {_mark(detail['mean_profit_factor'])}")
    print(f"  {branch} mean_win_rate > 0.50:                {walk_forward.mean_win_rate:>5.1%}  {_mark(detail['mean_win_rate'])}")
    print(f"  {branch} mean_max_drawdown < 5%:              {walk_forward.mean_max_drawdown:>5.2%}  {_mark(detail['mean_max_drawdown'])}")
    print(
        f"  {branch} worst_window_max_drawdown < 10%:    "
        f"{walk_forward.worst_window_max_drawdown:>5.2%}  {_mark(detail['worst_window_max_drawdown'])}"
    )
    print(f"  {last} positive_window_rate > 80%:          {walk_forward.positive_window_rate:>5.0%}  {_mark(detail['positive_window_rate'])}")


def _mark(passed: bool) -> str:
    return "\u2713" if passed else "\u2717"


def print_walk_forward_detail(windows: list[Any]) -> None:
    positive = sum(1 for window in windows if window.sharpe_ratio > 0.0)
    negative = sum(1 for window in windows if window.sharpe_ratio < 0.0)
    print("\nWalk-forward per-window detail:")
    for index, window in enumerate(windows, start=1):
        print(
            f"  Window {index:02d}: "
            f"Sharpe={window.sharpe_ratio:.2f}  "
            f"Trades={window.total_trades}  "
            f"Return={window.total_return_pct:.2%}  "
            f"MaxDD={window.max_drawdown_pct:.2%}"
        )
    print(f"\n  Positive Sharpe windows: {positive}/{len(windows)}")
    print(f"  Negative Sharpe windows: {negative}/{len(windows)}")


def print_research_variant_comparison(
    ablation: dict[str, Any],
    baseline_walk_forward: Any,
    variant_walk_forward: Any,
) -> None:
    baseline = ablation.get(MachineMode.RULE_REGIME.value)
    variant = ablation.get(RULE_REGIME_BUY_NEXT_OPEN)
    if baseline is None or variant is None:
        return

    baseline_positive = sum(1 for window in baseline_walk_forward.windows if window.sharpe_ratio > 0.0)
    variant_positive = sum(1 for window in variant_walk_forward.windows if window.sharpe_ratio > 0.0)
    print("\nResearch variant comparison:")
    print("  Baseline: rule_regime")
    print(f"  Variant:  {RULE_REGIME_BUY_NEXT_OPEN}")
    print(
        "  "
        f"{'metric':<28}"
        f"{'rule_regime':>18}"
        f"{RULE_REGIME_BUY_NEXT_OPEN:>32}"
    )
    print("  " + "-" * 78)
    rows = [
        ("trade count", f"{baseline.total_trades}", f"{variant.total_trades}"),
        ("win rate", f"{baseline.win_rate:.2%}", f"{variant.win_rate:.2%}"),
        ("profit factor", f"{baseline.profit_factor:.2f}", f"{variant.profit_factor:.2f}"),
        ("expectancy R", f"{_expectancy_r(baseline.trades):.3f}", f"{_expectancy_r(variant.trades):.3f}"),
        ("average R", f"{_average_r(baseline.trades):.3f}", f"{_average_r(variant.trades):.3f}"),
        ("median R", f"{_median_r(baseline.trades):.3f}", f"{_median_r(variant.trades):.3f}"),
        ("capital-weighted R", f"{_capital_weighted_r(baseline.trades):.3f}", f"{_capital_weighted_r(variant.trades):.3f}"),
        ("max drawdown", f"{baseline.max_drawdown_pct:.2%}", f"{variant.max_drawdown_pct:.2%}"),
        ("Sharpe", f"{baseline.sharpe_ratio:.2f}", f"{variant.sharpe_ratio:.2f}"),
        ("Sortino", f"{baseline.sortino_ratio:.2f}", f"{variant.sortino_ratio:.2f}"),
        ("total spread cost", f"${baseline.total_spread_cost:.2f}", f"${variant.total_spread_cost:.2f}"),
        ("total slippage cost", f"${baseline.total_slippage_cost:.2f}", f"${variant.total_slippage_cost:.2f}"),
        ("avg holding bars", f"{baseline.avg_trade_duration_bars}", f"{variant.avg_trade_duration_bars}"),
        (
            "WF positive windows",
            f"{baseline_positive}/{len(baseline_walk_forward.windows)}",
            f"{variant_positive}/{len(variant_walk_forward.windows)}",
        ),
    ]
    for label, baseline_value, variant_value in rows:
        print(f"  {label:<28}{baseline_value:>18}{variant_value:>32}")
    print("  exit reason distribution:")
    print(f"    rule_regime:                  {_exit_reason_distribution(baseline.trades)}")
    print(f"    {RULE_REGIME_BUY_NEXT_OPEN}:   {_exit_reason_distribution(variant.trades)}")
    print("  fill type distribution:")
    print(f"    rule_regime:                  {_fill_type_distribution(baseline.trades)}")
    print(f"    {RULE_REGIME_BUY_NEXT_OPEN}:   {_fill_type_distribution(variant.trades)}")


def print_mode_diagnostics(ablation: dict[str, Any]) -> None:
    baseline = ablation.get(MachineMode.RULE_ONLY.value)
    if baseline is None:
        return
    baseline_entries = _trade_entry_keys(baseline.trades)
    baseline_exits = _trade_exit_keys(baseline.trades)
    baseline_equity = np.asarray(baseline.equity_curve, dtype=float)
    print("\nMode diagnostics vs rule_only:")
    for mode_name, result in ablation.items():
        if mode_name == MachineMode.RULE_ONLY.value:
            continue
        entries = _trade_entry_keys(result.trades)
        exits = _trade_exit_keys(result.trades)
        equity = np.asarray(result.equity_curve, dtype=float)
        equity_diff = 0.0
        if len(equity) == len(baseline_equity) and len(equity):
            equity_diff = float(np.max(np.abs(equity - baseline_equity)))
        pnl_diff = float(result.final_equity - baseline.final_equity)
        entries_changed = len(entries.symmetric_difference(baseline_entries))
        exits_changed = len(exits.symmetric_difference(baseline_exits))
        trades_changed = abs(result.total_trades - baseline.total_trades) + entries_changed
        print(
            f"  {mode_name}: trades_changed={trades_changed} "
            f"entries_changed={entries_changed} exits_changed={exits_changed} "
            f"pnl_diff={pnl_diff:.2f} equity_curve_max_diff={equity_diff:.2f}"
        )
        if trades_changed == 0 and exits_changed == 0 and abs(pnl_diff) < 1e-9 and equity_diff < 1e-9:
            print(f"    ML/sentiment incremental value: not evidenced; trade list identical to rule_only.")


def _trade_entry_keys(trades: list[dict[str, Any]]) -> set[tuple[str, str, float]]:
    return {
        (
            str(trade.get("open_time", trade.get("timestamp", ""))),
            str(trade.get("direction", "")),
            round(float(trade.get("entry", trade.get("open_price", 0.0))), 2),
        )
        for trade in trades
    }


def _trade_exit_keys(trades: list[dict[str, Any]]) -> set[tuple[str, str, float]]:
    return {
        (
            str(trade.get("close_time", "")),
            str(trade.get("direction", "")),
            round(float(trade.get("exit", trade.get("close_price", 0.0))), 2),
        )
        for trade in trades
    }


def _r_values(trades: list[dict[str, Any]]) -> list[float]:
    return [
        float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))) / float(trade["risk_amount"])
        for trade in trades
        if float(trade.get("risk_amount", 0.0)) > 0.0
    ]


def _average_r(trades: list[dict[str, Any]]) -> float:
    values = _r_values(trades)
    return float(np.mean(values)) if values else 0.0


def _expectancy_r(trades: list[dict[str, Any]]) -> float:
    values = _r_values(trades)
    if not values:
        return 0.0
    wins = [value for value in values if value > 0.0]
    losses = [value for value in values if value <= 0.0]
    win_rate = len(wins) / len(values)
    loss_rate = len(losses) / len(values)
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    return win_rate * avg_win + loss_rate * avg_loss


def _median_r(trades: list[dict[str, Any]]) -> float:
    values = _r_values(trades)
    return float(np.median(values)) if values else 0.0


def _capital_weighted_r(trades: list[dict[str, Any]]) -> float:
    total_risk = sum(float(trade.get("risk_amount", 0.0)) for trade in trades)
    total_net = sum(float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))) for trade in trades)
    return total_net / total_risk if total_risk > 0.0 else 0.0


def _exit_reason_distribution(trades: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for trade in trades:
        reason = str(trade.get("reason", "unknown"))
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _fill_type_distribution(trades: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for trade in trades:
        fill_type = str(trade.get("fill_type", "unknown"))
        counts[fill_type] = counts.get(fill_type, 0) + 1
    return dict(sorted(counts.items()))


def load_backtest_ohlcv(
    settings: dict[str, Any],
    *,
    allow_proxy: bool = False,
    allow_synthetic: bool = False,
) -> tuple[pd.DataFrame, str]:
    db_path = Path(str(settings.get("data", {}).get("db_path", "aurum1/data/aurum1.sqlite3")))
    oanda_settings = settings.get("broker", {}).get("oanda", {})
    ingestor = AurumDataIngestor(settings)
    if os.getenv(str(oanda_settings.get("api_key_env", "OANDA_API_KEY"))):
        end = datetime.now(UTC)
        start = end - timedelta(days=365)
        try:
            fetched = ingestor.fetch_ohlcv_range("M15", start, end)
            if not fetched.empty:
                ingestor.persist_ohlcv("M15", fetched)
                frame = _oanda_cache_frame(db_path)
                if not frame.empty:
                    return frame.sort_index(), "real OANDA M15 XAU/USD"
        except Exception as exc:
            frame = _oanda_cache_frame(db_path)
            if not frame.empty:
                return frame.sort_index(), f"cached SQLite OANDA M15 XAU/USD (fetch warning: {exc})"
            if not allow_proxy and not allow_synthetic:
                raise RuntimeError(f"Real OANDA XAU_USD backtest data unavailable: {exc}") from exc
    else:
        frame = _oanda_cache_frame(db_path)
        if not frame.empty:
            return frame.sort_index(), "cached SQLite OANDA M15 XAU/USD"
        if not allow_proxy and not allow_synthetic:
            raise RuntimeError("Real OANDA XAU_USD backtest requires OANDA_API_KEY in .env or environment.")

    if allow_proxy:
        try:
            return _gold_futures_proxy(settings)
        except Exception as exc:
            if not allow_synthetic:
                raise RuntimeError(f"Gold futures proxy requested but unavailable: {exc}") from exc

    if allow_synthetic:
        return synthetic_ohlcv(900), "synthetic fallback (plumbing only)"

    raise RuntimeError("Real OANDA XAU_USD backtest data unavailable.")


def _oanda_cache_frame(db_path: Path) -> pd.DataFrame:
    try:
        frame = load_ohlcv("M15", db_path)
    except Exception:
        return pd.DataFrame()
    if frame.empty:
        return frame
    if "source" in frame.columns:
        frame = frame[frame["source"].astype(str).str.lower() == "oanda"]
    if "instrument" in frame.columns:
        frame = frame[frame["instrument"].astype(str).eq("XAU_USD")]
    return frame.sort_index().copy()


def _gold_futures_proxy(settings: dict[str, Any]) -> tuple[pd.DataFrame, str]:
    try:
        import yfinance as yf

        raw = yf.download("GC=F", period="60d", interval="15m", auto_adjust=False, progress=False, threads=False)
        if raw.empty:
            raise RuntimeError("yfinance returned no rows")
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [str(column[0]) for column in raw.columns]
        raw = raw.rename(columns={column: column.lower().replace(" ", "_") for column in raw.columns})
        raw.index = pd.to_datetime(raw.index, utc=True)
        frame = pd.DataFrame(index=raw.index)
        frame["open"] = raw["open"].astype("float64")
        frame["high"] = raw["high"].astype("float64")
        frame["low"] = raw["low"].astype("float64")
        frame["close"] = raw["close"].astype("float64")
        frame["volume"] = raw.get("volume", pd.Series(1.0, index=raw.index)).fillna(1.0).astype("float64")
        frame["source"] = "yfinance"
        frame["instrument"] = "GC=F"
        return frame.dropna().sort_index().tail(DEFAULT_OANDA_BACKTEST_COUNT), "GC=F futures proxy"
    except Exception as exc:
        raise RuntimeError(f"yfinance GC=F returned no usable rows: {exc}") from exc


def load_backtest_macro(
    settings: dict[str, Any],
    ohlcv: pd.DataFrame,
    *,
    allow_placeholder: bool = False,
) -> tuple[pd.DataFrame, str]:
    db_path = Path(str(settings.get("data", {}).get("db_path", "aurum1/data/aurum1.sqlite3")))
    fred_settings = settings.get("data", {}).get("fred", {})
    fred_api_key_env = str(fred_settings.get("api_key_env", "FRED_API_KEY"))
    cached = _macro_cache_frame(db_path)
    if not cached.empty:
        return cached, "cached SQLite real macro"
    if not os.getenv(fred_api_key_env):
        if allow_placeholder:
            return synthetic_macro_for(ohlcv), "placeholder (set FRED_API_KEY for real data)"
        raise RuntimeError("Real macro backtest requires FRED_API_KEY in .env or environment.")
    try:
        ingestor = AurumDataIngestor(settings)
        ingestor.persist_macro_data(ingestor.fetch_macro_data())
        macro = _macro_cache_frame(db_path)
        if not macro.empty:
            return macro, "real (DGS10, CPI, DXY, VIX)"
    except Exception as exc:
        if allow_placeholder:
            return synthetic_macro_for(ohlcv), f"placeholder (real macro failed: {exc})"
        raise RuntimeError(f"Real macro backtest data unavailable: {exc}") from exc
    raise RuntimeError("Real macro backtest data unavailable.")


def _macro_cache_frame(db_path: Path) -> pd.DataFrame:
    try:
        return load_macro(db_path)
    except Exception:
        return pd.DataFrame()


def load_backtest_cot(
    settings: dict[str, Any],
    ohlcv: pd.DataFrame,
    *,
    allow_placeholder: bool = False,
) -> tuple[pd.DataFrame, str]:
    db_path = Path(str(settings.get("data", {}).get("db_path", "aurum1/data/aurum1.sqlite3")))
    cached = _cot_cache_frame(db_path)
    if not cached.empty:
        return cached, "cached SQLite real COT"
    try:
        ingestor = AurumDataIngestor(settings)
        ingestor.persist_cot_data(ingestor.fetch_cot_data())
        cot = _cot_cache_frame(db_path)
        if not cot.empty:
            return cot, "real CFTC COT"
    except Exception as exc:
        if allow_placeholder:
            return synthetic_cot_for(ohlcv), f"placeholder (real COT failed: {exc})"
        raise RuntimeError(f"Real CFTC COT backtest data unavailable: {exc}") from exc
    raise RuntimeError("Real CFTC COT backtest data unavailable.")


def _cot_cache_frame(db_path: Path) -> pd.DataFrame:
    try:
        return load_cot(db_path)
    except Exception:
        return pd.DataFrame()


def print_header(
    settings: dict[str, Any],
    ohlcv: pd.DataFrame,
    data_label: str,
    macro_status: str,
    cot_status: str,
    history_status: dict[str, Any],
) -> None:
    start = ohlcv.index.min().isoformat() if len(ohlcv) else "n/a"
    end = ohlcv.index.max().isoformat() if len(ohlcv) else "n/a"
    print("=" * 58)
    print("AURUM-1 Phase 7 Backtest")
    print("=" * 58)
    print(f"Data:       {len(ohlcv)} rows {data_label}")
    print(f"Date range: {start} -> {end}")
    print(f"Macro data: {macro_status}")
    print(f"COT data:   {cot_status}")
    print(
        "History:    "
        f"{history_status['bars']} bars over {history_status['span_days']:.1f} days "
        f"(minimum {history_status['min_bars']} bars / {history_status['min_days']:.1f} days)"
    )
    if history_status["short_history"]:
        print("History gate: SHORT-HISTORY PLUMBING ONLY; quantitative readiness not verified")
    print(f"Runtime DB: {settings.get('backtesting', {}).get('runtime_db_path', 'aurum1/data/aurum1.sqlite3')} (no backtest execution writes)")
    print(f"Market DB:  {settings.get('data', {}).get('db_path', 'aurum1/data/backtest_market_cache.sqlite3')}")
    print(f"Backtest DB:{settings.get('backtesting', {}).get('execution_db_path', 'temp per run')}")
    print("Runtime DB writes: disabled for backtest execution/output")
    print("Sharpe frequency: daily returns")
    print(f"WF overlap: {'yes' if settings.get('backtesting', {}).get('allow_overlap', False) else 'no'}")
    print(
        "Window:     "
        f"train={settings['backtesting']['train_bars']} "
        f"test={settings['backtesting']['test_bars']} "
        f"step={settings['backtesting']['step_bars']}"
    )
    print("=" * 58)


def print_signal_audit(
    ohlcv: pd.DataFrame,
    macro: pd.DataFrame,
    cot: pd.DataFrame,
    settings: dict[str, Any],
) -> None:
    total_bars = len(ohlcv)
    features = FeatureEngineer({"feature_engineering": {"lookahead_check": False}}).build_features(
        ohlcv,
        macro,
        cot,
        include_target=False,
    )
    if features.empty:
        print("\nSignal Audit (full dataset): no feature rows available after warmup")
        return

    adx_threshold = float(settings.get("signals", {}).get("adx_threshold", 25.0))
    adx_mask = features["adx_14"] > adx_threshold
    session_mask = (features["session_london"] == 1) | (features["session_ny"] == 1)
    bullish_alignment = features["ema_alignment_score"] >= 3
    bearish_alignment = features["ema_alignment_score"] <= -3
    all_tech = adx_mask & session_mask & (bullish_alignment | bearish_alignment)

    raw_buy = features["ema_9"] > features["ema_20"]
    raw_sell = features["ema_9"] < features["ema_20"]
    regimes = RegimeClassifier.generate_labels(features).map(REGIME_LABELS)
    buy_signal = raw_buy & (regimes != "TRENDING_DOWN")
    sell_signal = raw_sell & (regimes != "TRENDING_UP")
    flat_signal = ~(buy_signal | sell_signal)

    print("\nSignal Audit (full dataset):")
    print(f"  Total bars:                    {total_bars}")
    print(f"  Bars with ADX > 25:            {_count_line(int(adx_mask.sum()), total_bars)}")
    print(f"  Bars in London/NY session:     {_count_line(int(session_mask.sum()), total_bars)}")
    print(f"  Bars with EMA alignment >= 3:  {_count_line(int(bullish_alignment.sum()), total_bars)}")
    print(f"  Bars meeting all tech filters: {_count_line(int(all_tech.sum()), total_bars)}")
    print(f"  Bars with BUY signal:          {_count_line(int(buy_signal.sum()), total_bars)}")
    print(f"  Bars with SELL signal:         {_count_line(int(sell_signal.sum()), total_bars)}")
    print(f"  Bars with FLAT signal:         {_count_line(int(flat_signal.sum()), total_bars)}")


def _count_line(count: int, total: int) -> str:
    percent = (count / total * 100.0) if total else 0.0
    return f"{count:>6} ({percent:>5.1f}%)"


def tune_backtest_windows(settings: dict[str, Any], rows: int) -> dict[str, Any]:
    tuned = copy.deepcopy(settings)
    tuned.setdefault("backtesting", {})
    default_train = int(tuned["backtesting"].get("train_bars", 6552))
    default_test = int(tuned["backtesting"].get("test_bars", 1638))
    if rows < default_train + default_test:
        train = max(300, int(rows * 0.50))
        test = max(100, int(rows * 0.25))
        if train + test > rows:
            test = max(100, rows - train)
        step = test
        tuned["backtesting"].update({"train_bars": train, "test_bars": test, "step_bars": step})
    tuned["backtesting"].setdefault("allow_overlap", False)
    tuned.setdefault("signals", {})
    tuned["signals"].setdefault("require_session_filter", False)
    tuned["signals"].setdefault("adx_threshold", 10)
    tuned.setdefault("execution", {})
    tuned["execution"].setdefault("slippage_std_pips", 0.5)
    tuned["execution"].setdefault("paper_spread_pips", 1.5)
    tuned.setdefault("broker", {})
    tuned["broker"]["paper_trade"] = True
    tuned["broker"].setdefault("paper_initial_equity", 10000.0)
    return tuned


def synthetic_ohlcv(rows: int) -> pd.DataFrame:
    index = pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="15min", tz="UTC")
    close = 2300.0 + np.cumsum(np.sin(np.arange(rows) / 7.0) * 0.25 + 0.03)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + 1.6
    low = np.minimum(open_, close) - 1.6
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0,
            "source": "synthetic",
            "instrument": "XAU_USD",
        },
        index=index,
    )


def synthetic_macro_for(ohlcv: pd.DataFrame) -> pd.DataFrame:
    index = pd.date_range(
        ohlcv.index.min().normalize(),
        ohlcv.index.max().normalize() + pd.Timedelta(days=1),
        freq="D",
        tz="UTC",
    )
    step = np.arange(len(index), dtype=float)
    return pd.DataFrame(
        {
            "dgs10": 4.0 + step * 0.01,
            "cpi": 315.0 + step * 0.1,
            "cpi_yoy": 3.0 + step * 0.01,
            "real_yield": 1.0 + step * 0.002,
            "dxy": 104.0 + step * 0.03,
            "dxy_daily_return": np.sin(step / 5.0) * 0.001,
            "vix": 16.0 + np.cos(step / 4.0),
            "vix_1d_change": np.sin(step / 3.0) * 0.1,
        },
        index=index,
    )


def synthetic_cot_for(ohlcv: pd.DataFrame) -> pd.DataFrame:
    index = pd.date_range(
        ohlcv.index.min().normalize() - pd.Timedelta(days=14),
        ohlcv.index.max().normalize(),
        freq="7D",
        tz="UTC",
    )
    return pd.DataFrame(
        {
            "market_name": "GOLD - COMMODITY EXCHANGE INC.",
            "open_interest": 200000.0,
            "long_positions": 120000.0,
            "short_positions": 70000.0,
            "net_positioning": 50000.0,
            "cot_net_long_pct": 0.20,
            "source": "synthetic",
        },
        index=index,
    )


if __name__ == "__main__":
    raise SystemExit(main())
