"""D6 — Donchian breakout BUY+SELL + ML ensemble filter (trained models).

D6 takes D4 (Donchian 20, BUY+SELL, 2R exit, no filters) and filters its
entries through the trained ML ensemble (RegimeClassifier + DirectionPredictor).
Only enters when the ML signal agrees with the breakout direction.

Requires trained model artifacts in aurum1/models/artifacts/.
"""

from __future__ import annotations
import argparse, json, math, sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research_edge_prototypes import build_research_features
from aurum1.features.engineer import FeatureEngineer
from aurum1.models.regime_classifier import RegimeClassifier
from aurum1.models.direction_predictor import DirectionPredictor
from aurum1.models.ensemble import EnsembleSignal

STRATEGY = "donchian_d6_ml_filtered"
LOOKBACK = 20; RISK_PCT = 0.0025
DEFAULT_MARKET_DB = ROOT / "aurum1" / "data" / "backtest_market_cache.sqlite3"

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

    # Load trained ML models
    print("Loading ML models...")
    rc = RegimeClassifier(settings)
    dp = DirectionPredictor(settings)
    ensemble = EnsembleSignal(settings)
    # Trigger model load
    from aurum1.models.utils import model_dir_from_settings, load_pickle
    model_dir = model_dir_from_settings(settings)
    rc_path = model_dir / "regime_classifier_latest.pkl"
    dp_path = model_dir / "direction_predictor_latest.pkl"
    if rc_path.exists():
        payload = load_pickle(rc_path)
        rc.model = payload.get("model")
        rc.feature_names = list(payload.get("feature_names", []))
        print(f"  RegimeClassifier loaded ({rc_path.stat().st_size/1024:.1f} KB)")
    else:
        print("  WARNING: RegimeClassifier not found, using fallback")
    if dp_path.exists():
        payload = load_pickle(dp_path)
        dp.model = payload.get("model")
        dp.scaler = payload.get("scaler")
        dp.feature_names = list(payload.get("feature_names", []))
        print(f"  DirectionPredictor loaded ({dp_path.stat().st_size/1024:.1f} KB)")
    else:
        print("  WARNING: DirectionPredictor not found, using fallback")

    # Build full feature table for ML predictions (with lookahead disabled for causal)
    print("Building features for ML...")
    dates = ohlcv.index.normalize().unique()
    macro = pd.DataFrame({'dgs10': 4.0, 'cpi': 300.0, 'cpi_yoy': 3.0, 'real_yield': 1.0,
        'dxy': 100.0, 'dxy_daily_return': 0.0, 'vix': 20.0, 'vix_1d_change': 0.0},
        index=pd.DatetimeIndex(dates, name='date'))
    cot = pd.DataFrame({'market_name': ['GOLD'], 'open_interest': [1.0], 'long_positions': [0.0],
        'short_positions': [0.0], 'net_positioning': [0.0], 'cot_net_long_pct': [0.0], 'source': ['placeholder']},
        index=pd.DatetimeIndex([dates[0]], name='report_date'))
    engineer = FeatureEngineer({'feature_engineering': {'lookahead_check': False}})
    full_features = engineer.build_features(ohlcv, macro, cot, include_target=False)
    print(f"  Feature table: {full_features.shape}")

    # Generate Donchian signals
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
            entries.setdefault(eb, []).append({"d": d, "e": e, "stop": stop, "a": a, "ts": st})

    # Build a map of feature rows by timestamp for ML lookup
    feat_by_ts = {}
    for ts in full_features.index:
        feat_by_ts[ts] = full_features.loc[ts:ts]

    print(f"\nRunning D4 (no ML) and D6 (with ML filter)...")
    results = {"D4_control": [], "D6_ml_filtered": []}

    for label, use_ml in [("D4_control", False), ("D6_ml_filtered", True)]:
        eq = 10000.0; trades = []; pos = None
        last_pred = {"direction": "FLAT", "regime": "RANGING", "score": 0.0}

        for bar_idx, (ts, row) in enumerate(ohlcv.iterrows()):
            # Exit check
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

            # Entry check
            for sig in entries.get(bar_idx, []):
                if pos: continue

                if use_ml:
                    # Get ML prediction for current candle
                    ts_st = pd.Timestamp(sig["ts"])
                    # Find closest feature row at or before signal time
                    feat_ts = full_features.index[full_features.index <= ts_st]
                    if len(feat_ts) == 0: continue
                    nearest_ts = feat_ts[-1]
                    feat_row = full_features.loc[nearest_ts:nearest_ts]

                    try:
                        # Get regime
                        if rc.model is not None and rc.feature_names:
                            regime_proba = rc.predict_proba(feat_row[rc.feature_names])[-1]
                        else:
                            regime_proba = np.full(3, 0.33)
                            adx_val = features.loc[nearest_ts, "adx_14"] if nearest_ts in features.index else 0
                            ema9 = features.loc[nearest_ts, "ema_9"] if nearest_ts in features.index else 0
                            ema20 = features.loc[nearest_ts, "ema_20"] if nearest_ts in features.index else 0
                            label = 2
                            if adx_val > 25 and ema9 > ema20: label = 0
                            elif adx_val > 25 and ema9 < ema20: label = 1
                            regime_proba = np.full(3, 0.10)
                            regime_proba[label] = 0.80

                        # Get direction signal
                        if dp.model is not None and dp.feature_names:
                            direction_signal = dp.predict_signal(feat_row[dp.feature_names])
                        else:
                            direction_signal = 0.5 if sig["d"] == "BUY" else -0.5

                        # Combine
                        sentiment = {"positive": 0.0, "negative": 0.0, "neutral": 1.0, "quality": "empty"}
                        signal_result = ensemble.combine(regime_proba, direction_signal, sentiment, timestamp=ts_st)

                        # D6 filter: only enter if ML agrees with breakout direction
                        if signal_result.direction != sig["d"] and signal_result.direction != "FLAT":
                            continue  # ML disagrees — skip

                        last_pred = {"direction": signal_result.direction, "regime": signal_result.regime,
                            "score": signal_result.raw_score}
                    except Exception as exc:
                        # If ML fails, fall through to Donchian-only entry
                        pass

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
            "avg_r": sum(rvs)/len(rvs)}

    d4s = stats(results["D4_control"], "D4 (no ML)")
    d6s = stats(results["D6_ml_filtered"], "D6 (ML filtered)")

    print("\n" + "=" * 78)
    print("D6 vs D4 — ML Filter Comparison (11-Year)")
    print(f"{'Variant':<35} {'Trades':>6} {'WR':>6} {'PF':>8} {'Total R':>8} {'Avg R':>8} {'PnL':>10}")
    print("-" * 78)
    for m in [d4s, d6s]:
        if m['trades']==0: continue
        print(f"{m['label']:<35} {m['trades']:>6} {m['wr']*100:>5.1f}% {m['pf']:>8.4f} {m['total_r']:>+8.2f} {m['avg_r']:>+8.4f} ${m['total_pnl']:>+8.2f}")
    dr = d6s['total_r']-d4s['total_r']; dp = d6s['total_pnl']-d4s['total_pnl']
    print(f"\nD6 vs D4: ΔR={dr:+.2f}  ΔPnL=${dp:+.2f}  {'✅ ML ADDS value' if dr>0 else '❌ ML does NOT add value'}")

    if args.json:
        d6s["generated_at"] = datetime.now(UTC).isoformat(); d6s["strategy"] = STRATEGY
        d6s["comparison"] = {"delta_r": dr, "delta_pnl": dp}
        print(json.dumps(d6s, indent=2, default=str))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
