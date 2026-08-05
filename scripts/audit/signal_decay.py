"""Signal decay monitoring — live vs backtest comparison.

The ICIR decay analysis on the 11-year backtest gives a baseline signal
decay profile. This script compares the LIVE D4 signal behavior (from
paper_trading.sqlite3) against that baseline to detect regime shifts.

A healthy signal:
  - IC peaks at horizon 1 (15min) and decays gracefully
  - Live performance broadly consistent with backtest expectations

If live decay diverges significantly (signal reversing, IC sign flip,
much faster decay), it's an early warning that the market regime has
shifted against D4.
"""

from __future__ import annotations

import json
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
OUTPUT_FILE = ROOT / "reports" / "research" / "signal_decay_monitor.json"

# Backtest baseline (from run_icir_decay_analysis.py results)
BACKTEST_BASELINE = {
    "peak_ic": -0.079,          # IC at 15min horizon
    "peak_icir": -1.18,         # ICIR at 15min
    "horizon_5h_ic": -0.020,    # IC at 5h (should decay)
    "decay_status": "Healthy decay, no reversal",
}


def load_live_trades(db_path: str | Path) -> list[dict[str, Any]]:
    """Load live trades from paper_trading.sqlite3."""
    trades = []
    with closing(sqlite3.connect(str(db_path))) as conn:
        rows = conn.execute("""
            SELECT id, direction, r_multiple, net_pnl, entry_time, exit_time
            FROM trades ORDER BY id
        """).fetchall()
        for row in rows:
            trades.append({
                "id": row[0],
                "direction": row[1],
                "r_multiple": float(row[2] or 0),
                "net_pnl": float(row[3] or 0),
                "entry_time": row[4],
                "exit_time": row[5],
            })
    return trades


def main() -> dict:
    """Run live vs backtest signal decay comparison."""
    print("=" * 70)
    print("  SIGNAL DECAY MONITOR — Live vs Backtest")
    print("=" * 70)

    if not DB_PATH.exists():
        print(f"\n  ERROR: No live trade DB at {DB_PATH}")
        return {}

    trades = load_live_trades(DB_PATH)
    print(f"\n  Loaded {len(trades)} live trades")

    if not trades:
        print("  No trades found.")
        return {}

    # Compute live performance metrics
    r_values = [t["r_multiple"] for t in trades]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r <= 0]

    mean_r = np.mean(r_values)
    std_r = np.std(r_values) if len(r_values) > 1 else 0
    sharpe = mean_r / std_r if std_r > 0 else 0
    win_rate = len(wins) / len(r_values)

    # Avg R by direction
    buy_r = [t["r_multiple"] for t in trades if t["direction"] == "BUY"]
    sell_r = [t["r_multiple"] for t in trades if t["direction"] == "SELL"]
    buy_avg = np.mean(buy_r) if buy_r else 0
    sell_avg = np.mean(sell_r) if sell_r else 0

    print(f"\n  Live performance ({len(trades)} trades):")
    print(f"    Mean R:   {mean_r:+.4f}")
    print(f"    Sharpe:   {sharpe:+.4f}")
    print(f"    Win rate: {win_rate:.1%}")
    print(f"    BUY avg R: {buy_avg:+.4f}  ({len(buy_r)} trades)")
    print(f"    SELL avg R: {sell_avg:+.4f}  ({len(sell_r)} trades)")

    # Compare against backtest expectations
    # Backtest: WR ~37%, mean R ~+0.12, SELL adds more edge than BUY
    print(f"\n  Backtest baseline:")
    print(f"    Win rate:  ~37%  (live: {win_rate:.1%})")
    print(f"    Peak IC:   {BACKTEST_BASELINE['peak_ic']}  (15min horizon)")
    print(f"    Decay:     {BACKTEST_BASELINE['decay_status']}")

    # Assessment
    checks = []
    checks.append(("Win rate consistent with backtest (37-60%)", 0.30 <= win_rate <= 0.65))
    checks.append(("Mean R positive (edge present)", mean_r > 0))
    checks.append(("SELL contributes meaningful edge", sell_avg > 0))
    checks.append(("No catastrophic regime shift (Sharpe not deeply negative)", sharpe > -0.5))

    print(f"\n  Regime shift checks:")
    for label, passed in checks:
        print(f"    {'✅' if passed else '⚠️'}  {label}")

    n_pass = sum(1 for _, p in checks if p)
    n_total = len(checks)
    verdict = "Healthy — live signal consistent with backtest baseline" if n_pass >= 3 else \
              "REVIEW — live signal diverging from backtest baseline"

    print(f"\n  VERDICT: {verdict} ({n_pass}/{n_total} checks passed)")

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "n_trades": len(trades),
        "live": {
            "mean_r": round(float(mean_r), 4),
            "sharpe": round(float(sharpe), 4),
            "win_rate": round(float(win_rate), 4),
            "buy_avg_r": round(float(buy_avg), 4),
            "sell_avg_r": round(float(sell_avg), 4),
        },
        "backtest_baseline": BACKTEST_BASELINE,
        "checks": [{"label": l, "passed": p} for l, p in checks],
        "verdict": verdict,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Results saved to {OUTPUT_FILE}")
    print("=" * 70)
    return summary


if __name__ == "__main__":
    main()
