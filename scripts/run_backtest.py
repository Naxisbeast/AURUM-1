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

from aurum1.backtesting import BacktestEngine, WalkForwardValidator, run_ablation_backtest, run_monte_carlo
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


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args(argv)
    load_dotenv(ROOT / ".env")

    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    ohlcv, data_label = load_backtest_ohlcv(
        settings,
        allow_proxy=args.allow_gold_futures_proxy,
        allow_synthetic=args.allow_synthetic,
    )
    macro, macro_status = load_backtest_macro(settings, ohlcv, allow_placeholder=args.allow_placeholder_data)
    cot, cot_status = load_backtest_cot(settings, ohlcv, allow_placeholder=args.allow_placeholder_data)
    settings = tune_backtest_windows(settings, len(ohlcv))

    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    print_header(settings, ohlcv, data_label, macro_status, cot_status)
    print_signal_audit(ohlcv, macro, cot, settings)

    walk_forward = WalkForwardValidator(settings).run(
        ohlcv,
        macro,
        cot,
        mode=MachineMode.RULE_REGIME,
        initial_equity=float(settings.get("broker", {}).get("paper_initial_equity", 10000.0)),
    )
    combined_trades = [trade for window in walk_forward.windows for trade in window.trades]
    monte_carlo = run_monte_carlo(
        combined_trades,
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

    best_mode, best_result = max(ablation.items(), key=lambda item: item[1].sharpe_ratio)
    recommendation = "proceed to paper trading" if walk_forward.promotion_gate_passed and monte_carlo.ruin_probability < 0.05 else "review risk params"
    if walk_forward.mean_sharpe <= 0.0 or walk_forward.mean_profit_factor < 1.0:
        recommendation = "retrain models"

    print("\nWalk-forward summary:")
    print(f"  Windows: {len(walk_forward.windows)}")
    print(f"  Mean Sharpe: {walk_forward.mean_sharpe:.2f}")
    print(f"  Mean Profit Factor: {walk_forward.mean_profit_factor:.2f}")
    print(f"  Mean Win Rate: {walk_forward.mean_win_rate:.2%}")
    print(f"  Mean Max Drawdown: {walk_forward.mean_max_drawdown:.2%}")
    print_promotion_gate(walk_forward)
    print_walk_forward_detail(walk_forward.windows)
    print("\nMonte Carlo summary:")
    print(f"  Median final equity: {monte_carlo.median_final_equity:.2f}")
    print(f"  5th percentile final equity: {monte_carlo.pct5_final_equity:.2f}")
    print(f"  95th percentile drawdown: {monte_carlo.pct95_max_drawdown:.2%}")
    print(f"  Ruin probability: {monte_carlo.ruin_probability:.2%}")
    print("\nFinal summary:")
    print(f"  Best mode by Sharpe: {best_mode} ({best_result.sharpe_ratio:.2f})")
    print(
        "  Promotion gate: "
        f"{'PASSED' if walk_forward.promotion_gate_passed else 'FAILED'} "
        f"({walk_forward.criteria_passed}/6 criteria met)"
    )
    print(f"  Monte Carlo ruin probability: {monte_carlo.ruin_probability:.2%}")
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
    return frame.tail(DEFAULT_OANDA_BACKTEST_COUNT).copy()


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
        step = max(50, test // 2)
        tuned["backtesting"].update({"train_bars": train, "test_bars": test, "step_bars": step})
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
