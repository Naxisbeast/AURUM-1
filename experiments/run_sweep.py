"""
experiments/run_sweep.py — Systematic parameter sweep with full logging.

Each experiment is logged to experiments/results/sweep_log.csv with:
  experiment_id, timestamp, description, trades, wr, pf, pnl, avg_r, config_json

Usage: python experiments/run_sweep.py
"""
import sys, os, json, csv, math, time, copy
from datetime import datetime, UTC
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from aurum1.data.ingestion import load_ohlcv, load_settings
from scripts.research.research_edge_prototypes import build_research_features
from aurum1.instruments import InstrumentSpec

# ── Setup ──
ohlcv = load_ohlcv('M15', 'aurum1/data/combined_market_cache.sqlite3')
features = build_research_features(ohlcv)
settings = load_settings('aurum1/config/settings.yaml')
spec = InstrumentSpec.from_settings(settings)
LOOKBACK = 20; RISK_PCT = 0.0025; sp = 1.5; slip = 0.5; sd = slip * spec.pip_size

buy_signal = features['close'] > features['high'].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
sell_signal = features['close'] < features['low'].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
valid = features['atr_14'].notna()
atr = features['atr_14']

LOG_FILE = Path(__file__).resolve().parent / 'results' / 'sweep_log.csv'
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

def log_experiment(exp_id, desc, result, config):
    """Append one row to the sweep log CSV."""
    is_new = not LOG_FILE.exists()
    with open(LOG_FILE, 'a', newline='') as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(['experiment_id', 'timestamp', 'description', 'trades', 'wr_pct',
                        'pf', 'pnl', 'avg_r', 'config_json'])
        w.writerow([
            exp_id, datetime.now(UTC).isoformat(), desc,
            result['trades'], f"{result['wr']:.1f}",
            f"{result['pf']:.3f}", f"{result['pnl']:+.2f}",
            f"{result['avg_r']:.4f}", json.dumps(config)
        ])

