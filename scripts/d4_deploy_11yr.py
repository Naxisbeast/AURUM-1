"""D4: BUY+SELL + 2R exit, no filters. 11-year final verdict."""
import sys, math, json
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research_edge_prototypes import build_research_features

LOOKBACK = 20; RISK_PCT = 0.0025

def main():
    settings = load_settings(Path('/opt/aurum1/aurum1/config/settings.yaml'))
    ohlcv = load_ohlcv("M15", Path('/opt/aurum1/aurum1/data/backtest_market_cache.sqlite3'))
    features = build_research_features(ohlcv)
    spec = InstrumentSpec.from_settings(settings)
    sp = 1.5; slip_pips = 0.5; sd = slip_pips * spec.pip_size

    buy_m = features["close"] > features["high"].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
    sell_m = features["close"] < features["low"].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
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
            entries.setdefault(eb, []).append({"d": d, "entry": e, "stop": stop, "atr": a})

    def run(label, enable_sell):
        eq = 10000.0; pos = None; trades = []
        for bar_idx, (ts, row) in enumerate(ohlcv.iterrows()):
            ts = pd.Timestamp(ts)
            if pos and bar_idx > pos["eb"]:
                o,h,l = float(row["open"]),float(row["high"]),float(row["low"])
                d = pos["d"]; ex = None; rn = None
                if d == "BUY":
                    if o <= pos["stop"]: ex,rn = o,"stop_loss_gap"
                    elif l <= pos["stop"]: ex,rn = pos["stop"],"stop_loss"
                    elif h >= pos["target"]: ex,rn = pos["target"],"take_profit"
                else:
                    if o >= pos["stop"]: ex,rn = o,"stop_loss_gap"
                    elif h >= pos["stop"]: ex,rn = pos["stop"],"stop_loss"
                    elif l <= pos["target"]: ex,rn = pos["target"],"take_profit"
                if ex and rn:
                    actual = ex - sd if d == "BUY" else ex + sd
                    gross = spec.pnl(d, pos["entry"], actual, pos["units"])
                    net = gross - pos["spread"]; rv = net/pos["risk"] if pos["risk"]>0 else 0
                    trades.append({"d":d,"r":rv,"pnl":net,"reason":rn})
                    eq += net; pos = None
            for sig in entries.get(bar_idx, []):
                if not enable_sell and sig["d"] == "SELL": continue
                if pos: continue
                sa = sd if sig["d"] == "BUY" else -sd
                adj = sig["entry"] + sa; orig_r = abs(sig["entry"] - sig["stop"])
                stop_a = adj - orig_r if sig["d"] == "BUY" else adj + orig_r
                target = adj + 2*orig_r if sig["d"] == "BUY" else adj - 2*orig_r
                risk = eq * RISK_PCT; u = max(1, int(risk/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
                act_r = orig_r * u * spec.ounces_per_unit; spread = 2*sp*spec.pip_value_per_unit*u
                pos = {"eb":bar_idx,"d":sig["d"],"entry":adj,"stop":stop_a,"target":target,"units":u,"risk":act_r,"spread":spread}
                break
        if pos and len(ohlcv)>0:
            last = float(ohlcv.iloc[-1]["close"]); gross = spec.pnl(pos["d"],pos["entry"],last,pos["units"])
            net = gross-pos["spread"]; rv = net/pos["risk"] if pos["risk"]>0 else 0
            trades.append({"d":pos["d"],"r":rv,"pnl":net,"reason":"end_of_data"})
        return trades

    raw = run("Raw BUY 2R", False)
    d4 = run("D4 BUY+SELL 2R", True)

    def stats(t, label):
        rv = [x["r"] for x in t]; w = sum(1 for r in rv if r>0); l = sum(1 for r in rv if r<0)
        g = sum(abs(r) for r in rv if r>0); ls = sum(abs(r) for r in rv if r<0)
        b = [x for x in t if x["d"]=="BUY"]; s = [x for x in t if x["d"]=="SELL"]
        return {"label":label,"trades":len(t),"wr":w/len(t),"pf":(g/ls if ls>0 else 0),
            "r":sum(rv),"pnl":sum(x["pnl"] for x in t),
            "buy_t":len(b),"buy_wr":sum(1 for x in b if x["r"]>0)/len(b) if b else 0,
            "sell_t":len(s),"sell_wr":sum(1 for x in s if x["r"]>0)/len(s) if s else 0}

    rs = stats(raw, "Raw BUY 2R")
    d4s = stats(d4, "D4 BUY+SELL 2R")

    print("=" * 78)
    print("FINAL 11-YEAR VERDICT (2016-2026)")
    print(f"Data: {len(ohlcv)} M15 candles")
    print("=" * 78)
    print(f"{'Variant':<30} {'Trades':>6} {'WR':>6} {'PF':>8} {'Total R':>8} {'PnL':>10}")
    print("-" * 78)
    for m in [rs, d4s]:
        print(f"{m['label']:<30} {m['trades']:>6} {m['wr']*100:>5.1f}% {m['pf']:>8.4f} {m['r']:>+8.2f} ${m['pnl']:>+8.2f}")
        if m['sell_t']: print(f"{'  SELL only':<30} {m['sell_t']:>6} {m['sell_wr']*100:>5.1f}%")
    print()
    print("BEST CONFIG: BUY+SELL 2R (no filters)")
    print(f"  +${d4s['pnl']-rs['pnl']:>+.2f} over raw strategy")
    print(f"  +{d4s['r']-rs['r']:>+.2f}R over raw strategy")
    print(f"  SELL contributes {d4s['sell_t']} trades at {d4s['sell_wr']*100:.1f}% WR")
    print(f"  PF: {d4s['pf']:.4f} over {d4s['trades']} trades (11 years)")

    # Save results
    out_path = Path('/opt/aurum1/reports/forward_shadow/d4_11yr_results.json')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "variant": "D4 BUY+SELL 2R",
        "candles": len(ohlcv),
        "date_range": f"{ohlcv.index[0].date()} to {ohlcv.index[-1].date()}",
        "raw_buy_2r": rs,
        "d4_buy_sell_2r": d4s,
        "improvement": {"delta_pnl": d4s['pnl']-rs['pnl'], "delta_r": d4s['r']-rs['r']}
    }, indent=2))
    print(f"\nResults saved: {out_path}")

if __name__ == "__main__":
    main()
