"""Monte Carlo simulation for D4 strategy — reshuffle + regime-aware.

Runs the full 11-year D4 backtest, extracts the trade distribution (R-multiples),
then runs Monte Carlo in two modes:
  1. Reshuffle (legacy): shuffles individual trades — breaks serial correlation
  2. Regime-aware (new):   bootstraps contiguous same-regime blocks — preserves
     the clustered loss patterns that cause drawdowns

Usage:
  python scripts/backtesting/run_monte_carlo.py                     # both modes
  python scripts/backtesting/run_monte_carlo.py --mode reshuffle    # legacy only
  python scripts/backtesting/run_monte_carlo.py --mode regime       # new only
"""

from __future__ import annotations
import argparse, json, math, sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.backtesting.monte_carlo import (
    run_monte_carlo,
    regime_block_bootstrap,
    MonteCarloResult,
    RegimeAwareMCResult,
)
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research.research_edge_prototypes import build_research_features

LOOKBACK = 20
SL_MULT = 2.0
R_MULT = 2.0
RISK_PCT = 0.0025
SLIP_PIPS = 0.5
NUM_SIMS = 10000
INITIAL_EQ = 10000.0

settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
spec = InstrumentSpec.from_settings(settings)
SP_1 = 0.1  # estimated average spread in pips
SLIP_DIST = SLIP_PIPS * spec.pip_size
PIP_VAL = spec.pip_value_per_unit

print("Loading 11-year M15 data...")
ohlcv = load_ohlcv("M15", ROOT / "aurum1" / "data" / "backtest_market_cache.sqlite3")
print(f"  {len(ohlcv)} candles ({ohlcv.index[0].date()} to {ohlcv.index[-1].date()})")

print("Building features...")
features = build_research_features(ohlcv)

print("Running D4 backtest...")
buy_mask = features["close"] > features["high"].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
sell_mask = features["close"] < features["low"].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
valid = features["atr_14"].notna()
buy_mask = buy_mask & valid; sell_mask = sell_mask & valid

# Build entry signals: bar_idx -> list of signals with regime labels
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
        risk_dist = abs(ep - stop)
        # Regime label: ADX + EMA alignment at signal time (same logic as the library)
        adx = float(features.loc[sig_ts, "adx_14"]) if "adx_14" in features.columns else 0
        ema9 = float(features.loc[sig_ts, "ema_9"]) if "ema_9" in features.columns else 0
        ema20 = float(features.loc[sig_ts, "ema_20"]) if "ema_20" in features.columns else 0
        ema50 = float(features.loc[sig_ts, "ema_50"]) if "ema_50" in features.columns else 0
        ema_alignment = sum([1 if ema9 > ema20 else -1, 1 if ema20 > ema50 else -1])
        if adx > 25 and ema_alignment >= 2:
            regime = "TRENDING_UP"
        elif adx > 25 and ema_alignment <= -2:
            regime = "TRENDING_DOWN"
        else:
            regime = "RANGING"
        entries.setdefault(entry_bar, []).append({
            "d": direction, "entry": ep, "stop": stop,
            "risk_dist": risk_dist, "atr": atr_val,
            "regime": regime,
        })

# Run through all bars
equity = INITIAL_EQ
pos = None
trade_r_values: list[float] = []
trade_reasons: list[str] = []
trade_directions: list[str] = []
trade_regimes: list[str] = []

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
            spread_cost = 2.0 * SP_1 * PIP_VAL * pos["units"]
            net = gross - spread_cost
            r_val = net / pos["risk_amount"] if pos["risk_amount"] > 0 else 0
            trade_r_values.append(r_val)
            trade_reasons.append(reason)
            trade_directions.append(pos["d"])
            trade_regimes.append(pos.get("regime", "RANGING"))
            equity += net
            pos = None

    if pos is None and bar_idx in entries:
        for sig in entries[bar_idx]:
            if pos: break
            adj_entry = sig["entry"] + SLIP_DIST if sig["d"] == "BUY" else sig["entry"] - SLIP_DIST
            orig_r = sig["risk_dist"]
            stop_adj = adj_entry - orig_r if sig["d"] == "BUY" else adj_entry + orig_r
            target = adj_entry + R_MULT * orig_r if sig["d"] == "BUY" else adj_entry - R_MULT * orig_r
            risk_dollars = equity * RISK_PCT
            raw_units = max(1, int(risk_dollars / (orig_r * spec.ounces_per_unit))) if orig_r > 0 else 1
            risk_amt = orig_r * raw_units * spec.ounces_per_unit
            pos = {"entry_bar": bar_idx, "d": sig["d"], "entry": adj_entry,
                   "stop": stop_adj, "target": target, "units": raw_units,
                   "risk_amount": risk_amt, "regime": sig.get("regime", "RANGING")}

