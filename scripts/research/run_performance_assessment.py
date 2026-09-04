"""Live performance assessment for D4 — does under-risking need addressing?

Answers two questions with real data from paper_trading.sqlite3:

  1. How is the system ACTUALLY performing? (edge quality, not dollar size)
     - Per-trade R stats: mean R, WR, PF, per-trade Sharpe
     - Daily-return Sharpe from account snapshots (equity curve)
     - Max drawdown (snapshot-derived)
     - Statistical confidence: t-stat / 95% CI that mean R > 0

  2. Does the under-risking finding (running ~0.17% actual vs 0.35% configured,
     0.0875% Kelly intent) need addressing?
     Key insight: R-distribution is SCALE-INVARIANT. Risk sizing converts R into
     dollars but does not change mean R, WR, PF, or Sharpe. So under-risking does
     NOT degrade edge quality — it only means smaller absolute $ per trade.
     Whether to lift risk is therefore a decision about:
       (a) statistical confidence the edge is real (else staying small is rational),
       (b) how much absolute $ we are leaving on the table,
       (c) drawdown budget (Monte Carlo: each doubling of risk ~doubles DD).

The verdict logic is honest about the 148-trade sample (DSR gate target is 200).
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "aurum1" / "data" / "paper_trading.sqlite3"
OUTPUT_FILE = ROOT / "reports" / "research" / "performance_assessment.json"

INITIAL_EQUITY = 10000.0
CONFIG_RISK_PCT = 0.0035     # settings.yaml
KELLY_CAP = 0.25             # kelly_max_fraction
KELLY_INTENT = CONFIG_RISK_PCT * KELLY_CAP   # 0.0875%


def t_stat(mean_r: float, std_r: float, n: int) -> float:
    if n < 2 or std_r <= 0:
        return 0.0
    return mean_r / (std_r / math.sqrt(n))


def main() -> dict:
    print("=" * 78)
    print("  D4 LIVE PERFORMANCE ASSESSMENT — does under-risking need fixing?")
    print("=" * 78)

    with closing(sqlite3.connect(str(DB_PATH))) as conn:
        trades = conn.execute(
            "SELECT entry_time, exit_time, direction, units, risk_amount, "
            "r_multiple, net_pnl, exit_reason FROM trades ORDER BY id"
        ).fetchall()
        snaps = conn.execute(
            "SELECT timestamp, equity FROM account_snapshots ORDER BY id"
        ).fetchall()

    n = len(trades)
    print(f"\n  {n} trades, {len(snaps)} equity snapshots")
    if n == 0:
        return {}

    r_vals = np.array([float(t[5] or 0) for t in trades], dtype=float)
    pnls = np.array([float(t[6] or 0) for t in trades], dtype=float)
    risk_amts = np.array([float(t[4] or 0) for t in trades], dtype=float)
    units = np.array([float(t[3] or 0) for t in trades], dtype=float)

    mean_r = float(np.mean(r_vals))
    std_r = float(np.std(r_vals, ddof=1))
    wins = r_vals[r_vals > 0]
    losses = r_vals[r_vals <= 0]
    wr = len(wins) / n
    pf = float(np.sum(wins) / abs(np.sum(losses))) if np.sum(losses) != 0 else float("inf")
    per_trade_sharpe = mean_r / std_r if std_r > 0 else 0.0
    ts = t_stat(mean_r, std_r, n)
    # 95% CI on mean R
    se = std_r / math.sqrt(n) if n > 1 else 0.0
    ci_lo, ci_hi = mean_r - 1.96 * se, mean_r + 1.96 * se

    # Actual risk deployed (reconstruct equity-before per trade)
    live_equity = INITIAL_EQUITY
    risk_pcts = []
    equity_at_trade = []
    for t in trades:
        risk_amt = float(t[4] or 0)
        risk_pcts.append(risk_amt / live_equity * 100.0 if live_equity > 0 else 0.0)
        equity_at_trade.append(live_equity)
        live_equity += float(t[6] or 0)
    avg_risk_pct = float(np.mean(risk_pcts))

    # Daily returns from snapshots -> daily Sharpe, real DD
    eq = pd.Series([float(s[1]) for s in snaps],
                   index=pd.to_datetime([s[0] for s in snaps], utc=True))
    daily = eq.resample("1D").last().ffill().pct_change().dropna()
    # Market-open only ~5d/week; annualize by sqrt(365*24/24?) — use sqrt(252) convention for daily
    daily_sharpe = float(np.mean(daily) / np.std(daily, ddof=1)) if len(daily) > 1 and np.std(daily, ddof=1) > 0 else 0.0
    ann_sharpe = daily_sharpe * math.sqrt(252)
    peak = eq.cummax()
    dd = ((peak - eq) / peak * 100.0)
    max_dd_pct = float(dd.max())

    # What the system SHOULD have made at full 0.35% (no Kelly cap, fractional sizing)
    # Compare observed vs hypothetical to quantify "money left on table"
    obs_net = float(np.sum(pnls))
    # At 0.35% x Kelly 0.25 = 0.0875% intent but floor forces ~0.17%; model already shows.
    # Simple scaling: dollars scale with risk fraction. If we'd run at 0.35% flat:
    hypot_0_35 = obs_net * (CONFIG_RISK_PCT / (avg_risk_pct / 100.0))

    print(f"\n  --- EDGE QUALITY (scale-invariant) ---")
    print(f"    Mean R:        {mean_r:+.4f}   [95% CI {ci_lo:+.4f} to {ci_hi:+.4f}]")
    print(f"    t-stat (H0: R=0): {ts:+.2f}   {'SIGNIFICANT' if abs(ts) >= 1.96 else 'NOT significant'} at 5%")
    print(f"    Std R:         {std_r:.4f}")
    print(f"    Per-trade Sharpe: {per_trade_sharpe:+.3f}")
    print(f"    Win rate:      {wr*100:.1f}%")
    print(f"    Profit factor: {pf:.3f}")
    print(f"    Daily Sharpe:  {daily_sharpe:+.3f}   (annualized {ann_sharpe:+.2f})")
    print(f"    Max drawdown:  {max_dd_pct:.2f}%  (snapshot-derived)")

    print(f"\n  --- RISK DEPLOYED ---")
    print(f"    Configured:    {CONFIG_RISK_PCT*100:.2f}% / trade")
    print(f"    Kelly intent:  {KELLY_INTENT*100:.3f}%  (0.35% x 0.25 cap)")
    print(f"    ACTUAL avg:    {avg_risk_pct:.3f}% / trade   (min {min(risk_pcts):.3f} / max {max(risk_pcts):.3f})")
    print(f"    1-unit floor:  {sum(1 for t in trades if int(t[3] or 0)==1)}/{n} trades are exactly 1 unit")

    # Interpret under-risking
    print(f"\n  --- DOES UNDER-RISKING NEED ADDRESSING? ---")
    print(f"    Edge is {'real and ' if abs(ts) >= 1.96 else 'NOT yet statistically proven '}(t={ts:+.2f}, n={n})")
    print(f"    Observed net:  ${obs_net:+.2f} over {n} trades at {avg_risk_pct:.2f}% avg risk")
    print(f"    Hypothetical @0.35% flat: ~${hypot_0_35:+,.0f} (same edge, more $ at 2x the DD per Monte Carlo)")

    # Verdict
    checks = []
    edge_real = abs(ts) >= 1.96
    checks.append(("Edge statistically real (|t|>=1.96)", edge_real))
    checks.append(("Mean R > 0", mean_r > 0))
    checks.append(("Max DD within 10% budget", max_dd_pct < 10.0))
    checks.append(("WR in backtest range (30-65%)", 0.30 <= wr <= 0.65))
    checks.append(("Running BELOW Kelly intent is impossible (floor > intent)", avg_risk_pct > KELLY_INTENT * 100))

    if not edge_real:
        verdict = ("NOT YET — stay conservative. Under-risking is currently FINE (even good): "
                   "at {:.0f} trades the edge (t={:+.2f}) is not statistically distinguishable from "
                   "noise, so deploying more risk is not yet justified. Finish the 200-trade DSR gate "
                   "first.".format(n, ts))
    else:
        verdict = ("EDGE LOOKS REAL but sample is modest ({:} trades, t={:+.2f}). Under-risking is "
                   "'leaving money on the table' ONLY in the sense that the same edge at 0.35% would "
                   "make ~2x the dollars at ~2x the drawdown. That is a deliberate risk-budget decision, "
                   "NOT a bug. The 0.35% config never fires because Kelly caps it at 0.0875% and the "
                   "1-unit floor pushes it to ~0.17%. If more $ is desired, raise the lever AFTER the "
                   "200-trade gate confirms DSR; until then current sizing is defensible.".format(n, ts))

    print(f"\n  Checks:")
    for label, passed in checks:
        print(f"    {'OK ' if passed else '-- '} {label}")
    print(f"\n  VERDICT: {verdict}")

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "n_trades": n,
        "edge_quality": {
            "mean_r": round(mean_r, 4),
            "mean_r_ci_95": [round(ci_lo, 4), round(ci_hi, 4)],
            "t_stat": round(ts, 3),
            "std_r": round(std_r, 4),
            "per_trade_sharpe": round(per_trade_sharpe, 4),
            "win_rate": round(wr, 4),
            "profit_factor": round(pf, 4),
            "daily_sharpe": round(daily_sharpe, 4),
            "ann_sharpe": round(ann_sharpe, 4),
            "max_dd_pct": round(max_dd_pct, 3),
        },
        "risk_deployed": {
            "configured_pct": CONFIG_RISK_PCT * 100,
            "kelly_intent_pct": KELLY_INTENT * 100,
            "actual_avg_pct": round(avg_risk_pct, 3),
            "actual_min_pct": round(min(risk_pcts), 3),
            "actual_max_pct": round(max(risk_pcts), 3),
            "pct_1unit_trades": round(sum(1 for t in trades if int(t[3] or 0) == 1) / n * 100, 1),
        },
        "money_left_on_table": {
            "observed_net": round(obs_net, 2),
            "hypothetical_at_035_flat": round(hypot_0_35, 0),
        },
        "verdict": verdict,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Results saved to {OUTPUT_FILE}")
    print("=" * 78)
    return summary


if __name__ == "__main__":
    main()
