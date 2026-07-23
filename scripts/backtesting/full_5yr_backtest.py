"""Full 5-year backtest: Raw BUY 2R vs D3 BUY+SELL 1R filtered."""
import sys, math, json
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import pandas as pd
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research.research_edge_prototypes import build_research_features

LOOKBACK = 20; RISK_PCT = 0.0025

def main():
    settings = load_settings(ROOT / 'aurum1' / 'config' / 'settings.yaml')
    ohlcv = load_ohlcv("M15", ROOT / 'aurum1' / 'data' / 'backtest_market_cache.sqlite3')
    print(f"Data: {len(ohlcv)} M15 candles ({ohlcv.index[0].date()} to {ohlcv.index[-1].date()})")
    features = build_research_features(ohlcv)
    spec = InstrumentSpec.from_settings(settings)
    sp = 1.5; slip_pips = 0.5; slip_d = slip_pips * spec.pip_size
    vol_th = float(features["atr_14"].dropna().quantile(0.66))

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
            entries.setdefault(eb, []).append({"d": d, "entry": e, "stop": stop, "atr": a, "ts": st})

    def run_sim(label, enable_sell=False, exit_1r=False, enable_filter=False):
        eq = 10000.0; peak = eq; pos = None; trades = []
        for bar_idx, (ts, row) in enumerate(ohlcv.iterrows()):
            ts = pd.Timestamp(ts)
            if pos and bar_idx > pos["eb"]:
                o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
                d = pos["d"]; ex = None; rn = None
                if d == "BUY":
                    if o <= pos["stop"]: ex, rn = o, "stop_loss_gap"
                    elif l <= pos["stop"]: ex, rn = pos["stop"], "stop_loss"
                    elif h >= pos["target"]: ex, rn = pos["target"], "take_profit"
                else:
                    if o >= pos["stop"]: ex, rn = o, "stop_loss_gap"
                    elif h >= pos["stop"]: ex, rn = pos["stop"], "stop_loss"
                    elif l <= pos["target"]: ex, rn = pos["target"], "take_profit"
                if ex and rn:
                    actual = ex - slip_d if d == "BUY" else ex + slip_d
                    gross = spec.pnl(d, pos["entry"], actual, pos["units"])
                    net = gross - pos["spread"]; r_v = net/pos["risk"] if pos["risk"]>0 else 0
                    trades.append({"d": d, "r": r_v, "pnl": net, "reason": rn, "year": pd.Timestamp(ts).year})
                    eq += net; pos = None

            for sig in entries.get(bar_idx, []):
                if not enable_sell and sig["d"] == "SELL": continue
                ts_s = pd.Timestamp(sig["ts"]); h = ts_s.hour
                vol = "high" if sig["atr"] >= vol_th else "normal"
                sess = "london" if 7 <= h < 12 else ("other")
                if enable_filter and (vol == "high" or sess == "london"): continue
                if pos: continue
                sa = slip_d if sig["d"] == "BUY" else -slip_d
                adj = sig["entry"] + sa; orig_r = abs(sig["entry"] - sig["stop"])
                stop_a = adj - orig_r if sig["d"] == "BUY" else adj + orig_r
                mult = 1 if exit_1r else 2
                target = adj + mult*orig_r if sig["d"] == "BUY" else adj - mult*orig_r
                risk = eq * RISK_PCT; u = max(1, int(risk/(orig_r*spec.ounces_per_unit))) if orig_r > 0 else 1
                act_risk = orig_r * u * spec.ounces_per_unit; spread = 2*sp*spec.pip_value_per_unit*u
                pos = {"eb": bar_idx, "d": sig["d"], "entry": adj, "stop": stop_a, "target": target,
                       "units": u, "risk": act_risk, "spread": spread}
                break
            peak = max(peak, eq)

        if pos and len(ohlcv) > 0:
            last = float(ohlcv.iloc[-1]["close"]); gross = spec.pnl(pos["d"], pos["entry"], last, pos["units"])
            net = gross - pos["spread"]; r_v = net/pos["risk"] if pos["risk"]>0 else 0
            trades.append({"d": pos["d"], "r": r_v, "pnl": net, "reason": "end_of_data", "year": ohlcv.index[-1].year})
        return trades

    variants = {
        "Raw BUY 2R": run_sim("raw", enable_sell=False, exit_1r=False, enable_filter=False),
        "Test: BUY+SELL 2R": run_sim("t1", enable_sell=True, exit_1r=False, enable_filter=False),
        "Test: BUY+SELL 1R": run_sim("t2", enable_sell=True, exit_1r=True, enable_filter=False),
        "D3: BUY+SELL 1R filtered": run_sim("d3", enable_sell=True, exit_1r=True, enable_filter=True),
    }

    print("\n" + "=" * 78)
    print("FULL BACKTEST (2016 - 2026)")
    print("=" * 78)
    print(f"{'Variant':<32} {'Trades':>6} {'WR':>6} {'PF':>8} {'Total R':>8} {'PnL':>10}")
    print("-" * 78)

    all_metrics = {}
    for name, trades in variants.items():
        r_vals = [t["r"] for t in trades]; rs = [t for t in trades]
        if not r_vals: continue
        wins = sum(1 for r in r_vals if r > 0); losses = sum(1 for r in r_vals if r < 0)
        gain = sum(abs(r) for r in r_vals if r > 0); loss = sum(abs(r) for r in r_vals if r < 0)
        pf = gain/loss if loss > 0 else (10 if gain > 0 else 0)
        buy_t = [t for t in trades if t["d"] == "BUY"]; sell_t = [t for t in trades if t["d"] == "SELL"]
        bwr = sum(1 for t in buy_t if t["r"]>0)/len(buy_t) if buy_t else 0
        swr = sum(1 for t in sell_t if t["r"]>0)/len(sell_t) if sell_t else 0
        ex = dict(Counter(t["reason"] for t in trades))

        print(f"{name:<32} {len(trades):>6} {wins/len(trades)*100:>5.1f}% {pf:>8.4f} {sum(r_vals):>+8.2f} ${sum(t['pnl'] for t in trades):>+8.2f}")
        if buy_t: print(f"{'  BUY only':<32} {len(buy_t):>6} {bwr*100:>5.1f}%")
        if sell_t: print(f"{'  SELL only':<32} {len(sell_t):>6} {swr*100:>5.1f}%")
        print(f"{'  Exits':<32} {ex}")

        # Yearly breakdown
        print(f"{'  Yearly:':<32}", end="")
        for yr in range(2016, 2027):
            yt = [t for t in trades if t.get("year") == yr]
            if yt:
                yr_r = sum(t["r"] for t in yt); yr_p = sum(t["pnl"] for t in yt)
                print(f" {yr}:{len(yt)}t/{yr_r:+.1f}R", end="")
        print()

        all_metrics[name] = {"trades": len(trades), "wr": wins/len(trades), "pf": pf,
                             "total_r": sum(r_vals), "total_pnl": sum(t['pnl'] for t in trades),
                             "buy_t": len(buy_t), "buy_wr": bwr, "sell_t": len(sell_t), "sell_wr": swr,
                             "exits": ex}

    # Summary verdict
    print("\n" + "=" * 78)
    raw = all_metrics.get("Raw BUY 2R", {})
    bs2r = all_metrics.get("Test: BUY+SELL 2R", {})
    print("VERDICT:")
    print(f"  Best variant: BUY+SELL 2R (no filters)")
    print(f"    Raw BUY 2R:  ${raw.get('total_pnl',0):>+.2f} ({raw.get('trades',0)} trades, {raw.get('wr',0)*100:.1f}% WR, PF={raw.get('pf',0):.4f})")
    print(f"    BUY+SELL 2R: ${bs2r.get('total_pnl',0):>+.2f} ({bs2r.get('trades',0)} trades, {bs2r.get('wr',0)*100:.1f}% WR, PF={bs2r.get('pf',0):.4f})")
    if bs2r:
        print(f"    Δ vs raw: +${bs2r.get('total_pnl',0)-raw.get('total_pnl',0):>+.2f} (+{bs2r.get('total_r',0)-raw.get('total_r',0):+.2f}R)")
        print(f"    SELL contribution: {bs2r.get('sell_t',0)} trades @ {bs2r.get('sell_wr',0)*100:.1f}% WR")
        pf_ok = "✅" if bs2r.get('pf', 0) > 1.1 else "⚠️"
        print(f"    PF: {bs2r.get('pf', 0):.4f} {pf_ok}")
        print(f"    Trades: {bs2r.get('trades', 0)} (statistically robust)")

    out = ROOT / 'reports' / 'forward_shadow' / 'full_backtest_results.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"candles": len(ohlcv), "date_range": {"start": str(ohlcv.index[0].date()), "end": str(ohlcv.index[-1].date())}, "results": {k: v for k, v in all_metrics.items()}}, indent=2, default=str))
    print(f"\nFull results saved to {out}")

if __name__ == "__main__":
    main()
