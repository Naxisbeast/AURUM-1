"""Monte Carlo simulation for D4 strategy.

Runs the full 11-year D4 backtest, extracts the trade distribution (R-multiples),
then shuffles trades 10,000 times to estimate probability of ruin, expected max
drawdown, worst losing streak, and confidence intervals.

Usage: python scripts/run_monte_carlo.py
"""

from __future__ import annotations
import json, math, sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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

# Build entry signals: bar_idx → list of signals
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
        entries.setdefault(entry_bar, []).append({
            "d": direction, "entry": ep, "stop": stop,
            "risk_dist": risk_dist, "atr": atr_val
        })

# Run through all bars
equity = INITIAL_EQ
pos = None
trade_r_values = []   # list of R-multiples
trade_reasons = []
trade_directions = []

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
                   "risk_amount": risk_amt}

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
    equity += net

trade_r_values = np.array(trade_r_values, dtype=np.float64)
n_trades = len(trade_r_values)

print(f"\n=== BACKTEST RESULTS ===")
print(f"Total trades: {n_trades}")
wins = trade_r_values > 0
losses = trade_r_values <= 0
n_wins = int(wins.sum())
n_losses = int(losses.sum())
print(f"Wins: {n_wins} ({n_wins/n_trades*100:.1f}%)")
print(f"Losses: {n_losses} ({n_losses/n_trades*100:.1f}%)")
print(f"Avg R (win): {trade_r_values[wins].mean():.4f}" if n_wins > 0 else "Avg R (win): N/A")
print(f"Avg R (loss): {trade_r_values[losses].mean():.4f}" if n_losses > 0 else "Avg R (loss): N/A")
print(f"Net R: {trade_r_values.sum():.2f}")
print(f"Profit Factor: {trade_r_values[wins].sum() / abs(trade_r_values[losses].sum()):.4f}" if n_losses > 0 else "PF: inf")
print(f"Final equity: ${equity:.2f}")
print(f"Return: {(equity/INITIAL_EQ - 1)*100:.2f}%")

# Compute streak stats
max_consec_wins = 0
max_consec_losses = 0
current_streak = 0
current_type = None
for r in trade_r_values:
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

print(f"Max consecutive wins: {max_consec_wins}")
print(f"Max consecutive losses: {max_consec_losses}")

# Exit breakdown
exit_counts = Counter(trade_reasons)
for reason, count in exit_counts.most_common():
    print(f"  {reason}: {count}")

# ========== MONTE CARLO SIMULATION ==========
print(f"\n=== MONTE CARLO ({NUM_SIMS} SIMULATIONS) ===")
print(f"Shuffling {n_trades} trades...")

# Pre-allocate for speed
final_equities = np.zeros(NUM_SIMS, dtype=np.float64)
max_dd_pcts = np.zeros(NUM_SIMS, dtype=np.float64)
max_dd_dollars = np.zeros(NUM_SIMS, dtype=np.float64)
worst_streaks = np.zeros(NUM_SIMS, dtype=np.int32)
profit_factors = np.zeros(NUM_SIMS, dtype=np.float64)
max_risk_exposure = np.zeros(NUM_SIMS, dtype=np.float64)
trades_until_ruin = np.full(NUM_SIMS, n_trades, dtype=np.int32)
ruined = np.zeros(NUM_SIMS, dtype=bool)

RISK_PER_TRADE_DOLLARS = INITIAL_EQ * RISK_PCT

for sim in range(NUM_SIMS):
    if sim % 2000 == 0:
        print(f"  Simulation {sim}/{NUM_SIMS}...")

    np.random.shuffle(trade_r_values)

    eq = INITIAL_EQ
    peak = INITIAL_EQ
    max_dd = 0.0
    max_dd_dollar = 0.0
    streak = 0
    worst_streak_sim = 0
    is_losing_streak = False
    gwins = 0.0
    gloss = 0.0
    ru = False
    first_ruin_trade = n_trades

    for i, r in enumerate(trade_r_values):
        risk_amt = eq * RISK_PCT
        pnl = r * risk_amt  # R-multiple × current risk amount

        if r > 0:
            gwins += pnl
            if is_losing_streak:
                is_losing_streak = False
                streak = 0
        else:
            gloss += abs(pnl)
            if not is_losing_streak:
                is_losing_streak = True
                streak = 1
            else:
                streak += 1
            worst_streak_sim = max(worst_streak_sim, streak)

        eq += pnl
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100
        max_dd = max(max_dd, dd)
        max_dd_dollar = max(max_dd_dollar, peak - eq)

        if eq <= 0 and not ru:
            ru = True
            first_ruin_trade = i + 1
            break

    final_equities[sim] = eq
    max_dd_pcts[sim] = max_dd
    max_dd_dollars[sim] = max_dd_dollar
    worst_streaks[sim] = worst_streak_sim
    profit_factors[sim] = gwins / gloss if gloss > 0 else float('inf')
    max_risk_exposure[sim] = max(INITIAL_EQ - (peak - eq), INITIAL_EQ) / INITIAL_EQ * 100
    trades_until_ruin[sim] = first_ruin_trade
    ruined[sim] = ru

