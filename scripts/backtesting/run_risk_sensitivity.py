"""Risk sensitivity analysis for D4 strategy.

Replays the 8,178-trade R-multiple distribution at different risk-per-trade
levels to map the relationship between position sizing and drawdown risk.

Usage: python scripts/run_risk_sensitivity.py
"""

from __future__ import annotations
import json, math, sys
from collections import Counter
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

LOOKBACK = 20
SL_MULT = 2.0
R_MULT = 2.0
SLIP_PIPS = 0.5
NUM_SIMS = 10000
INITIAL_EQ = 10000.0
RISK_LEVELS = [0.001, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02]

settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
spec = InstrumentSpec.from_settings(settings)
SLIP_DIST = SLIP_PIPS * spec.pip_size

print("Loading 11-year M15 data...")
ohlcv = load_ohlcv("M15", ROOT / "aurum1" / "data" / "backtest_market_cache.sqlite3")
print(f"  {len(ohlcv)} candles ({ohlcv.index[0].date()} to {ohlcv.index[-1].date()})")

features = build_research_features(ohlcv)

print("Running D4 backtest to extract trade distribution...")
buy_mask = features["close"] > features["high"].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
sell_mask = features["close"] < features["low"].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
valid = features["atr_14"].notna()
buy_mask = buy_mask & valid; sell_mask = sell_mask & valid

entries = {}
for direction, mask in [("BUY", buy_mask), ("SELL", sell_mask)]:
    for sig_ts in features.index[mask.fillna(False)]:
        sig_bar = ohlcv.index.get_loc(sig_ts)
        entry_bar = sig_bar + 1
        if entry_bar >= len(ohlcv): continue
        ep = float(ohlcv.iloc[entry_bar]["open"])
        atr_val = float(features.loc[sig_ts, "atr_14"])
        if not math.isfinite(atr_val) or atr_val <= 0: continue
        stop = ep - SL_MULT * atr_val if direction == "BUY" else ep + SL_MULT * atr_val
        if (direction == "BUY" and stop >= ep) or (direction == "SELL" and stop <= ep): continue
        risk_dist = abs(ep - stop)
        entries.setdefault(entry_bar, []).append({
            "d": direction, "entry": ep, "stop": stop,
            "risk_dist": risk_dist, "atr": atr_val
        })

equity = INITIAL_EQ
pos = None
trade_r_values = []

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
            spread_cost = 2.0 * 0.1 * spec.pip_value_per_unit * pos["units"]
            net = gross - spread_cost
            r_val = net / pos["risk_amount"] if pos["risk_amount"] > 0 else 0
            trade_r_values.append(r_val)
            equity += net; pos = None

    if pos is None and bar_idx in entries:
        for sig in entries[bar_idx]:
            if pos: break
            adj_entry = sig["entry"] + SLIP_DIST if sig["d"] == "BUY" else sig["entry"] - SLIP_DIST
            orig_r = sig["risk_dist"]
            stop_adj = adj_entry - orig_r if sig["d"] == "BUY" else adj_entry + orig_r
            target = adj_entry + R_MULT * orig_r if sig["d"] == "BUY" else adj_entry - R_MULT * orig_r
            risk_dollars = equity * 0.0025
            raw_units = max(1, int(risk_dollars / (orig_r * spec.ounces_per_unit))) if orig_r > 0 else 1
            risk_amt = orig_r * raw_units * spec.ounces_per_unit
            pos = {"entry_bar": bar_idx, "d": sig["d"], "entry": adj_entry, "stop": stop_adj, "target": target, "units": raw_units, "risk_amount": risk_amt}

if pos is not None and len(ohlcv):
    last_close = float(ohlcv.iloc[-1]["close"])
    gross = spec.pnl(pos["d"], pos["entry"], last_close, pos["units"])
    spread_cost = 2.0 * 0.1 * spec.pip_value_per_unit * pos["units"]
    net = gross - spread_cost
    r_val = net / pos["risk_amount"] if pos["risk_amount"] > 0 else 0
    trade_r_values.append(r_val)

trade_r_values = np.array(trade_r_values, dtype=np.float64)
n_trades = len(trade_r_values)
print(f"  {n_trades} trades extracted")

# ========== RISK SENSITIVITY ==========
print(f"\n{'='*70}")
print(f"RISK SENSITIVITY ANALYSIS ({NUM_SIMS} SIMULATIONS PER LEVEL)")
print(f"{'='*70}")