# Flush last position
if pos is not None and len(ohlcv):
    last_close = float(ohlcv.iloc[-1]["close"])
    gross = spec.pnl(pos["d"], pos["entry"], last_close, pos["units"])
    spread_cost = 2.0 * SP_1 * PIP_VAL * pos["units"]
    net = gross - spread_cost
    r_val = net / pos["risk_amount"] if pos["risk_amount"] > 0 else 0
    trade_r_values.append(r_val)
    trade_reasons.append("end_of_data")
    trade_directions.append(pos["d"])
    trade_regimes.append(pos.get("regime", "RANGING"))
    equity += net

trade_r_values_arr = np.array(trade_r_values, dtype=np.float64)
n_trades = len(trade_r_values_arr)
wins = trade_r_values_arr > 0
losses = trade_r_values_arr <= 0
n_wins = int(wins.sum())
n_losses = int(losses.sum())

# Compute streak stats
max_consec_wins = 0
max_consec_losses = 0
current_streak = 0
current_type = None
for r in trade_r_values_arr:
    is_win = r > 0
    if current_type is None or is_win != current_type:
        current_type = is_win
        current_streak = 1
    else:
        current_streak += 1
    if is_win:
        max_consec_wins = max(max_consec_wins, current_streak)
    else:
        max_consec_losses = max(max_consec_losses, current_streak)

exit_counts = Counter(trade_reasons)

# Parse args
parser = argparse.ArgumentParser(description="D4 Monte Carlo simulation")
parser.add_argument("--mode", choices=["reshuffle", "regime", "both"], default="both",
                    help="MC mode: reshuffle (legacy), regime (block bootstrap), or both")
args = parser.parse_args()

# ========== BACKTEST SUMMARY ==========
print(f"\n{'='*70}")
print(f"BACKTEST SUMMARY")
print(f"{'='*70}")
print(f"Total trades: {n_trades}")
print(f"Wins: {n_wins} ({n_wins/n_trades*100:.1f}%)" if n_trades else "Wins: N/A")
print(f"Losses: {n_losses} ({n_losses/n_trades*100:.1f}%)" if n_trades else "Losses: N/A")
avg_r_win = trade_r_values_arr[wins].mean() if n_wins > 0 else float("nan")
avg_r_loss = trade_r_values_arr[losses].mean() if n_losses > 0 else float("nan")
print(f"Avg R (win): {avg_r_win:.4f}")
print(f"Avg R (loss): {avg_r_loss:.4f}")
print(f"Net R: {trade_r_values_arr.sum():.2f}")
pf_backtest = trade_r_values_arr[wins].sum() / abs(trade_r_values_arr[losses].sum()) if n_losses > 0 else float("inf")
print(f"Profit Factor: {pf_backtest:.4f}")
print(f"Final equity: ${equity:.2f}")
print(f"Return: {(equity/INITIAL_EQ - 1)*100:.2f}%")
print(f"Max consecutive wins: {max_consec_wins}")
print(f"Max consecutive losses: {max_consec_losses}")
print(f"Regime distribution: {dict(Counter(trade_regimes))}")
for reason, count in exit_counts.most_common():
    print(f"  {reason}: {count}")

# Build trade dicts for library functions
trade_dicts: list[dict[str, Any]] = [
    {
        "pnl": float(trade_r_values[i] * INITIAL_EQ * RISK_PCT),
        "net_pnl": float(trade_r_values[i] * INITIAL_EQ * RISK_PCT),
        "r_multiple": float(trade_r_values[i]),
        "regime": str(trade_regimes[i]),
        "direction": str(trade_directions[i]),
    }
    for i in range(n_trades)
]
del trade_r_values, trade_reasons, trade_directions, trade_regimes, trade_r_values_arr

# ========== RESHUFFLE MODE (Legacy) ==========
results: dict[str, Any] = {}
if args.mode in ("reshuffle", "both"):
    print(f"\n{'='*70}")
    print(f"MONTE CARLO — RESHUFFLE MODE ({NUM_SIMS} SIMULATIONS)")
    print(f"{'='*70}")
    reshuffle_result: MonteCarloResult = run_monte_carlo(
        trade_dicts, n_simulations=NUM_SIMS, initial_equity=INITIAL_EQ,
    )
    results["reshuffle"] = {
        "n_simulations": reshuffle_result.n_simulations,
        "final_equity": {
            "median": round(reshuffle_result.median_final_equity, 2),
            "pct5": round(reshuffle_result.pct5_final_equity, 2),
            "pct95": round(reshuffle_result.pct95_final_equity, 2),
        },
        "max_drawdown_pct": {
            "median": round(reshuffle_result.median_max_drawdown, 2),
            "pct95": round(reshuffle_result.pct95_max_drawdown, 2),
        },
        "sharpe": {
            "median": round(reshuffle_result.median_sharpe, 2),
            "pct5": round(reshuffle_result.pct5_sharpe, 2),
        },
        "ruin_probability_pct": round(reshuffle_result.ruin_probability * 100, 2),
    }
    print(f"  Median final equity: ${reshuffle_result.median_final_equity:.2f}")
    print(f"  5th percentile final equity: ${reshuffle_result.pct5_final_equity:.2f}")
    print(f"  95th percentile final equity: ${reshuffle_result.pct95_final_equity:.2f}")
    print(f"  Median max drawdown: {reshuffle_result.median_max_drawdown:.2f}%")
    print(f"  95th percentile max drawdown: {reshuffle_result.pct95_max_drawdown:.2f}%")
    print(f"  Ruin probability: {reshuffle_result.ruin_probability*100:.2f}%")

