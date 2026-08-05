"""Determinism audit for the D4 backtest.

Runs the full D4 backtest twice with identical inputs and compares outputs.
Any divergence indicates unseeded randomness, which would make backtest
results non-reproducible and therefore untrustworthy.

Checks:
  1. Same trade count
  2. Same trade R-multiples (exact match)
  3. Same final equity
  4. Same trade directions and exits
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research.research_edge_prototypes import build_research_features

MARKET_DB = ROOT / "aurum1" / "data" / "backtest_market_cache.sqlite3"
OUTPUT_FILE = ROOT / "reports" / "research" / "determinism_audit.json"

LOOKBACK = 20
RISK_PCT = 0.0025
SL_MULT = 2.0
R_MULT = 2.0
SLIP_DIST = 0.5 * 0.01  # 0.5 pips slippage
SP_1 = 1.5  # spread pips


def run_d4_backtest(seed: int = 42) -> dict:
    """Run the D4 backtest and return trade results + final equity."""
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    spec = InstrumentSpec.from_settings(settings)

    ohlcv = load_ohlcv("M15", MARKET_DB)
    features = build_research_features(ohlcv)

    buy_mask = features["close"] > features["high"].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
    sell_mask = features["close"] < features["low"].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
    valid = features["atr_14"].notna()
    buy_mask = buy_mask & valid
    sell_mask = sell_mask & valid

    # Build entries
    entries = {}
    for direction, mask in [("BUY", buy_mask), ("SELL", sell_mask)]:
        for sig_ts in features.index[mask.fillna(False)]:
            sig_bar = ohlcv.index.get_loc(sig_ts)
            entry_bar = sig_bar + 1
            if entry_bar >= len(ohlcv):
                continue
            ep = float(ohlcv.iloc[entry_bar]["open"])
            atr_val = float(features.loc[sig_ts, "atr_14"])
            if not math.isfinite(atr_val) or atr_val <= 0:
                continue
            stop = ep - SL_MULT * atr_val if direction == "BUY" else ep + SL_MULT * atr_val
            if (direction == "BUY" and stop >= ep) or (direction == "SELL" and stop <= ep):
                continue
            entries.setdefault(entry_bar, []).append({
                "d": direction, "entry": ep, "stop": stop,
                "risk_dist": abs(ep - stop),
            })

    equity = 10000.0
    pos = None
    trades = []

    for bar_idx in range(len(ohlcv)):
        row = ohlcv.iloc[bar_idx]
        o, h, l = float(row["open"]), float(row["high"]), float(row["low"])

        if pos is not None and bar_idx > pos["entry_bar"]:
            ex_price, reason = None, None
            if pos["d"] == "BUY":
                if o <= pos["stop"]: ex_price, reason = o, "stop_loss_gap"
                elif l <= pos["stop"]: ex_price, reason = pos["stop"], "stop_loss"
                elif h >= pos["target"]: ex_price, reason = pos["target"], "take_profit"
            else:
                if o >= pos["stop"]: ex_price, reason = o, "stop_loss_gap"
                elif h >= pos["stop"]: ex_price, reason = pos["stop"], "stop_loss"
                elif l <= pos["target"]: ex_price, reason = pos["target"], "take_profit"

            if ex_price and reason:
                actual_exit = ex_price - SLIP_DIST if pos["d"] == "BUY" else ex_price + SLIP_DIST
                gross = spec.pnl(pos["d"], pos["entry"], actual_exit, pos["units"])
                spread_cost = 2.0 * SP_1 * spec.pip_value_per_unit * pos["units"]
                net = gross - spread_cost
                r_val = net / pos["risk_amount"] if pos["risk_amount"] > 0 else 0
                trades.append({
                    "d": pos["d"], "r": round(r_val, 8), "reason": reason,
                })
                equity += net
                pos = None

        if pos is None and bar_idx in entries:
            for sig in entries[bar_idx]:
                if pos:
                    break
                adj_entry = sig["entry"] + SLIP_DIST if sig["d"] == "BUY" else sig["entry"] - SLIP_DIST
                orig_r = sig["risk_dist"]
                stop_adj = adj_entry - orig_r if sig["d"] == "BUY" else adj_entry + orig_r
                target = adj_entry + R_MULT * orig_r if sig["d"] == "BUY" else adj_entry - R_MULT * orig_r
                risk_dollars = equity * RISK_PCT
                raw_units = max(1, int(risk_dollars / (orig_r * spec.ounces_per_unit))) if orig_r > 0 else 1
                risk_amt = orig_r * raw_units * spec.ounces_per_unit
                pos = {"entry_bar": bar_idx, "d": sig["d"], "entry": adj_entry,
                       "stop": stop_adj, "target": target, "units": raw_units,
                       "risk_amount": risk_amt}

    return {"trades": trades, "final_equity": round(equity, 8), "n_trades": len(trades)}


def fingerprint(result: dict) -> str:
    """Create a deterministic fingerprint of a backtest result."""
    trades = result["trades"]
    # Hash the full trade sequence (direction + r + reason)
    h = hashlib.sha256()
    for t in trades:
        h.update(f"{t['d']}|{t['r']}|{t['reason']};".encode())
    return h.hexdigest()


def main() -> dict:
    """Run the determinism audit."""
    print("=" * 70)
    print("  DETERMINISM AUDIT — D4 Backtest")
    print("=" * 70)

    print("\n  Running backtest run #1...")
    run1 = run_d4_backtest(seed=42)
    print(f"    {run1['n_trades']} trades, final equity ${run1['final_equity']:,.2f}")

    print("  Running backtest run #2...")
    run2 = run_d4_backtest(seed=42)
    print(f"    {run2['n_trades']} trades, final equity ${run2['final_equity']:,.2f}")

    # Compare
    fp1 = fingerprint(run1)
    fp2 = fingerprint(run2)

    n_same = run1["n_trades"] == run2["n_trades"]
    eq_same = run1["final_equity"] == run2["final_equity"]
    fp_same = fp1 == fp2

    print(f"\n  Trade count match:  {'✅' if n_same else '❌'} ({run1['n_trades']} vs {run2['n_trades']})")
    print(f"  Final equity match: {'✅' if eq_same else '❌'} (${run1['final_equity']} vs ${run2['final_equity']})")
    print(f"  Full fingerprint:   {'✅' if fp_same else '❌'} (deterministic)" if fp_same else
          f"  Full fingerprint:   ❌ DIVERGED")

    # Check trade-by-trade if fingerprints differ
    if not fp_same:
        print("\n  Locating first divergence...")
        for i, (t1, t2) in enumerate(zip(run1["trades"], run2["trades"])):
            if t1 != t2:
                print(f"    Trade {i}: run1={t1} vs run2={t2}")
                break
        else:
            if len(run1["trades"]) != len(run2["trades"]):
                print(f"    Trade count differs: {len(run1['trades'])} vs {len(run2['trades'])}")

    verdict = "PASS — D4 backtest is fully deterministic" if (n_same and eq_same and fp_same) else \
              "FAIL — nondeterminism detected (unseeded randomness)"

    print(f"\n  VERDICT: {verdict}")

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "n_trades_run1": run1["n_trades"],
        "n_trades_run2": run2["n_trades"],
        "final_equity_run1": run1["final_equity"],
        "final_equity_run2": run2["final_equity"],
        "fingerprint_match": fp_same,
        "verdict": verdict,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(summary, indent=2, default=str))
    print(f"  Results saved to {OUTPUT_FILE}")
    print("=" * 70)
    return summary


if __name__ == "__main__":
    main()
