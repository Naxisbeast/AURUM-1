"""D3 Research: Test SELL signals on Donchian breakouts.

Systematically tests each improvement in isolation:
  1. Baseline: Raw Donchian 2R BUY-only (locked)
  2. Test A:  Add SELL signals (2R exit, no filters)
  3. Test B:  SELL + 1R exit (no filters)
  4. Test C:  SELL + 1R + vol/session filter (D2 + SELL = D3)

Each test measures the marginal impact of the change.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research.research_edge_prototypes import build_research_features

LOOKBACK = 20
RISK_PER_TRADE_PCT = 0.0025
DEFAULT_MARKET_DB = ROOT / "aurum1" / "data" / "forward_shadow_market_cache.sqlite3"


@dataclass
class SimTrade:
    direction: str
    entry_price: float
    exit_price: float
    exit_reason: str
    r_multiple: float
    net_pnl: float
    session: str
    volatility_regime: str
    weekday: str


def classify_session(ts: pd.Timestamp) -> str:
    h = int(ts.hour)
    if 0 <= h < 7: return "asia"
    if 7 <= h < 12: return "london"
    if 12 <= h < 16: return "london_ny_overlap"
    if 16 <= h < 21: return "new_york"
    return "rollover"


def classify_vol(atr: float, threshold: float | None) -> str:
    return "high" if threshold is None or atr >= threshold else "normal"


def generate_signals(ohlcv: pd.DataFrame, features: pd.DataFrame) -> dict[str, list]:
    """Generate both BUY and SELL Donchian breakout signals."""
    buy_signal = features["close"] > features["high"].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
    sell_signal = features["close"] < features["low"].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
    valid = features["atr_14"].notna()
    buy_signal = buy_signal & valid
    sell_signal = sell_signal & valid

    signals = {"BUY": [], "SELL": []}
    for direction, mask in [("BUY", buy_signal), ("SELL", sell_signal)]:
        for signal_time in features.index[mask.fillna(False)]:
            bar = int(ohlcv.index.get_loc(signal_time))
            entry_bar = bar + 1
            if entry_bar >= len(ohlcv):
                continue
            entry_price = float(ohlcv.iloc[entry_bar]["open"])
            atr = float(features.loc[signal_time, "atr_14"])
            if not math.isfinite(atr) or atr <= 0:
                continue
            if direction == "BUY":
                stop = entry_price - 2.0 * atr
                tp = entry_price + 4.0 * atr  # 2R
                tp_1r = entry_price + 2.0 * atr  # 1R
            else:
                stop = entry_price + 2.0 * atr
                tp = entry_price - 4.0 * atr  # 2R
                tp_1r = entry_price - 2.0 * atr  # 1R
            if (direction == "BUY" and stop >= entry_price) or (direction == "SELL" and stop <= entry_price):
                continue
            signals[direction].append({
                "bar": bar, "entry_bar": entry_bar, "signal_time": signal_time,
                "entry_price": entry_price, "stop": stop, "tp_2r": tp, "tp_1r": tp_1r,
                "atr": atr, "reason": f"donchian_{LOOKBACK}_{'up' if direction=='BUY' else 'down'}"
            })
    return signals


def run_simulation(ohlcv: pd.DataFrame, features: pd.DataFrame, settings: dict[str, Any],
                   enable_sell: bool = False, exit_1r: bool = False, enable_filter: bool = False,
                   label: str = "") -> dict[str, Any]:
    """Run a single simulation with specified features enabled."""
    spec = InstrumentSpec.from_settings(settings)
    signals = generate_signals(ohlcv, features)
    spread_pips = float(settings.get("execution", {}).get("paper_spread_pips", 1.5))
    slip_pips = float(settings.get("execution", {}).get("slippage_std_pips", 0.5))
    slip_dist = slip_pips * spec.pip_size
    atr_vals = features["atr_14"].dropna()
    vol_threshold = float(atr_vals.quantile(0.66)) if len(atr_vals) >= 10 else None

    all_dirs = ["BUY"]
    if enable_sell:
        all_dirs.append("SELL")

    # Build entry map
    entries: dict[int, list[dict]] = {}
    for d in all_dirs:
        for sig in signals[d]:
            entries.setdefault(sig["entry_bar"], []).append({**sig, "direction": d})

    equity = 10000.0
    peak = equity
    trades: list[SimTrade] = []
    position: dict | None = None
    skipped = 0
    taken = 0
    blocked_reasons: Counter = Counter()

    for bar_idx, (ts, row) in enumerate(ohlcv.iterrows()):
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None: ts = ts.tz_localize("UTC")

        # Check exit for open position
        if position is not None and bar_idx > position["entry_bar"]:
            o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
            entry = position["entry_price"]
            sl = position["stop"]
            tp = position["target"]
            direction = position["direction"]
            exit_p = None
            reason = None

            if direction == "BUY":
                if o <= sl: exit_p, reason = o, "stop_loss_gap"
                elif l <= sl: exit_p, reason = sl, "stop_loss"
                elif h >= tp: exit_p, reason = tp, "take_profit"
            else:  # SELL
                if o >= sl: exit_p, reason = o, "stop_loss_gap"
                elif h >= sl: exit_p, reason = sl, "stop_loss"
                elif l <= tp: exit_p, reason = tp, "take_profit"

            if exit_p is not None and reason is not None:
                actual_exit = exit_p - slip_dist if direction == "BUY" else exit_p + slip_dist
                gross = spec.pnl(direction, entry, actual_exit, position["units"])
                net = gross - position["spread_est"]
                r = net / position["risk_amt"] if position["risk_amt"] > 0 else 0.0
                trades.append(SimTrade(direction=direction, entry_price=entry, exit_price=actual_exit,
                    exit_reason=reason, r_multiple=r, net_pnl=net,
                    session=position["session"], volatility_regime=position["vol"], weekday=position["wd"]))
                equity += net
                position = None

        # Process signals at this bar
        for sig in entries.get(bar_idx, []):
            d = sig["direction"]
            ts_sig = pd.Timestamp(sig["signal_time"])
            if ts_sig.tzinfo is None: ts_sig = ts_sig.tz_localize("UTC")
            vol = classify_vol(sig["atr"], vol_threshold)
            sess = classify_session(ts_sig)
            wd = ts_sig.day_name()

            # Apply filter if enabled
            if enable_filter and (vol == "high" or sess == "london"):
                blocked_reasons[f"filtered_{d}"] += 1
                skipped += 1
                continue

            if position is not None:
                skipped += 1
                blocked_reasons[f"open_position_{d}"] += 1
                continue

            taken += 1
            slip_adj = slip_dist if d == "BUY" else -slip_dist
            adjusted_entry = sig["entry_price"] + slip_adj
            orig_risk = abs(sig["entry_price"] - sig["stop"])
            adjusted_stop = adjusted_entry - orig_risk if d == "BUY" else adjusted_entry + orig_risk
            target = adjusted_entry + orig_risk if d == "BUY" else adjusted_entry - orig_risk
            if exit_1r:
                target = adjusted_entry + orig_risk if d == "BUY" else adjusted_entry - orig_risk
            else:
                target = adjusted_entry + 2 * orig_risk if d == "BUY" else adjusted_entry - 2 * orig_risk

            target_risk = equity * RISK_PER_TRADE_PCT
            raw_units = target_risk / (orig_risk * spec.ounces_per_unit) if orig_risk > 0 else spec.min_units
            units = spec.lots_to_units(spec.round_lots(spec.units_to_lots(raw_units)))
            actual_risk = orig_risk * units * spec.ounces_per_unit
            spread = 2.0 * spread_pips * spec.pip_value_per_unit * units

            position = {"direction": d, "entry_bar": bar_idx, "entry_price": adjusted_entry,
                "stop": adjusted_stop, "target": target, "units": units, "risk_amt": actual_risk,
                "spread_est": spread, "vol": vol, "session": sess, "wd": wd}

        peak = max(peak, equity)
    # end loop

    # Close any open position at end
    if position is not None and len(ohlcv) > 0:
        last = float(ohlcv.iloc[-1]["close"])
        gross = spec.pnl(position["direction"], position["entry_price"], last, position["units"])
        net = gross - position["spread_est"]
        r = net / position["risk_amt"] if position["risk_amt"] > 0 else 0.0
        trades.append(SimTrade(direction=position["direction"], entry_price=position["entry_price"],
            exit_price=last, exit_reason="end_of_data", r_multiple=r, net_pnl=net,
            session=position["session"], volatility_regime=position["vol"], weekday=position["wd"]))

    return compute_metrics(label, trades, equity, skipped, taken, blocked_reasons)


def compute_metrics(label: str, trades: list[SimTrade], final_equity: float,
                    skipped: int, taken: int, reasons: Counter) -> dict[str, Any]:
    r_vals = [t.r_multiple for t in trades]
    if not trades:
        return {"variant": label, "trades": 0, "final_equity": final_equity}

    wins = sum(1 for r in r_vals if r > 0)
    losses = sum(1 for r in r_vals if r < 0)
    gain = sum(abs(r) for r in r_vals if r > 0)
    loss = sum(abs(r) for r in r_vals if r < 0)
    pf = gain / loss if loss > 0 else (10.0 if gain > 0 else 0.0)

    # Separate by direction
    buy_trades = [t for t in trades if t.direction == "BUY"]
    sell_trades = [t for t in trades if t.direction == "SELL"]

    return {
        "variant": label,
        "trades": len(trades),
        "wins": wins, "losses": losses,
        "win_rate": wins / len(trades),
        "profit_factor": pf,
        "total_r": sum(r_vals),
        "avg_r": sum(r_vals) / len(r_vals),
        "total_pnl": sum(t.net_pnl for t in trades),
        "final_equity": final_equity,
        "skipped": skipped, "taken": taken,
        "blocked_reasons": dict(reasons),
        "buy_trades": len(buy_trades),
        "sell_trades": len(sell_trades),
        "buy_wr": sum(1 for t in buy_trades if t.r_multiple > 0) / len(buy_trades) if buy_trades else 0,
        "sell_wr": sum(1 for t in sell_trades if t.r_multiple > 0) / len(sell_trades) if sell_trades else 0,
        "exits": dict(Counter(t.exit_reason for t in trades)),
    }


def main() -> int:
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    ohlcv = load_ohlcv("M15", DEFAULT_MARKET_DB)
    if ohlcv.empty:
        print("ERROR: No M15 data")
        return 1
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=548)
    ohlcv = ohlcv[ohlcv.index >= cutoff].copy()
    features = build_research_features(ohlcv)

    print("=" * 72)
    print("D3 RESEARCH — Systematic SELL signal testing")
    print("=" * 72)
    print(f"Candles: {len(ohlcv)} ({ohlcv.index.min().date()} to {ohlcv.index.max().date()})")
    print()

    # Test A: Add SELL only (baseline = 2R exit, no filters)
    # Matches the locked forward-shadow behavior but with SELL enabled
    print("--- Test A: Add SELL signals (2R exit, no filters) ---")
    baseline_buy = run_simulation(ohlcv, features, settings, enable_sell=False, exit_1r=False, enable_filter=False, label="Baseline BUY-only 2R")
    test_a = run_simulation(ohlcv, features, settings, enable_sell=True, exit_1r=False, enable_filter=False, label="A: BUY+SELL 2R")
    print(f"  Baseline BUY:  {baseline_buy['trades']:>4} trades, WR={baseline_buy['win_rate']*100:>5.1f}%, PF={baseline_buy['profit_factor']:<.4f}, R={baseline_buy['total_r']:<+8.2f}")
    print(f"  Test A (w SELL): {test_a['trades']:>4} trades, WR={test_a['win_rate']*100:>5.1f}%, PF={test_a['profit_factor']:<.4f}, R={test_a['total_r']:<+8.2f}")
    print(f"    BUY:  {test_a['buy_trades']} trades, WR={test_a['buy_wr']*100:.1f}%")
    print(f"    SELL: {test_a['sell_trades']} trades, WR={test_a['sell_wr']*100:.1f}%")
    print(f"    Δ trades vs baseline: +{test_a['trades'] - baseline_buy['trades']}")
    print(f"    Δ R vs baseline: {test_a['total_r'] - baseline_buy['total_r']:+.2f}R")
    print()

    # Test B: SELL + 1R exit
    print("--- Test B: Add SELL + 1R exit (no filters) ---")
    baseline_buy_1r = run_simulation(ohlcv, features, settings, enable_sell=False, exit_1r=True, enable_filter=False, label="Baseline BUY-only 1R")
    test_b = run_simulation(ohlcv, features, settings, enable_sell=True, exit_1r=True, enable_filter=False, label="B: BUY+SELL 1R")
    print(f"  Baseline BUY 1R: {baseline_buy_1r['trades']:>4} trades, WR={baseline_buy_1r['win_rate']*100:>5.1f}%, PF={baseline_buy_1r['profit_factor']:<.4f}, R={baseline_buy_1r['total_r']:<+8.2f}")
    print(f"  Test B (w SELL): {test_b['trades']:>4} trades, WR={test_b['win_rate']*100:>5.1f}%, PF={test_b['profit_factor']:<.4f}, R={test_b['total_r']:<+8.2f}")
    print(f"    BUY:  {test_b['buy_trades']} trades, WR={test_b['buy_wr']*100:.1f}%")
    print(f"    SELL: {test_b['sell_trades']} trades, WR={test_b['sell_wr']*100:.1f}%")
    print(f"    Δ trades vs baseline: +{test_b['trades'] - baseline_buy_1r['trades']}")
    print(f"    Δ R vs baseline: {test_b['total_r'] - baseline_buy_1r['total_r']:+.2f}R")
    print()

    # Test C: SELL + 1R + vol/session filter (D3 = D2 + SELL)
    print("--- Test C: SELL + 1R + vol/session filter (D3 = D2 + SELL) ---")
    d2_baseline = run_simulation(ohlcv, features, settings, enable_sell=False, exit_1r=True, enable_filter=True, label="D2 baseline (BUY-only 1R filter)")
    test_c = run_simulation(ohlcv, features, settings, enable_sell=True, exit_1r=True, enable_filter=True, label="C: D3 (BUY+SELL 1R filter)")
    print(f"  D2 baseline:     {d2_baseline['trades']:>4} trades, WR={d2_baseline['win_rate']*100:>5.1f}%, PF={d2_baseline['profit_factor']:<.4f}, R={d2_baseline['total_r']:<+8.2f}")
    print(f"  Test C (D3):     {test_c['trades']:>4} trades, WR={test_c['win_rate']*100:>5.1f}%, PF={test_c['profit_factor']:<.4f}, R={test_c['total_r']:<+8.2f}")
    print(f"    BUY:  {test_c['buy_trades']} trades, WR={test_c['buy_wr']*100:.1f}%")
    print(f"    SELL: {test_c['sell_trades']} trades, WR={test_c['sell_wr']*100:.1f}%")

    # D2 raw comparison
    # Calculate SELL marginal value
    delta_r = test_c['total_r'] - d2_baseline['total_r']
    delta_trades = test_c['trades'] - d2_baseline['trades']
    print(f"    Δ trades: +{delta_trades}")
    print(f"    Δ R: +{delta_r:.2f}R")
    print(f"    Δ PnL: ${test_c['total_pnl'] - d2_baseline['total_pnl']:+.2f}")

    # Exit breakdown
    print(f"\n    D3 Exit breakdown: {test_c['exits']}")

    print()
    print("=" * 72)
    print("SUMMARY TABLE")
    print("=" * 72)
    print(f"{'Variant':<28} {'Trades':>6} {'WR':>6} {'PF':>8} {'Total R':>8} {'PnL':>10}")
    print("-" * 72)
    for result in [baseline_buy, test_a, baseline_buy_1r, test_b, d2_baseline, test_c]:
        print(f"{result['variant']:<28} {result['trades']:>6} {result['win_rate']*100:>5.1f}% {result['profit_factor']:>8.4f} {result['total_r']:>+8.2f} ${result['total_pnl']:>+8.2f}")
    print("=" * 72)

    # Output JSON
    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candles": len(ohlcv),
        "date_range": {"start": str(ohlcv.index.min().date()), "end": str(ohlcv.index.max().date())},
        "results": {
            "baseline_buy_2r": baseline_buy,
            "test_a_buy_sell_2r": test_a,
            "baseline_buy_1r": baseline_buy_1r,
            "test_b_buy_sell_1r": test_b,
            "d2_baseline": d2_baseline,
            "test_c_d3": test_c,
        }
    }
    output_path = ROOT / "reports" / "forward_shadow" / "d3_research_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(f"\nFull results: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
