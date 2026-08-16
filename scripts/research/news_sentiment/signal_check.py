"""Signal plausibility check for daily news sentiment vs XAU/USD returns.

This is the go/no-go filter for the news-sentiment research thread. It takes
the daily sentiment series produced by `build_daily_sentiment.py` and answers
three questions:

  1. **IC / correlation**: does daily net sentiment predict next-day gold
     return? Reports Pearson, Spearman, and ICIR (IC mean/std) for lags 0-3.
  2. **Day-bias hit rate**: does a bullish-vs-bearish news day predict next-day
     direction better than coin flip?
  3. **Strategy-fit probe**: slices the IC by market regime (up / down / choppy
     daily returns) to see WHERE the signal concentrates — trend-following
     days, mean-reversion days, etc.

Interpretation thresholds (honest, pre-set):
  - |IC| >= 0.05 at lag>=1 with stable sign -> weak but real, worth pursuing
  - |IC| < 0.02 -> no signal at daily frequency; likely a dead end
  - hit-rate meaningfully above 0.50 (e.g. >=0.54 with n>100) -> day-bias
    overlay worth testing
  - regime slice shows a big edge in one regime -> strategy-fit hint

Usage:
    python scripts/research/news_sentiment/signal_check.py \
        --sentiment reports/research/news_daily_sentiment.csv \
        [--out reports/research/news_signal_check.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IC_FLOOR = 0.02   # below this |IC| is noise
IC_WEAK = 0.05    # at/above this is a weak-but-real signal
HIT_FLOOR = 0.52  # at/above this the day-bias overlay starts being interesting


def _ic_report(daily: pd.DataFrame, sentiment_col: str, ret_col: str) -> dict:
    """Pearson/Spearman IC and ICIR between sentiment (lag) and forward return."""
    n = len(daily)
    if n < 10:
        return {"pearson": None, "spearman": None, "icir_approx": None, "n": n}
    s = daily[sentiment_col].to_numpy(dtype=float)
    r = daily[ret_col].to_numpy(dtype=float)
    if np.std(s) == 0 or np.std(r) == 0:
        return {"pearson": None, "spearman": None, "icir_approx": None, "n": n}
    ic = float(np.corrcoef(r, s)[0, 1])
    ic_spearman = float(stats.spearmanr(r, s)[0]) if n > 2 else 0.0
    # ICIR: mean of daily IC over the period / std. Since we have one series,
    # approximate ICIR via the t-stat of the correlation.
    t_stat = ic * np.sqrt((n - 2) / max(1.0, 1.0 - ic**2)) if n > 2 else 0.0
    icir = t_stat / np.sqrt(n) if n > 0 else 0.0
    return {
        "pearson": round(float(ic), 4),
        "spearman": round(float(ic_spearman), 4),
        "icir_approx": round(float(icir), 4),
        "n": n,
    }


def _hit_rate(daily: pd.DataFrame, sentiment_col: str, ret_col: str) -> dict:
    """Fraction of days where sentiment sign predicts next-day return sign."""
    s = daily[sentiment_col].dropna()
    r = daily[ret_col].dropna()
    df = pd.DataFrame({"s": s, "r": r}).dropna()
    if df.empty:
        return {"hit_rate": 0.0, "n": 0}
    pred_up = df["s"] > 0
    actual_up = df["r"] > 0
    hits = (pred_up == actual_up).mean()
    return {"hit_rate": round(float(hits), 4), "n": int(len(df))}


def _regime_breakdown(daily: pd.DataFrame, sentiment_col: str, ret_col: str) -> dict:
    """Correlation of sentiment vs forward return sliced by prior-day regime."""
    df = daily.copy()
    # Regime of the market: classify by prior-day return magnitude/sign.
    df["prev_ret"] = df[ret_col].shift(1)
    bins = pd.cut(
        df["prev_ret"],
        bins=[-np.inf, -0.005, 0.005, np.inf],
        labels=["down", "choppy", "up"],
    )
    out: dict[str, dict] = {}
    for regime in ["down", "choppy", "up"]:
        sub = df[bins == regime].dropna(subset=[sentiment_col, ret_col])
        if len(sub) < 5:
            out[regime] = {"n": int(len(sub)), "pearson": None}
            continue
        out[regime] = {
            "n": int(len(sub)),
            "pearson": round(float(sub[ret_col].corr(sub[sentiment_col], method="pearson")), 4),
        }
    return out


def _lags(daily: pd.DataFrame, sentiment_col: str, ret_col: str, max_lag: int = 3) -> dict:
    """IC of sentiment at lag L vs forward return (shift sentiment back L days)."""
    out: dict[str, dict] = {}
    for lag in range(0, max_lag + 1):
        df = pd.DataFrame({
            "s": daily[sentiment_col].shift(lag),
            "r": daily[ret_col],
        }).dropna()
        if len(df) < 10:
            out[f"lag{lag}"] = {"pearson": None, "n": int(len(df))}
            continue
        out[f"lag{lag}"] = {
            "pearson": round(float(df["r"].corr(df["s"], method="pearson")), 4),
            "spearman": round(float(df["r"].corr(df["s"], method="spearman")), 4),
            "n": int(len(df)),
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sentiment", required=True, type=Path, help="daily sentiment CSV")
    parser.add_argument("--out", type=Path, default=None, help="JSON output path")
    args = parser.parse_args()

    daily = pd.read_csv(args.sentiment, parse_dates=["date"])
    daily = daily.sort_values("date").reset_index(drop=True)
    sent_col = "net_sentiment"
    ret_col = "gold_return"
    daily = daily.dropna(subset=[sent_col, ret_col])

    if daily.empty:
        print("No usable rows (need both net_sentiment and gold_return).")
        return

    report = {
        "n_days": int(len(daily)),
        "date_range": [str(daily["date"].min().date()), str(daily["date"].max().date())],
        "lags": _lags(daily, sent_col, ret_col, max_lag=3),
        "contemporaneous": _ic_report(daily, sent_col, ret_col),
        "hit_rate": _hit_rate(daily, sent_col, ret_col),
        "regime_breakdown": _regime_breakdown(daily, sent_col, ret_col),
    }

    # Verdict (honest, pre-set). Guard against tiny samples.
    MIN_SAMPLE = 30
    n = report["n_days"]
    if n < MIN_SAMPLE:
        verdict = (f"INSUFFICIENT DATA — only {n} days; need >= {MIN_SAMPLE} for a reliable "
                   "IC/hit-rate read. This is a pipeline check, not a signal verdict.")
    else:
        best = max((v["pearson"] for v in report["lags"].values() if v.get("pearson") is not None), default=0.0)
        lag1 = report["lags"].get("lag1", {}).get("pearson")
        hit = report["hit_rate"]["hit_rate"]

        if lag1 is not None and abs(lag1) >= IC_WEAK:
            verdict = "PROMISING — lag-1 sentiment IC above 0.05. Worth building the overlay/shadow strategy."
        elif abs(best) >= IC_FLOOR:
            verdict = f"WEAK — best |IC| {abs(best):.3f} is above noise floor but below 0.05. Marginal; consider a larger window or lexicon baseline."
        else:
            verdict = "NO SIGNAL — |IC| below 0.02 at daily frequency. Likely a dead end for a standalone stream; overlay value depends on hit-rate below."

        if hit >= HIT_FLOOR:
            verdict += f" Day-bias hit-rate {hit:.2f} is above the {HIT_FLOOR:.2f} floor — the daily-context overlay idea deserves a direct test."
    report["verdict"] = verdict

    print(json.dumps(report, indent=2))
    print(f"\nVERDICT: {verdict}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2))
        print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
