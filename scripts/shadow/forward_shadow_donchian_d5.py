"""D5 forward shadow — BUY+SELL Donchian + 2R exit + adaptive ATR stop + volume imbalance filter.

Enhancements over D4:
  1. Adaptive ATR Stop: switch between 2x ATR and Donchian low based on ATR regime
  2. Volume Imbalance Filter: only enter when 5-bar buy/sell volume aligns with direction

D4: BUY+SELL Donchian, 2R exit, no filters. 8,175 trades, 1.14 PF, +$42,678 PnL (11yr).
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
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research.research_edge_prototypes import build_research_features, atr_wilder, ema

STRATEGY = "donchian_d5_adaptive_volfilter"
LOOKBACK = 20; RISK_PCT = 0.0025
DEFAULT_MARKET_DB = ROOT / "aurum1" / "data" / "backtest_market_cache.sqlite3"

def volume_imbalance(ohlcv: pd.Series, window: int = 5) -> pd.Series:
    """5-bar volume imbalance: (buy_vol - sell_vol) / total_vol."""
    buy_vol = ohlcv["volume"] * (ohlcv["close"] > ohlcv["open"]).astype(float)
    sell_vol = ohlcv["volume"] * (ohlcv["close"] < ohlcv["open"]).astype(float)
    # Neutral candles: half volume to each side
    neutral = ohlcv["close"] == ohlcv["open"]
    buy_vol = buy_vol + ohlcv["volume"] * 0.5 * neutral.astype(float)
    sell_vol = sell_vol + ohlcv["volume"] * 0.5 * neutral.astype(float)
    buy_sum = buy_vol.rolling(window, min_periods=window).sum()
    sell_sum = sell_vol.rolling(window, min_periods=window).sum()
    total = buy_sum + sell_sum
    result = (buy_sum - sell_sum) / total.replace(0, np.nan)
    return result.fillna(0.0)

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    ohlcv = load_ohlcv("M15", args.market_db)
    if ohlcv.empty: print("ERROR: No M15 data"); return 1
    features = build_research_features(ohlcv)
    spec = InstrumentSpec.from_settings(settings)
    sp = 1.5; slip = 0.5; sd = slip * spec.pip_size

    # Additional features for D5
    features["atr_20_ma"] = features["atr_14"].rolling(20, min_periods=20).mean()
    features["donchian_low_20"] = features["low"].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
    features["donchian_high_20"] = features["high"].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
    features["vol_imbalance_5"] = volume_imbalance(ohlcv)

    buy_m = features["close"] > features["donchian_high_20"]
    sell_m = features["close"] < features["donchian_low_20"]
    valid = features["atr_14"].notna()
    buy_m = buy_m & valid; sell_m = sell_m & valid
    entries = {}
    for d, mask in [("BUY", buy_m), ("SELL", sell_m)]:
        for st in features.index[mask.fillna(False)]:
            bar = int(ohlcv.index.get_loc(st)); eb = bar+1
            if eb >= len(ohlcv): continue
            e = float(ohlcv.iloc[eb]["open"]); a = float(features.loc[st, "atr_14"])
            if not math.isfinite(a) or a <= 0: continue
            stop = e - 2*a if d == "BUY" else e + 2*a
            if (d == "BUY" and stop >= e) or (d == "SELL" and stop <= e): continue
            entries.setdefault(eb, []).append({
                "d": d, "e": e, "stop": stop, "a": a,
                "atr_20_ma": float(features.loc[st, "atr_20_ma"]),
                "dc_low": float(features.loc[st, "donchian_low_20"]),
                "dc_high": float(features.loc[st, "donchian_high_20"]),
                "vol_imb": float(features.loc[st, "vol_imbalance_5"]),
            })

    results = {"D4_control": [], "D5a_adaptive_stop_only": [], "D5b_vol_filter_only": [], "D5c_both": []}

    for label, use_adaptive_stop, use_vol_filter in [
        ("D4_control", False, False),
        ("D5a_adaptive_stop_only", True, False),
        ("D5b_vol_filter_only", False, True),
        ("D5c_both", True, True)]:

        eq = 10000.0; pos = None; trades = []
        for bar_idx, (ts, row) in enumerate(ohlcv.iterrows()):
            if pos and bar_idx > pos["eb"]:
                o,h,l = float(row["open"]),float(row["high"]),float(row["low"])
                d = pos["d"]; ex = None; rn = None
                if d == "BUY":
                    if o <= pos["stop"]: ex,rn = o,"stop_loss_gap"
                    elif l <= pos["stop"]: ex,rn = pos["stop"],"stop_loss"
                    elif h >= pos["tgt"]: ex,rn = pos["tgt"],"take_profit"
                else:
                    if o >= pos["stop"]: ex,rn = o,"stop_loss_gap"
                    elif h >= pos["stop"]: ex,rn = pos["stop"],"stop_loss"
                    elif l <= pos["tgt"]: ex,rn = pos["tgt"],"take_profit"
                if ex and rn:
                    actual = ex - sd if d == "BUY" else ex + sd
                    gross = spec.pnl(d, pos["entry"], actual, pos["units"])
                    net = gross - pos["spr"]; rv = net/pos["risk"] if pos["risk"]>0 else 0
                    trades.append({"d":d,"r":rv,"p":net,"x":rn})
                    eq += net; pos = None

            for sig in entries.get(bar_idx, []):
                if pos: continue
                # Volume imbalance filter
                if use_vol_filter:
                    vi = sig["vol_imb"]
                    if (sig["d"] == "BUY" and vi <= 0) or (sig["d"] == "SELL" and vi >= 0):
                        continue

                sa = sd if sig["d"] == "BUY" else -sd
                adj = sig["e"] + sa; orig_r = abs(sig["e"] - sig["stop"])

                # Adaptive ATR stop
                if use_adaptive_stop and sig["atr_20_ma"] > 0 and math.isfinite(sig["atr_20_ma"]):
                    if sig["a"] < sig["atr_20_ma"]:
                        # Tight regime: 2× ATR stop
                        stop_a = adj - 2*sig["a"] if sig["d"] == "BUY" else adj + 2*sig["a"]
                    else:
                        # Trending regime: Donchian channel stop
                        if sig["d"] == "BUY":
                            # Use Donchian low for BUY stop (exit when price falls below 20-bar low)
                            dc_level = sig["dc_low"] + sd  # add slippage buffer
                            stop_a = min(adj - 2*sig["a"], dc_level)  # tighter of 2× ATR and Donchian
                        else:
                            dc_level = sig["dc_high"] - sd
                            stop_a = max(adj + 2*sig["a"], dc_level)
                else:
                    stop_a = adj - orig_r if sig["d"] == "BUY" else adj + orig_r

                # Recompute risk distance based on adaptive stop
                actual_risk_dist = abs(adj - stop_a)
                if actual_risk_dist <= 0: continue
                tgt = adj + 2*actual_risk_dist if sig["d"] == "BUY" else adj - 2*actual_risk_dist
                risk = eq * RISK_PCT; u = max(1, int(risk/(actual_risk_dist*spec.ounces_per_unit))) if actual_risk_dist>0 else 1
                act_r = actual_risk_dist * u * spec.ounces_per_unit; spread = 2*sp*spec.pip_value_per_unit*u
                pos = {"eb":bar_idx,"d":sig["d"],"entry":adj,"stop":stop_a,"tgt":tgt,"units":u,"risk":act_r,"spr":spread}
                break

        if pos and len(ohlcv)>0:
            last = float(ohlcv.iloc[-1]["close"]); gross = spec.pnl(pos["d"],pos["entry"],last,pos["units"])
            net = gross-pos["spr"]; rv = net/pos["risk"] if pos["risk"]>0 else 0
            trades.append({"d":pos["d"],"r":rv,"p":net,"x":"end_of_data"})
        results[label] = trades

    def stats(t, label):
        rvs = [x["r"] for x in t]; w = sum(1 for r in rvs if r>0); l = sum(1 for r in rvs if r<0)
        if not rvs: return {"label": label, "trades": 0}
        g = sum(abs(r) for r in rvs if r>0); ls = sum(abs(r) for r in rvs if r<0)
        b = [x for x in t if x["d"]=="BUY"]; s = [x for x in t if x["d"]=="SELL"]
        return {"label": label, "trades": len(t), "wr": w/len(t), "pf": g/ls if ls>0 else 0,
            "total_r": sum(rvs), "total_pnl": sum(x["p"] for x in t),
            "buy_t": len(b), "buy_wr": sum(1 for x in b if x["r"]>0)/len(b) if b else 0,
            "sell_t": len(s), "sell_wr": sum(1 for x in s if x["r"]>0)/len(s) if s else 0,
            "exits": dict(Counter(x["x"] for x in t)),
            "avg_r": sum(rvs)/len(rvs)}

    metrics_list = [stats(results[k], {"D4_control": "D4 control (no enhancements)", "D5a_adaptive_stop_only": "D5a adaptive stop only", "D5b_vol_filter_only": "D5b vol filter only", "D5c_both": "D5c both enhancements"}[k]) for k in ["D4_control", "D5a_adaptive_stop_only", "D5b_vol_filter_only", "D5c_both"]]

    print("=" * 78)
    print("D5 Component Analysis — 11-Year Comparison")
    print(f"Data: {len(ohlcv)} M15 candles ({ohlcv.index[0].date()} to {ohlcv.index[-1].date()})")
    print("=" * 78)
    print(f"{'Variant':<35} {'Trades':>6} {'WR':>6} {'PF':>8} {'Total R':>8} {'Avg R':>8} {'PnL':>10}")
    print("-" * 78)
    for m in metrics_list:
        if m['trades'] == 0: continue
        print(f"{m['label']:<35} {m['trades']:>6} {m['wr']*100:>5.1f}% {m['pf']:>8.4f} {m['total_r']:>+8.2f} {m['avg_r']:>+8.4f} ${m['total_pnl']:>+8.2f}")
        if m.get('sell_t'): print(f"{'  SELL only':<35} {m['sell_t']:>6} {m['sell_wr']*100:>5.1f}%")

    d4 = metrics_list[0]
    for m in metrics_list[1:]:
        if m['trades'] == 0: continue
        dr = m['total_r'] - d4['total_r']
        dp = m['total_pnl'] - d4['total_pnl']
        print(f"{'  Δ vs D4':<35} {'':>6} {'':>6} {'':>8} {dr:>+8.2f} {'':>8} ${dp:>+8.2f}")

    print(f"\n{'─'*78}")
    print("VERDICT:")
    for m in metrics_list[1:]:
        if m['trades'] == 0: continue
        dr = m['total_r'] - d4['total_r']
        tag = "✅ ADDS value" if dr > 0 else "❌ HURTS value"
        print(f"  {m['label']:<30}: ΔR={dr:>+7.2f} {tag}")

    if args.json:
        out = metrics_list[-1]
        out["generated_at"] = datetime.now(UTC).isoformat()
        out["strategy"] = STRATEGY
        out["all_variants"] = {m['label']: m for m in metrics_list}
        print()
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
