"""Live vs Backtest Comparator — compare D4 paper trader stats to 11-year distribution.

Reads trades from paper_trading.sqlite3 and compares key metrics against the
known 11-year backtest distribution. Detects drift, flags anomalies, and
generates a structured JSON report.

Usage:
    python scripts/run_live_vs_backtest_comparator.py

    # Save report to a file
    python scripts/run_live_vs_backtest_comparator.py --output reports/forward_shadow/drift_report.json
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research.research_edge_prototypes import build_research_features

# ---------------------------------------------------------------------------
# Backtest baseline — D4, Donchian 20, BUY+SELL, 2R exit, no filters
# Derived from the 11-year full run on 236,303 M15 candles (2016-06 to 2026-06)
# ---------------------------------------------------------------------------

D4_BASELINE = {
    "label": "D4 11-Year Backtest (2016-06 to 2026-06)",
    "source": "Full backtest on backtest_market_cache.sqlite3, 236,303 M15 candles",
    "total_trades": 8178,
    "win_rate": 0.370,        # 37.0%
    "profit_factor": 1.14,     # ratio gross profit / gross loss
    "mean_r": 0.101,           # average R-multiple across all trades
    "median_r": 0.0,           # median R-multiple (WR ~37%)
    "std_r": 1.42,             # standard deviation of R-multiples
    "sharpe": 1.27,            # mean Sharpe across 18 walk-forward windows
    "positive_window_rate": 0.889,  # 16/18 windows positive
    "percentile_5th_r": -1.0,  # 5th percentile R (worst 5% of trades)
    "percentile_25th_r": -1.0, # 25th percentile R
    "percentile_75th_r": 2.0,  # 75th percentile R
    "percentile_95th_r": 2.0,  # 95th percentile R
    "max_dd_pct": 20.3,        # 99th percentile drawdown (risk sensitivity at 0.25%)
    "avg_trades_per_day": 1.8, # 8178 trades / ~4520 trading days
    "win_r_avg": 2.0,          # average winning trade is +2R (fixed target)
    "loss_r_avg": -1.0,        # average losing trade is -1R (fixed stop)
    "worst_losing_streak": 40, # Monte Carlo 10k sims
}

# Warning thresholds (how many standard deviations from baseline before flagging)
WARNING_ZONES = {
    "win_rate": {"low": 0.30, "high": 0.44, "critical_low": 0.25},       # expected ~37%
    "profit_factor": {"low": 0.90, "critical_low": 0.80},                  # expected 1.14
    "mean_r": {"low": 0.0, "critical_low": -1.0},                         # expected +0.10R
    "avg_trades_per_day": {"low": 0.1, "critical_low": 0.0},               # expected 1.8/day
}


def load_trades_from_db(db_path: str | Path) -> list[dict]:
    """Load all completed trades from paper_trading.sqlite3."""
    db = Path(db_path)
    if not db.exists():
        print(f"  Paper trading DB not found: {db}")
        return []

    trades: list[dict] = []
    try:
        with closing(sqlite3.connect(str(db))) as conn:
            rows = conn.execute(
                "SELECT entry_time, exit_time, direction, entry_price, exit_price, "
                "stop_loss, take_profit, units, risk_amount, r_multiple, net_pnl, "
                "spread_cost, slippage_cost, exit_reason FROM trades ORDER BY entry_time"
            ).fetchall()
            for row in rows:
                trades.append({
                    "entry_time": row[0] or "",
                    "exit_time": row[1] or "",
                    "direction": row[2],
                    "entry_price": row[3],
                    "exit_price": row[4] or 0.0,
                    "stop_loss": row[5],
                    "take_profit": row[6],
                    "units": row[7],
                    "risk_amount": row[8] or 0.0,
                    "r_multiple": row[9] or 0.0,
                    "net_pnl": row[10] or 0.0,
                    "spread_cost": row[11] or 0.0,
                    "slippage_cost": row[12] or 0.0,
                    "exit_reason": row[13] or "",
                })
    except sqlite3.OperationalError:
        pass  # no trades table yet
    return trades


def compute_metrics(trades: list[dict]) -> dict[str, Any]:
    """Compute aggregate metrics from a list of completed trades."""
    n = len(trades)
    if n == 0:
        return {"total_trades": 0}

    r_vals = [float(t.get("r_multiple", 0)) for t in trades if t.get("exit_reason")]
    if not r_vals:
        return {"total_trades": 0}

    wins = [r for r in r_vals if r > 0]
    losses = [r for r in r_vals if r < 0]
    n_wins = len(wins)
    n_losses = len(losses)

    gross_profit = sum(abs(w) for w in wins)
    gross_loss = sum(abs(l) for l in losses)

    # Timeframe
    times = [t.get("entry_time", "") for t in trades if t.get("entry_time")]
    first_date = min(times) if times else ""
    last_date = max(times) if times else ""
    days_span = 1
    if first_date and last_date:
        try:
            fd = datetime.fromisoformat(first_date.replace("Z", "+00:00"))
            ld = datetime.fromisoformat(last_date.replace("Z", "+00:00"))
            days_span = max((ld - fd).total_seconds() / 86400, 1)
        except (ValueError, TypeError):
            pass

    # Losing streak
    max_streak = 0
    current_streak = 0
    for r in r_vals:
        if r < 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    # Consecutive wins
    max_win_streak = 0
    current_win_streak = 0
    for r in r_vals:
        if r > 0:
            current_win_streak += 1
            max_win_streak = max(max_win_streak, current_win_streak)
        else:
            current_win_streak = 0

    return {
        "total_trades": n,
        "win_rate": n_wins / n if n > 0 else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0),
        "mean_r": sum(r_vals) / len(r_vals),
        "median_r": sorted(r_vals)[len(r_vals) // 2] if r_vals else 0.0,
        "std_r": float(__import__("statistics").stdev(r_vals)) if len(r_vals) > 1 else 0.0,
        "r_values": [round(r, 4) for r in r_vals],
        "n_wins": n_wins,
        "n_losses": n_losses,
        "total_r": sum(r_vals),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "worst_losing_streak": max_streak,
        "best_win_streak": max_win_streak,
        "avg_trades_per_day": round(n / days_span, 2),
        "days_span": round(days_span, 1),
        "first_date": first_date,
        "last_date": last_date,
        "exit_reasons": _count_reasons(trades),
    }


def _count_reasons(trades: list[dict]) -> dict[str, int]:
    """Count exit reasons."""
    counts: dict[str, int] = {}
    for t in trades:
        r = t.get("exit_reason", "unknown")
        counts[r] = counts.get(r, 0) + 1
    return counts


def detect_drift(live: dict[str, Any], baseline: dict[str, Any]) -> list[dict]:
    """Compare live metrics against baseline and flag any drift."""
    flags: list[dict] = []
    if live["total_trades"] == 0:
        return flags

    for metric, zones in WARNING_ZONES.items():
        live_val = live.get(metric)
        if live_val is None:
            continue
        base_val = baseline.get(metric)
        if base_val is None:
            continue

        severity = "ok"
        message = f"{metric}: live={live_val:.3f} vs baseline={base_val:.3f}"

        if "critical_low" in zones and live_val < zones["critical_low"]:
            severity = "critical"
            message += " — CRITICAL: well below minimum threshold"
        elif "low" in zones and live_val < zones["low"]:
            severity = "warning"
            message += " — WARNING: below expected range"
        elif "high" in zones and live_val > zones["high"]:
            severity = "warning"
            message += " — WARNING: above expected range"

        if severity != "ok":
            flags.append({
                "metric": metric,
                "severity": severity,
                "live_value": round(float(live_val), 4),
                "baseline_value": float(base_val),
                "threshold_low": zones.get("low"),
                "threshold_high": zones.get("high"),
                "message": message,
            })

    # Additional checks
    if live["total_trades"] > 0:
        # Check exit reason distribution
        reasons = live.get("exit_reasons", {})
        tp_pct = reasons.get("take_profit", 0) / live["total_trades"] * 100
        sl_pct = (reasons.get("stop_loss", 0) + reasons.get("stop_loss_gap", 0)) / live["total_trades"] * 100
        if tp_pct < 20:
            flags.append({
                "metric": "take_profit_rate",
                "severity": "warning",
                "live_value": round(tp_pct, 1),
                "baseline_value": 37.0,  # ~WR
                "message": f"Take profit rate is {tp_pct:.1f}% — many trades closing at stop loss",
            })
        if live["worst_losing_streak"] > baseline.get("worst_losing_streak", 40):
            flags.append({
                "metric": "losing_streak",
                "severity": "warning",
                "live_value": live["worst_losing_streak"],
                "baseline_value": baseline["worst_losing_streak"],
                "message": f"Losing streak of {live['worst_losing_streak']} exceeds baseline max of {baseline['worst_losing_streak']}",
            })

    return flags


def run_full_backtest_for_r_distribution() -> list[float]:
    """Run the full D4 backtest locally to extract trade R-multiples for KS test."""
    try:
        settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
        spec = InstrumentSpec.from_settings(settings)
    except Exception:
        return []  # non-critical

    db_path = ROOT / "aurum1" / "data" / "backtest_market_cache.sqlite3"
    if not db_path.exists():
        print("  (backtest cache not found locally — skipping R-distribution comparison)")
        return []

    LOOKBACK = 20
    SL_MULT = 2.0
    R_MULT = 2.0
    SLIP_PIPS = 0.5
    slip_dist = SLIP_PIPS * spec.pip_size
    SP_EST = 0.15  # estimated round-turn spread cost in price units

    ohlcv = load_ohlcv("M15", db_path)
    features = build_research_features(ohlcv)

    buy_mask = features["close"] > features["high"].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
    sell_mask = features["close"] < features["low"].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
    valid = features["atr_14"].notna()
    buy_mask = buy_mask & valid
    sell_mask = sell_mask & valid

    r_values: list[float] = []
    for direction, mask in [("BUY", buy_mask), ("SELL", sell_mask)]:
        for sig_ts in features.index[mask.fillna(False)]:
            sig_bar = ohlcv.index.get_loc(sig_ts)
            entry_bar = sig_bar + 1
            if entry_bar >= len(ohlcv):
                continue
            ep = float(ohlcv.iloc[entry_bar]["open"]) + (slip_dist if direction == "BUY" else -slip_dist)
            atr_val = float(features.loc[sig_ts, "atr_14"])
            if not math.isfinite(atr_val) or atr_val <= 0:
                continue
            stop = ep - SL_MULT * atr_val if direction == "BUY" else ep + SL_MULT * atr_val
            if (direction == "BUY" and stop >= ep) or (direction == "SELL" and stop <= ep):
                continue
            tp = ep + R_MULT * abs(ep - stop) if direction == "BUY" else ep - R_MULT * abs(ep - stop)
            risk_dist = abs(ep - stop)

            # Simulate exit
            for bar_idx in range(entry_bar + 1, min(entry_bar + 200, len(ohlcv))):
                row = ohlcv.iloc[bar_idx]
                o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
                if direction == "BUY":
                    if o <= stop:
                        exit_price, reason = o, "stop_loss_gap"
                        break
                    if l <= stop:
                        exit_price, reason = stop, "stop_loss"
                        break
                    if h >= tp:
                        exit_price, reason = tp, "take_profit"
                        break
                else:
                    if o >= stop:
                        exit_price, reason = o, "stop_loss_gap"
                        break
                    if h >= stop:
                        exit_price, reason = stop, "stop_loss"
                        break
                    if l <= tp:
                        exit_price, reason = tp, "take_profit"
                        break
            else:
                exit_price, reason = float(ohlcv.iloc[min(entry_bar + 199, len(ohlcv) - 1)]["close"]), "timeout"

            gross_pnl = _pnl(spec, direction, ep, exit_price, 1.0)
            spread_cost = SP_EST * 2 * spec.pip_value_per_unit * 1.0
            net_pnl = gross_pnl - spread_cost
            r_val = net_pnl / risk_dist if risk_dist > 0 else 0.0
            r_values.append(r_val)

    print(f"  Backtest R-distribution: {len(r_values)} trades computed")
    return r_values


def _pnl(spec, direction: str, entry: float, exit_p: float, units: float) -> float:
    if direction == "BUY":
        return (exit_p - entry) * units * spec.ounces_per_unit
    return (entry - exit_p) * units * spec.ounces_per_unit


def assess_alignment(live_r: list[float], backtest_r: list[float]) -> dict:
    """Compare the live R-distribution to the backtest distribution."""
    if len(live_r) < 5 or len(backtest_r) < 100:
        return {"assessment": "insufficient_data", "live_samples": len(live_r), "backtest_samples": len(backtest_r)}

    try:
        from scipy.stats import ks_2samp, mannwhitneyu
    except ImportError:
        return {"assessment": "scipy_not_available", "live_samples": len(live_r), "backtest_samples": len(backtest_r)}

    ks_stat, ks_p = ks_2samp(live_r, backtest_r)
    mw_stat, mw_p = mannwhitneyu(live_r, backtest_r, alternative="two-sided")

    if ks_p < 0.01:
        alignment = "significant_drift"
    elif ks_p < 0.05:
        alignment = "possible_drift"
    else:
        alignment = "consistent"

    return {
        "assessment": alignment,
        "live_samples": len(live_r),
        "backtest_samples": len(backtest_r),
        "ks_statistic": round(float(ks_stat), 4),
        "ks_p_value": round(float(ks_p), 4),
        "mw_statistic": round(float(mw_stat), 2),
        "mw_p_value": round(float(mw_p), 4),
        "interpretation": (
            "Live R-distribution matches backtest (p>0.05)" if alignment == "consistent"
            else "Possible drift detected — monitor closely" if alignment == "possible_drift"
            else "Significant drift detected — investigate strategy or market regime change"
        ),
    }


def generate_report(live_trades: list[dict], baseline: dict[str, Any],
                    backtest_r: list[float] | None = None) -> dict:
    """Generate a complete live vs backtest comparison report."""
    live_metrics = compute_metrics(live_trades)
    drift_flags = detect_drift(live_metrics, baseline)

    # R-distribution comparison
    r_alignment: dict[str, Any] = {"assessment": "no_live_trades_yet"}
    if live_metrics["total_trades"] > 0 and backtest_r:
        live_r = [r for r in live_metrics.get("r_values", []) if r != 0]  # exclude non-exits
        if live_r:
            r_alignment = assess_alignment(live_r, backtest_r)

    # Determine overall health
    severity_order = {"critical": 0, "warning": 1, "ok": 2}
    worst = "ok"
    for flag in drift_flags:
        if severity_order.get(flag["severity"], 2) < severity_order.get(worst, 2):
            worst = flag["severity"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_health": worst,
        "n_flags": len(drift_flags),
        "flags": drift_flags,
        "live": live_metrics,
        "baseline": baseline,
        "r_distribution_alignment": r_alignment,
        "comparison": _build_comparison_table(live_metrics, baseline),
    }


def _build_comparison_table(live: dict, baseline: dict) -> list[dict]:
    """Build a row-by-row comparison for the summary."""
    keys = ["total_trades", "win_rate", "profit_factor", "mean_r", "median_r",
            "std_r", "worst_losing_streak", "avg_trades_per_day"]
    rows = []
    for key in keys:
        lv = live.get(key, 0)
        bv = baseline.get(key, 0)
        pct_diff = ((lv - bv) / bv * 100) if bv else 0
        rows.append({
            "metric": key.replace("_", " ").title(),
            "live_value": round(float(lv), 4) if isinstance(lv, (int, float)) else lv,
            "baseline_value": round(float(bv), 4) if isinstance(bv, (int, float)) else bv,
            "pct_diff": round(pct_diff, 1),
        })
    return rows


def print_report(report: dict):
    """Print a human-readable report to stdout."""
    print("\n" + "=" * 70)
    print(f"  D4 LIVE vs BACKTEST COMPARISON REPORT")
    print(f"  Generated: {report['generated_at']}")
    print("=" * 70)

    print(f"\n  Overall health: {report['overall_health'].upper()}")
    print(f"  Flags: {report['n_flags']}")

    if report["flags"]:
        print(f"\n  ⚠️  DRIFT FLAGS:")
        for flag in report["flags"]:
            icon = "🔴" if flag["severity"] == "critical" else "🟡"
            print(f"    {icon} [{flag['severity'].upper()}] {flag['message']}")

    print(f"\n  📊 KEY METRICS:")
    print(f"    {'Metric':<30} {'Live':<12} {'Baseline':<12} {'Diff':<10}")
    print(f"    {'-'*30} {'-'*12} {'-'*12} {'-'*10}")
    for row in report.get("comparison", []):
        diff_str = f"{row['pct_diff']:+.1f}%" if row["pct_diff"] else "—"
        print(f"    {row['metric']:<30} {row['live_value']:<12} {row['baseline_value']:<12} {diff_str:<10}")

    r_align = report.get("r_distribution_alignment", {})
    if r_align.get("assessment") not in ("no_live_trades_yet", "insufficient_data"):
        print(f"\n  📈 R-DISTRIBUTION ALIGNMENT:")
        print(f"    Assessment: {r_align['assessment']}")
        print(f"    KS test: statistic={r_align.get('ks_statistic')}, p={r_align.get('ks_p_value')}")
        print(f"    {r_align.get('interpretation', '')}")

    live = report.get("live", {})
    if live.get("total_trades", 0) > 0:
        print(f"\n  📋 LIVE TRADE DETAILS:")
        print(f"    Wins: {live.get('n_wins', 0)}  Losses: {live.get('n_losses', 0)}")
        print(f"    Total R: {live.get('total_r', 0):+.2f}")
        print(f"    Gross PnL: ${live.get('gross_profit', 0):+.2f} / ${live.get('gross_loss', 0):+.2f}")
        print(f"    Best streak: {live.get('best_win_streak', 0)}W  Worst streak: {live.get('worst_losing_streak', 0)}L")
        print(f"    Date range: {live.get('first_date', '')} to {live.get('last_date', '')} ({live.get('days_span', 0)} days)")

    print("=" * 70 + "\n")


def main():
    p = argparse.ArgumentParser(description="D4 Live vs Backtest Comparator")
    p.add_argument("--db-path", type=str, default=None,
                   help="Path to paper_trading.sqlite3 (default: ROOT/aurum1/data/paper_trading.sqlite3)")
    p.add_argument("--output", type=str, default=None,
                   help="Save report JSON to this path")
    p.add_argument("--run-backtest", action="store_true",
                   help="Also run 11-year backtest for R-distribution comparison (slow: ~90s)")
    args = p.parse_args()

    if args.db_path:
        db_path = Path(args.db_path)
    else:
        db_path = ROOT / "aurum1" / "data" / "paper_trading.sqlite3"

    print(f"\n  Loading paper trades from: {db_path}")
    trades = load_trades_from_db(db_path)
    print(f"  Found {len(trades)} completed trades")

    backtest_r = None
    if args.run_backtest:
        print("\n  Computing backtest R-distribution...")
        backtest_r = run_full_backtest_for_r_distribution()
        print(f"  Computed {len(backtest_r)} backtest trade outcomes")

    report = generate_report(trades, D4_BASELINE, backtest_r)
    print_report(report)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        report_clean = dict(report)
        # Strip raw R-values from output to keep file manageable
        if "r_values" in report_clean.get("live", {}):
            del report_clean["live"]["r_values"]
        out_path.write_text(json.dumps(report_clean, indent=2, default=str))
        print(f"  Report saved to: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
