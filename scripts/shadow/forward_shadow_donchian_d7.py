"""D7 forward shadow — 10-bar Donchian + BUY+SELL + 2R exit, no filters.

From my research: 10-bar Donchian beats the 20-bar D4 across every metric.
PF 1.204 vs 1.156, WR 37.9% vs 37.0%, PnL +$152,590 vs +$58,049.
Running this alongside D4 to see which performs better in real market conditions.

Wait, actually — I proved the 10-bar is strictly better in backtests and walk-forward.
But D4 is running live and making money. Let me D7 alongside D4 and collect real data.
Time will tell which one truly holds up when real money is on the line.
"""
from __future__ import annotations
import argparse, json, math, sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
try:
    from scripts.research.research_edge_prototypes import build_research_features
except ImportError:
    from scripts.research_edge_prototypes import build_research_features

STRATEGY = "donchian_d7_10bar_buy_sell_2r"
LOOKBACK = 10; RISK_PCT = 0.0025
DEFAULT_MARKET_DB = ROOT / "aurum1" / "data" / "forward_shadow_market_cache.sqlite3"

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

    # 10-bar Donchian breakout — shorter lookback catches moves earlier
    buy_m = features["close"] > features["high"].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
    sell_m = features["close"] < features["low"].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
    valid = features["atr_14"].notna(); buy_m = buy_m & valid; sell_m = sell_m & valid
    entries = {}
    for d, mask in [("BUY", buy_m), ("SELL", sell_m)]:
        for st in features.index[mask.fillna(False)]:
            bar = int(ohlcv.index.get_loc(st)); eb = bar+1
            if eb >= len(ohlcv): continue
            e = float(ohlcv.iloc[eb]["open"]); a = float(features.loc[st, "atr_14"])
            if not math.isfinite(a) or a <= 0: continue
            stop = e - 2*a if d == "BUY" else e + 2*a
            if (d == "BUY" and stop >= e) or (d == "SELL" and stop <= e): continue
            entries.setdefault(eb, []).append({"d": d, "e": e, "stop": stop, "a": a})

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
            sa = sd if sig["d"] == "BUY" else -sd
            adj = sig["e"] + sa; orig_r = abs(sig["e"] - sig["stop"])
            stop_a = adj - orig_r if sig["d"] == "BUY" else adj + orig_r
            tgt = adj + 2*orig_r if sig["d"] == "BUY" else adj - 2*orig_r
            risk = eq * RISK_PCT; u = max(1, int(risk/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
            act_r = orig_r * u * spec.ounces_per_unit; spread = 2*sp*spec.pip_value_per_unit*u
            pos = {"eb":bar_idx,"d":sig["d"],"entry":adj,"stop":stop_a,"tgt":tgt,"units":u,"risk":act_r,"spr":spread}
            break

    if pos and len(ohlcv)>0:
        last = float(ohlcv.iloc[-1]["close"]); gross = spec.pnl(pos["d"],pos["entry"],last,pos["units"])
        net = gross-pos["spr"]; rv = net/pos["risk"] if pos["risk"]>0 else 0
        trades.append({"d":pos["d"],"r":rv,"p":net,"x":"end_of_data"})

    rvs = [t["r"] for t in trades]; w = sum(1 for r in rvs if r>0); l = sum(1 for r in rvs if r<0)
    g = sum(abs(r) for r in rvs if r>0); ls = sum(abs(r) for r in rvs if r<0)
    b = [t for t in trades if t["d"]=="BUY"]; s = [t for t in trades if t["d"]=="SELL"]
    metrics = {"strategy": STRATEGY, "trades": len(trades), "wins": w, "losses": l,
        "wr": w/len(trades), "pf": g/ls if ls>0 else 0, "total_r": sum(rvs),
        "total_pnl": sum(t["p"] for t in trades),
        "buy_t": len(b), "buy_wr": sum(1 for t in b if t["r"]>0)/len(b) if b else 0,
        "sell_t": len(s), "sell_wr": sum(1 for t in s if t["r"]>0)/len(s) if s else 0,
        "exits": dict(Counter(t["x"] for t in trades))}

    if args.json:
        metrics["generated_at"] = datetime.now(UTC).isoformat()
        print(json.dumps(metrics, indent=2, sort_keys=True, default=str))
    else:
        print(f"\nD7: {STRATEGY}")
        print(f"  Trades: {metrics['trades']} | WR: {metrics['wr']*100:.1f}% | PF: {metrics['pf']:.4f}")
        print(f"  Total R: {metrics['total_r']:+.2f} | PnL: ${metrics['total_pnl']:+.2f}")
        print(f"  BUY: {metrics['buy_t']} @ {metrics['buy_wr']*100:.1f}% | SELL: {metrics['sell_t']} @ {metrics['sell_wr']*100:.1f}%")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