headers = ["Risk/Trade", "Median DD", "95th DD", "99th DD", "Worst DD",
           "P(DD>10%)", "P(DD>20%)", "P(DD>30%)", "Median Streak",
           "95th Streak", "Worst Streak", "Median Return", "P(Ruin)"]
print(f"{'='*110}")
print(f"  {'':>10s}  {'MedDD':>7s}  {'95thDD':>7s}  {'99thDD':>7s}  {'WrstDD':>7s}  {'>10%':>6s}  {'>20%':>6s}  {'>30%':>6s}  {'MedStr':>7s}  {'95thStr':>7s}  {'WrstStr':>7s}  {'MedRet':>8s}  {'Ruin':>6s}")
print(f"{'='*110}")

results = []
base_r_values = trade_r_values.copy()

for risk_pct in RISK_LEVELS:
    risk_label = f"{risk_pct*100:.2f}%"
    final_eqs = np.zeros(NUM_SIMS, dtype=np.float64)
    max_dds = np.zeros(NUM_SIMS, dtype=np.float64)
    worst_streaks = np.zeros(NUM_SIMS, dtype=np.int32)
    ruined = np.zeros(NUM_SIMS, dtype=bool)

    for sim in range(NUM_SIMS):
        r_vals = base_r_values.copy()
        np.random.shuffle(r_vals)

        eq = INITIAL_EQ; peak = INITIAL_EQ; max_dd = 0.0
        streak = 0; worst_st = 0; is_loss = False

        for r in r_vals:
            pnl = r * eq * risk_pct
            eq += pnl
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100
            max_dd = max(max_dd, dd)

            if r <= 0:
                if not is_loss: is_loss = True; streak = 1
                else: streak += 1
                worst_st = max(worst_st, streak)
            else:
                is_loss = False; streak = 0

            if eq <= 0:
                ruined[sim] = True
                break

        final_eqs[sim] = eq
        max_dds[sim] = max_dd
        worst_streaks[sim] = worst_st

    sorted_dd = np.sort(max_dds)
    sorted_streaks = np.sort(worst_streaks)
    returns = (final_eqs / INITIAL_EQ - 1) * 100
    sorted_ret = np.sort(returns)

    med_dd = round(np.median(max_dds), 1)
    p95_dd = round(sorted_dd[int(NUM_SIMS*0.95)], 1)
    p99_dd = round(sorted_dd[int(NUM_SIMS*0.99)], 1)
    worst_dd = round(sorted_dd[-1], 1)
    p_over_10 = round((max_dds > 10).mean() * 100, 1)
    p_over_20 = round((max_dds > 20).mean() * 100, 1)
    p_over_30 = round((max_dds > 30).mean() * 100, 1)
    med_streak = int(np.median(worst_streaks))
    p95_streak = int(sorted_streaks[int(NUM_SIMS*0.95)])
    worst_streak = int(sorted_streaks[-1])
    med_ret = round(np.median(returns), 1)
    ruin_pct = round(ruined.mean() * 100, 2)

    print(f"  {risk_label:>10s}  {med_dd:>7.1f}%  {p95_dd:>7.1f}%  {p99_dd:>7.1f}%  {worst_dd:>7.1f}%  {p_over_10:>6.1f}%  {p_over_20:>6.1f}%  {p_over_30:>6.1f}%  {med_streak:>7d}  {p95_streak:>7d}  {worst_streak:>7d}  {med_ret:>8.1f}%  {ruin_pct:>6.2f}%")

    results.append({
        "risk_per_trade_pct": risk_pct * 100,
        "median_dd_pct": med_dd,
        "95th_dd_pct": p95_dd,
        "99th_dd_pct": p99_dd,
        "worst_dd_pct": worst_dd,
        "p_dd_gt_10pct": p_over_10,
        "p_dd_gt_20pct": p_over_20,
        "p_dd_gt_30pct": p_over_30,
        "median_worst_streak": med_streak,
        "95th_worst_streak": p95_streak,
        "worst_streak": worst_streak,
        "median_return_pct": med_ret,
        "ruin_probability_pct": ruin_pct,
    })

print(f"{'─'*110}")

# Save
output = {
    "parameters": {
        "lookback": LOOKBACK,
        "num_simulations_per_level": NUM_SIMS,
        "initial_equity": INITIAL_EQ,
        "n_trades": n_trades,
    },
    "risk_levels": results,
    "generated_at": datetime.now(UTC).isoformat(),
}

out_path = ROOT / "reports" / "forward_shadow" / "risk_sensitivity_d4_results.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(output, indent=2))
print(f"\nSaved to {out_path}")
print("DONE")