# ========== MONTE CARLO RESULTS ==========
print(f"\n{'='*70}")
print(f"MONTE CARLO RESULTS ({NUM_SIMS} SIMULATIONS)")
print(f"{'='*70}")

# Final equity statistics
sorted_eq = np.sort(final_equities)
print(f"\n--- FINAL EQUITY ---")
print(f"  Mean: ${np.mean(final_equities):.2f}")
print(f"  Median: ${np.median(final_equities):.2f}")
print(f"  Std: ${np.std(final_equities):.2f}")
print(f"  Best 5%: ${sorted_eq[int(NUM_SIMS*0.95)]:.2f}")
print(f"  Best 25%: ${sorted_eq[int(NUM_SIMS*0.75)]:.2f}")
print(f"  Worst 25%: ${sorted_eq[int(NUM_SIMS*0.25)]:.2f}")
print(f"  Worst 5%: ${sorted_eq[int(NUM_SIMS*0.05)]:.2f}")
print(f"  Worst 1%: ${sorted_eq[int(NUM_SIMS*0.01)]:.2f}")
print(f"  Worst 0.1%: ${sorted_eq[max(0, int(NUM_SIMS*0.001))]:.2f}")

# Return
returns = (final_equities / INITIAL_EQ - 1) * 100
sorted_returns = np.sort(returns)
print(f"\n--- RETURN ---")
print(f"  Mean: {np.mean(returns):.2f}%")
print(f"  Median: {np.median(returns):.2f}%")
print(f"  Std: {np.std(returns):.2f}%")
print(f"  Best: {sorted_returns[-1]:.2f}%")
print(f"  95th: {sorted_returns[int(NUM_SIMS*0.95)]:.2f}%")
print(f"  75th: {sorted_returns[int(NUM_SIMS*0.75)]:.2f}%")
print(f"  25th: {sorted_returns[int(NUM_SIMS*0.25)]:.2f}%")
print(f"  5th: {sorted_returns[int(NUM_SIMS*0.05)]:.2f}%")
print(f"  1st: {sorted_returns[int(NUM_SIMS*0.01)]:.2f}%")
print(f"  Worst: {sorted_returns[0]:.2f}%")

# Drawdown
sorted_dd = np.sort(max_dd_pcts)
print(f"\n--- MAX DRAWDOWN ---")
print(f"  Mean: {np.mean(max_dd_pcts):.2f}%")
print(f"  Median: {np.median(max_dd_pcts):.2f}%")
print(f"  Worst: {sorted_dd[-1]:.2f}%")
print(f"  95th percentile: {sorted_dd[int(NUM_SIMS*0.95)]:.2f}%")
print(f"  99th percentile: {sorted_dd[int(NUM_SIMS*0.99)]:.2f}%")
print(f"  99.5th percentile: {sorted_dd[int(NUM_SIMS*0.995)]:.2f}%")
print(f"  Best: {sorted_dd[0]:.2f}%")
print(f"  % simulations with DD > 20%: {(max_dd_pcts > 20).mean()*100:.1f}%")
print(f"  % simulations with DD > 30%: {(max_dd_pcts > 30).mean()*100:.1f}%")
print(f"  % simulations with DD > 50%: {(max_dd_pcts > 50).mean()*100:.1f}%")

# Win rate distribution
wr_backtest = n_wins / n_trades * 100
print(f"\n--- WIN RATE ---")
print(f"  Backtest WR: {wr_backtest:.1f}% (fixed across all shuffles)")

# Lose streaks
sorted_streaks = np.sort(worst_streaks)
print(f"\n--- WORST LOSING STREAK ---")
print(f"  Backtest max: {max_consec_losses}")
print(f"  Mean: {np.mean(worst_streaks):.1f}")
print(f"  Median: {int(np.median(worst_streaks))}")
print(f"  95th percentile: {int(sorted_streaks[int(NUM_SIMS*0.95)])}")
print(f"  99th percentile: {int(sorted_streaks[int(NUM_SIMS*0.99)])}")
print(f"  Worst: {int(sorted_streaks[-1])}")
print(f"  % with streak > 20: {(worst_streaks > 20).mean()*100:.1f}%")
print(f"  % with streak > 30: {(worst_streaks > 30).mean()*100:.1f}%")

# Profit factor
sorted_pf = np.sort(profit_factors)
pfs_finite = profit_factors[np.isfinite(profit_factors)]
print(f"\n--- PROFIT FACTOR ---")
print(f"  Mean: {np.mean(pfs_finite):.2f}" if len(pfs_finite) else "  Mean: inf")
print(f"  Median: {np.median(pfs_finite):.2f}" if len(pfs_finite) else "  Median: inf")
print(f"  % with PF < 1.0: {(profit_factors < 1.0).mean()*100:.1f}%")
print(f"  % with PF < 0.8: {(profit_factors < 0.8).mean()*100:.1f}%")