# ========== REGIME-AWARE MODE (Block Bootstrap) ==========
if args.mode in ("regime", "both"):
    print(f"\n{'='*70}")
    print(f"MONTE CARLO — REGIME-AWARE BLOCK BOOTSTRAP ({NUM_SIMS} SIMULATIONS)")
    print(f"{'='*70}")
    print(f"Building contiguous regime blocks from {n_trades} trades...")
    regime_result: RegimeAwareMCResult = regime_block_bootstrap(
        trade_dicts, n_simulations=NUM_SIMS, initial_equity=INITIAL_EQ,
    )
    results["regime_aware"] = {
        "n_simulations": regime_result.n_simulations,
        "n_blocks": regime_result.n_blocks,
        "block_size": regime_result.block_size_distribution,
        "final_equity": {
            "median": round(regime_result.median_final_equity, 2),
            "pct5": round(regime_result.pct5_final_equity, 2),
            "pct95": round(regime_result.pct95_final_equity, 2),
        },
        "max_drawdown_pct": {
            "median": round(regime_result.median_max_drawdown, 2),
            "pct95": round(regime_result.pct95_max_drawdown, 2),
            "pct99": round(regime_result.pct99_max_drawdown, 2),
            "worst_observed": round(regime_result.worst_drawdown_observed, 2),
        },
        "drawdown_percentiles": regime_result.drawdown_percentiles,
        "sharpe": {
            "median": round(regime_result.median_sharpe, 2),
            "pct5": round(regime_result.pct5_sharpe, 2),
        },
        "ruin_probability_pct": round(regime_result.ruin_probability * 100, 2),
    }
    print(f"  Regime blocks: {regime_result.n_blocks} ({regime_result.block_size_distribution})")
    print(f"  Median final equity: ${regime_result.median_final_equity:.2f}")
    print(f"  5th percentile final equity: ${regime_result.pct5_final_equity:.2f}")
    print(f"  95th percentile final equity: ${regime_result.pct95_final_equity:.2f}")
    print(f"  Median max drawdown: {regime_result.median_max_drawdown:.2f}%")
    print(f"  95th percentile max drawdown: {regime_result.pct95_max_drawdown:.2f}%")
    print(f"  99th percentile max drawdown: {regime_result.pct99_max_drawdown:.2f}%")
    print(f"  Worst drawdown observed: {regime_result.worst_drawdown_observed:.2f}%")
    print(f"  Ruin probability: {regime_result.ruin_probability*100:.2f}%")

# ========== COMPARISON (if both modes ran) ==========
if "reshuffle" in results and "regime_aware" in results:
    print(f"\n{'='*70}")
    print("COMPARISON: RESHUFFLE vs REGIME-AWARE")
    print(f"{'='*70}")
    reshuffle_dd = results["reshuffle"]["max_drawdown_pct"]["pct95"]
    regime_dd = results["regime_aware"]["max_drawdown_pct"]["pct95"]
    ratio = regime_dd / reshuffle_dd if reshuffle_dd > 0 else float("nan")
    print(f"  95th percentile DD (reshuffle): {reshuffle_dd:.2f}%")
    print(f"  95th percentile DD (regime):    {regime_dd:.2f}%")
    print(f"  DD ratio (regime/reshuffle):    {ratio:.2f}x")
    print(f"  (Regime-aware should be 1.5-2.0x if losses cluster)")

# ========== SAVE ==========
output = {
    "backtest": {
        "n_trades": n_trades,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "win_rate_pct": round(n_wins / n_trades * 100, 2) if n_trades else 0.0,
        "net_r": round(float(sum(t["r_multiple"] for t in trade_dicts)), 2),
        "max_consecutive_wins": max_consec_wins,
        "max_consecutive_losses": max_consec_losses,
        "exit_breakdown": dict(exit_counts),
        "final_equity": round(equity, 2),
        "return_pct": round((equity / INITIAL_EQ - 1) * 100, 2),
    },
    "results": results,
    "config": {
        "lookback": LOOKBACK,
        "sl_mult": SL_MULT,
        "r_mult": R_MULT,
        "risk_pct": RISK_PCT,
        "slip_pips": SLIP_PIPS,
        "n_simulations": NUM_SIMS,
        "initial_equity": INITIAL_EQ,
    },
    "generated_at": datetime.now(UTC).isoformat(),
}

out_path = ROOT / "reports" / "monte_carlo_d4_results.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(output, indent=2))
print(f"\nSaved to {out_path}")
print(f"\n{'='*70}")
print("DONE")