def run(entry_filter, exit_mode, exit_param=None, lookback=20):
    """Run one backtest with given parameters."""
    b_sig = (features['close'] > features['high'].rolling(lookback, min_periods=lookback).max().shift(1)) if lookback != 20 else buy_signal
    s_sig = (features['close'] < features['low'].rolling(lookback, min_periods=lookback).min().shift(1)) if lookback != 20 else sell_signal
    entries = {}
    for d, mask in [('BUY', b_sig & valid & entry_filter), ('SELL', s_sig & valid & entry_filter)]:
        for st in features.index[mask.fillna(False)]:
            bar = int(ohlcv.index.get_loc(st)); eb = bar+1
            if eb >= len(ohlcv): continue
            e = float(ohlcv.iloc[eb]['open']); a = float(features.loc[st, 'atr_14'])
            if not math.isfinite(a) or a <= 0: continue
            stop = e - 2*a if d == 'BUY' else e + 2*a
            if (d == 'BUY' and stop >= e) or (d == 'SELL' and stop <= e): continue
            entries.setdefault(eb, []).append({'d':d,'e':e,'stop':stop,'a':a, 'lookback': lookback})

    eq = 10000.0; pos = None; trades = []
    for bar_idx, (ts, row) in enumerate(ohlcv.iterrows()):
        # Position management
        if pos:
            o,h,l = float(row['open']),float(row['high']),float(row['low']); d = pos['d']
            if exit_mode == 'FIXED_2R':
                if (d == 'BUY' and l <= pos['stop']) or (d == 'SELL' and h >= pos['stop']):
                    ex = pos['stop']; actual = ex - sd if d == 'BUY' else ex + sd
                    net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
                    rv = net/pos['risk'] if pos['risk']>0 else 0; trades.append(rv); eq += net; pos = None
                elif (d == 'BUY' and h >= pos['tgt']) or (d == 'SELL' and l <= pos['tgt']):
                    ex = pos['tgt']; actual = ex - sd if d == 'BUY' else ex + sd
                    net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
                    rv = net/pos['risk']; trades.append(rv); eq += net; pos = None
            elif exit_mode == 'CHANDELIER':
                mult = exit_param or 3.0
                if not pos.get('cx'):
                    class CX:
                        def __init__(s2):
                            s2.d=d;s2.ent=pos['entry'];s2.m=mult;s2.ext=pos['entry']
                            s2.stp = pos['entry']-(mult*pos['ae']) if d=='BUY' else pos['entry']+(mult*pos['ae'])
                            s2.trig=False;s2.ex=None
                        def upd(s2,high,low,atr2):
                            if s2.trig: return True
                            if s2.d=='BUY':
                                s2.ext=max(s2.ext,high);ns=s2.ext-(s2.m*atr2);s2.stp=max(s2.stp,ns)
                                if low<=s2.stp: s2.trig=True; s2.ex=s2.stp; return True
                            else:
                                s2.ext=min(s2.ext,low);ns=s2.ext+(s2.m*atr2);s2.stp=min(s2.stp,ns)
                                if high>=s2.stp: s2.trig=True; s2.ex=s2.stp; return True
                            return False
                    pos['cx'] = CX()
                atr_v = float(atr.iloc[bar_idx]) or 0.01
                if pos['cx'].upd(h, l, atr_v):
                    actual = pos['cx'].ex - sd if d == 'BUY' else pos['cx'].ex + sd
                    net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
                    rv = net/pos['risk'] if pos['risk']>0 else 0; trades.append(rv); eq += net; pos = None

        # Entry
        for sig in entries.get(bar_idx, []):
            if pos: continue
            sa = sd if sig['d'] == 'BUY' else -sd; adj = sig['e'] + sa
            orig_r = abs(sig['e'] - sig['stop'])
            stop_a = adj - orig_r if sig['d'] == 'BUY' else adj + orig_r
            tgt = adj + 2*orig_r if sig['d'] == 'BUY' else adj - 2*orig_r
            risk_amt = eq * RISK_PCT; u = max(1, int(risk_amt/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
            act_r = orig_r * u * spec.ounces_per_unit; spr = 2*sp*spec.pip_value_per_unit*u
            pos = {'d':sig['d'],'entry':adj,'stop':stop_a,'tgt':tgt,'units':u,'risk':act_r,'spr':spr,'ae':sig['a']}

    rvs = np.array(trades) if trades else np.array([0])
    w = sum(1 for r in rvs if r>0); g = sum(abs(r) for r in rvs if r>0); ls = sum(abs(r) for r in rvs if r<0)
    return {'trades': len(trades), 'wr': w/len(trades)*100 if trades else 0,
            'pf': g/ls if ls > 0 else 0, 'pnl': eq-10000, 'avg_r': float(np.mean(rvs)) if len(rvs)>0 else 0}

# ─────────────────────────────────────────────────────────────────────
# SWEEP 1: Chandelier multiplier sweep (2.0x → 8.0x)
# ─────────────────────────────────────────────────────────────────────
print('=' * 68)
print('SWEEP 1: Chandelier Multiplier Scan (no filter)')
print('=' * 68)
print(f"{'Mult':>6}  {'Trades':>8}  {'WR':>6}  {'PF':>7}  {'PnL':>10}  {'AvgR':>7}  {'vs Base':>8}")
print('-' * 60)

baseline = run(pd.Series(True, index=buy_signal.index), 'FIXED_2R')
log_experiment('baseline', 'D4 fixed 2R, no filters', baseline, {'exit':'FIXED_2R'})
print(f"{'Baseline':>6}  {baseline['trades']:>8}  {baseline['wr']:>5.1f}%  {baseline['pf']:>6.3f}  ${baseline['pnl']:>8.0f}  {baseline['avg_r']:>6.3f}  {'—':>8}")

true_filter = pd.Series(True, index=buy_signal.index)
for m in [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 8.0]:
    t0 = time.time()
    r = run(true_filter, 'CHANDELIER', exit_param=m)
    elapsed = time.time() - t0
    label = f'Chandelier {m:.1f}x'
    log_experiment(f'ch_{m:.1f}x', f'Chandelier exit {m:.1f}x ATR', r, {'exit':'CHANDELIER','mult':m})
    vs = ((r['pf'] - baseline['pf']) / baseline['pf']) * 100
    print(f"{m:>4.1f}x  {r['trades']:>8}  {r['wr']:>5.1f}%  {r['pf']:>6.3f}  ${r['pnl']:>8.0f}  {r['avg_r']:>6.3f}  {vs:>+7.1f}%  ({elapsed:.0f}s)")

# ─────────────────────────────────────────────────────────────────────
# SWEEP 2: Shorter lookback (10-bar Donchian) + Chandelier
# ─────────────────────────────────────────────────────────────────────
print()
print('=' * 68)
print('SWEEP 2: 10-bar Donchian lookback (shorter)')
print('=' * 68)
print(f"{'Config':>16}  {'Trades':>8}  {'WR':>6}  {'PF':>7}  {'PnL':>10}  {'AvgR':>7}  {'vs Base':>8}")
print('-' * 60)

# 10-bar baseline with fixed 2R
r10 = run(true_filter, 'FIXED_2R', lookback=10)
log_experiment('donchian_10_fixed2r', '10-bar Donchian + fixed 2R', r10, {'exit':'FIXED_2R','lookback':10})
vs = ((r10['pf'] - baseline['pf']) / baseline['pf']) * 100
print(f"{'10-bar + 2R':>16}  {r10['trades']:>8}  {r10['wr']:>5.1f}%  {r10['pf']:>6.3f}  ${r10['pnl']:>8.0f}  {r10['avg_r']:>6.3f}  {vs:>+7.1f}%")

# 10-bar + chandelier sweep
for m in [4.0, 5.0, 5.5, 6.0, 6.5, 7.0]:
    t0 = time.time()
    r = run(true_filter, 'CHANDELIER', exit_param=m, lookback=10)
    elapsed = time.time() - t0
    label = f'10-bar + Ch {m:.1f}x'
    log_experiment(f'donchian_10_ch_{m:.1f}x', f'10-bar Donchian + Chandelier {m:.1f}x', r, {'exit':'CHANDELIER','mult':m,'lookback':10})
    vs = ((r['pf'] - baseline['pf']) / baseline['pf']) * 100
    print(f"{label:>16}  {r['trades']:>8}  {r['wr']:>5.1f}%  {r['pf']:>6.3f}  ${r['pnl']:>8.0f}  {r['avg_r']:>6.3f}  {vs:>+7.1f}%  ({elapsed:.0f}s)")

# ─────────────────────────────────────────────────────────────────────
# SWEEP 3: 15-bar Donchian (midpoint) + Chandelier
# ─────────────────────────────────────────────────────────────────────
print()
print('=' * 68)
print('SWEEP 3: 15-bar Donchian lookback')
print('=' * 68)
print(f"{'Config':>16}  {'Trades':>8}  {'WR':>6}  {'PF':>7}  {'PnL':>10}  {'AvgR':>7}  {'vs Base':>8}")
print('-' * 60)

r15 = run(true_filter, 'FIXED_2R', lookback=15)
log_experiment('donchian_15_fixed2r', '15-bar Donchian + fixed 2R', r15, {'exit':'FIXED_2R','lookback':15})
vs = ((r15['pf'] - baseline['pf']) / baseline['pf']) * 100
print(f"{'15-bar + 2R':>16}  {r15['trades']:>8}  {r15['wr']:>5.1f}%  {r15['pf']:>6.3f}  ${r15['pnl']:>8.0f}  {r15['avg_r']:>6.3f}  {vs:>+7.1f}%")

for m in [5.0, 5.5, 6.0, 6.5, 7.0]:
    t0 = time.time()
    r = run(true_filter, 'CHANDELIER', exit_param=m, lookback=15)
    elapsed = time.time() - t0
    label = f'15-bar + Ch {m:.1f}x'
    log_experiment(f'donchian_15_ch_{m:.1f}x', f'15-bar Donchian + Chandelier {m:.1f}x', r, {'exit':'CHANDELIER','mult':m,'lookback':15})
    vs = ((r['pf'] - baseline['pf']) / baseline['pf']) * 100
    print(f"{label:>16}  {r['trades']:>8}  {r['wr']:>5.1f}%  {r['pf']:>6.3f}  ${r['pnl']:>8.0f}  {r['avg_r']:>6.3f}  {vs:>+7.1f}%  ({elapsed:.0f}s)")

print()
print(f'Full log saved to: {LOG_FILE}')
print('Done.')