# Ruin probability
n_ruined = int(ruined.sum())
print(f"\n--- RUIN ANALYSIS ---")
print(f"  Ruin probability (< $0): {n_ruined/NUM_SIMS*100:.2f}%")
print(f"  % ending below $5,000 (-50%): {(final_equities < 5000).mean()*100:.1f}%")
print(f"  % ending below $7,500 (-25%): {(final_equities < 7500).mean()*100:.1f}%")
print(f"  % ending below $9,000 (-10%): {(final_equities < 9000).mean()*100:.1f}%")
print(f"  % ending above $12,500 (+25%): {(final_equities > 12500).mean()*100:.1f}%")
print(f"  % ending above $15,000 (+50%): {(final_equities > 15000).mean()*100:.1f}%")
print(f"  % ending above $20,000 (+100%): {(final_equities > 20000).mean()*100:.1f}%")

# Probability of specific drawdowns
print(f"\n--- RISK SUMMARY ---")
print(f"  Probability of > 10% drawdown: {(max_dd_pcts > 10).mean()*100:.1f}%")
print(f"  Probability of > 15% drawdown: {(max_dd_pcts > 15).mean()*100:.1f}%")
print(f"  Probability of > 20% drawdown: {(max_dd_pcts > 20).mean()*100:.1f}%")
print(f"  Probability of > 30% drawdown: {(max_dd_pcts > 30).mean()*100:.1f}%")
print(f"  Probability of any losing streak > 10: {(worst_streaks > 10).mean()*100:.1f}%")
print(f"  Probability of any losing streak > 15: {(worst_streaks > 15).mean()*100:.1f}%")

# Save results
output = {
    "backtest": {
        "n_trades": n_trades,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "win_rate_pct": round(wr_backtest, 2),
        "net_r": round(float(trade_r_values.sum()), 2),
        "max_consecutive_wins": max_consec_wins,
        "max_consecutive_losses": max_consec_losses,
        "exit_breakdown": dict(exit_counts),
        "final_equity": round(equity, 2),
        "return_pct": round((equity/INITIAL_EQ - 1)*100, 2),
    },
    "monte_carlo": {
        "n_simulations": NUM_SIMS,
        "final_equity": {
            "mean": round(float(np.mean(final_equities)), 2),
            "median": round(float(np.median(final_equities)), 2),
            "std": round(float(np.std(final_equities)), 2),
            "best_5pct": round(float(sorted_eq[int(NUM_SIMS*0.95)]), 2),
            "worst_25pct": round(float(sorted_eq[int(NUM_SIMS*0.25)]), 2),
            "worst_5pct": round(float(sorted_eq[int(NUM_SIMS*0.05)]), 2),
            "worst_1pct": round(float(sorted_eq[int(NUM_SIMS*0.01)]), 2),
        },
        "return_pct": {
            "mean": round(float(np.mean(returns)), 2),
            "median": round(float(np.median(returns)), 2),
            "std": round(float(np.std(returns)), 2),
            "best": round(float(sorted_returns[-1]), 2),
            "95th": round(float(sorted_returns[int(NUM_SIMS*0.95)]), 2),
            "5th": round(float(sorted_returns[int(NUM_SIMS*0.05)]), 2),
            "1st": round(float(sorted_returns[int(NUM_SIMS*0.01)]), 2),
        },
        "max_drawdown_pct": {
            "mean": round(float(np.mean(max_dd_pcts)), 2),
            "median": round(float(np.median(max_dd_pcts)), 2),
            "worst": round(float(sorted_dd[-1]), 2),
            "95th_percentile": round(float(sorted_dd[int(NUM_SIMS*0.95)]), 2),
            "99th_percentile": round(float(sorted_dd[int(NUM_SIMS*0.99)]), 2),
            "pct_over_20": round((max_dd_pcts > 20).mean()*100, 1),
            "pct_over_30": round((max_dd_pcts > 30).mean()*100, 1),
        },
        "worst_losing_streak": {
            "mean": round(float(np.mean(worst_streaks)), 1),
            "median": int(np.median(worst_streaks)),
            "95th_percentile": int(sorted_streaks[int(NUM_SIMS*0.95)]),
            "99th_percentile": int(sorted_streaks[int(NUM_SIMS*0.99)]),
            "worst": int(sorted_streaks[-1]),
            "pct_over_20": round((worst_streaks > 20).mean()*100, 1),
        },
        "profit_factor": {
            "mean": round(float(np.mean(pfs_finite)), 2) if len(pfs_finite) else None,
            "median": round(float(np.median(pfs_finite)), 2) if len(pfs_finite) else None,
            "pct_below_1": round((profit_factors < 1.0).mean()*100, 1),
        },
        "ruin": {
            "probability_pct": round(n_ruined/NUM_SIMS*100, 2),
            "pct_below_50pct": round((final_equities < 5000).mean()*100, 1),
            "pct_below_25pct": round((final_equities < 7500).mean()*100, 1),
            "pct_above_25pct": round((final_equities > 12500).mean()*100, 1),
            "pct_above_50pct": round((final_equities > 15000).mean()*100, 1),
        },
    },
    "generated_at": datetime.now(UTC).isoformat(),
}

out_path = ROOT / "reports" / "forward_shadow" / "monte_carlo_d4_results.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(output, indent=2))
print(f"\nSaved to {out_path}")
print(f"\n{'='*70}")
print("DONE")
