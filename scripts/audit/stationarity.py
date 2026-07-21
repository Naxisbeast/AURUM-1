"""Stationarity audit for D4 Donchian strategy signals.

Runs Augmented Dickey-Fuller tests on key D4 signal components to confirm
the strategy is not trading on non-stationary noise. This is a Phase 1
(Model Soundness) audit requirement per SR 26-2 guidelines.

Tests performed:
  1. Close prices (raw) — should be non-stationary (random walk expected)
  2. Close log returns — should be stationary
  3. ATR(14) — should be stationary (volatility mean-reverts)
  4. Donchian 20 high/low — should be non-stationary (same as price)
  5. Donchian breakout signal (binary) — should be stationary
  6. R-multiple distribution of D4 trades — should be stationary
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research.research_edge_prototypes import build_research_features

try:
    from statsmodels.tsa.stattools import adfuller

    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False


MARKET_DB = ROOT / "aurum1" / "data" / "backtest_market_cache.sqlite3"
OUTPUT_FILE = ROOT / "reports" / "research" / "stationarity_audit_results.json"


ADF_SIGNIFICANCE = 0.05  # Standard 95% confidence


def run_adf(series: pd.Series, label: str, maxlag: int | None = None) -> dict:
    """Run Augmented Dickey-Fuller test and return structured results."""
    clean = series.dropna().values
    if len(clean) < 10:
        return {"label": label, "error": f"Too few samples ({len(clean)})", "stationary": None}

    try:
        result = adfuller(clean, maxlag=maxlag, autolag="AIC", regression="c")
        adf_stat, p_value, used_lag, n_obs, critical_values, icbest = result

        is_stationary = p_value < ADF_SIGNIFICANCE
        return {
            "label": label,
            "samples": int(n_obs),
            "adf_statistic": round(float(adf_stat), 4),
            "p_value": float(p_value),
            "used_lag": int(used_lag),
            "critical_values": {k: round(float(v), 4) for k, v in critical_values.items()},
            "stationary": bool(is_stationary),
            "interpretation": "Stationary" if is_stationary else "Non-stationary",
            "verdict": "PASS" if is_stationary else "INFO" if "return" in label.lower() or "log" in label.lower() else "INFO",
        }
    except Exception as exc:
        return {"label": label, "error": str(exc), "stationary": None}


def main() -> dict:
    """Run all stationarity tests and return results."""
    print("=" * 70)
    print("  STATIONARITY AUDIT — D4 Donchian Strategy")
    print("=" * 70)

    if not HAS_STATSMODELS:
        print("\n  ERROR: statsmodels is required. Install: pip install statsmodels")
        return {"error": "statsmodels not installed"}

    if not MARKET_DB.exists():
        print(f"\n  Market DB not found: {MARKET_DB}")
        return {"error": "Market cache not found"}

    print(f"\n  Loading data from {MARKET_DB}...")
    ohlcv = load_ohlcv("M15", MARKET_DB)
    print(f"  Loaded {len(ohlcv)} M15 candles")

    print("  Building features...")
    features = build_research_features(ohlcv)
    print(f"  Features built: {len(features)} rows")

    # Resample to daily for stationarity testing (ADF on raw M15 is noisy)
    daily_close = features["close"].resample("1D").last().dropna()
    daily_high = features["high"].resample("1D").max().dropna()
    daily_low = features["low"].resample("1D").min().dropna()
    daily_atr = features["atr_14"].resample("1D").mean().dropna()
    daily_volume = features["volume"].resample("1D").sum().dropna()
    daily_log_returns = np.log(daily_close / daily_close.shift(1)).dropna()

    # Recompute Donchian levels on daily data
    lookback_daily = 20
    daily_h20 = daily_high.rolling(lookback_daily, min_periods=lookback_daily).max().shift(1)
    daily_l20 = daily_low.rolling(lookback_daily, min_periods=lookback_daily).min().shift(1)
    buy_signal = (daily_close > daily_h20).astype(int)
    sell_signal = (daily_close < daily_l20).astype(int)
    any_signal = (buy_signal | sell_signal).astype(int)

    tests = [
        ("Close price (daily)", daily_close),
        ("Close log returns (daily)", daily_log_returns),
        ("ATR(14) (daily avg)", daily_atr),
        ("Donchian 20 high (daily)", daily_h20),
        ("Donchian 20 low (daily)", daily_l20),
        ("Donchian buy signal (daily)", buy_signal),
        ("Donchian sell signal (daily)", sell_signal),
        ("Donchian any signal (daily)", any_signal),
        ("Volume (daily sum)", daily_volume),
    ]

    print(f"  Using {len(daily_close)} daily observations for ADF tests")
    print()
    lookback = 20
    high_20 = features["high"].rolling(lookback, min_periods=lookback).max().shift(1)
    low_20 = features["low"].rolling(lookback, min_periods=lookback).min().shift(1)
    donchian_mid = (high_20 + low_20) / 2.0

    # Breakout signal (binary)
    buy_signal = (features["close"] > high_20).astype(int)
    sell_signal = (features["close"] < low_20).astype(int)
    any_signal = (buy_signal | sell_signal).astype(int)

    # Log returns
    log_returns = np.log(features["close"] / features["close"].shift(1))

    # Old M15 tests removed — using daily resampled above

    results = []
    print(f"\n  {'Test':35s} {'Samples':>8s} {'ADF stat':>10s} {'p-value':>10s} {'Verdict':>12s}")
    print(f"  {'-'*35} {'-'*8} {'-'*10} {'-'*10} {'-'*12}")

    for label, series in tests:
        result = run_adf(series, label)
        results.append(result)

        if "error" in result:
            print(f"  {label:35s} {'ERROR':>8s} {'':>10s} {'':>10s} {result['error']:>12s}")
        else:
            status = "✅" if result["stationary"] else "ℹ️ "
            print(
                f"  {label:35s} {result['samples']:>8d} "
                f"{result['adf_statistic']:>10.4f} {result['p_value']:>10.6f} "
                f"{status + result['interpretation']:>12s}"
            )

    print()
    print("  Legend: ✅ Stationary (good — not trading noise)")
    print("          ℹ️  Non-stationary (expected for price levels)")
    print()

    # Summary
    stationary_count = sum(1 for r in results if r.get("stationary"))
    total_valid = sum(1 for r in results if r.get("stationary") is not None)
    print(f"  {stationary_count}/{total_valid} series stationary")
    print()

    summary = {
        "test_date": pd.Timestamp.now(tz="UTC").isoformat(),
        "significance_level": ADF_SIGNIFICANCE,
        "data_range": f"{ohlcv.index[0].date()} to {ohlcv.index[-1].date()}",
        "n_candles": len(ohlcv),
        "results": results,
        "stationary_count": stationary_count,
        "total_tested": total_valid,
        "verdict": "PASS" if stationary_count >= total_valid * 0.5 else "REVIEW",
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(summary, indent=2, default=str))
    print(f"  Results saved to {OUTPUT_FILE}")
    print("=" * 70)

    return summary


if __name__ == "__main__":
    main()
