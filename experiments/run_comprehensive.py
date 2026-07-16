"""Full systematic test suite for 10-bar Donchian improvements."""
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

ohlcv = load_ohlcv('M15', 'aurum1/data/combined_market_cache.sqlite3')
features = build_research_features(ohlcv)
settings = load_settings('aurum1/config/settings.yaml')
spec = InstrumentSpec.from_settings(settings)
RISK_PCT = 0.0025; sp = 1.5; slip = 0.5; sd = slip * spec.pip_size
atr = features['atr_14']

LOG_FILE = Path(__file__).resolve().parent / 'results' / 'sweep_002_10bar_comprehensive.csv'
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

def log_result(exp_id, desc, result, config):
    is_new = not LOG_FILE.exists()
    with open(LOG_FILE, 'a', newline='') as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(['experiment_id','timestamp','description','trades','wr_pct','pf','pnl','avg_r','max_dd','config_json'])
        max_dd = result.get('max_dd', 0)
        w.writerow([exp_id, datetime.now(UTC).isoformat(), desc,
                    result['trades'], f"{result['wr']:.1f}", f"{result['pf']:.3f}",
                    f"{result['pnl']:+.2f}", f"{result['avg_r']:.4f}", f"{max_dd:.4f}",
                    json.dumps(config)])

def run(entry_filter=None, exit_mode='FIXED_2R', exit_param=None, lookback=10,
        vol_scale=False, equity_curve=False):
    """Run backtest with 10-bar Donchian and optional features.

    Uses features DataFrame for signal computation (matches batch_test.py).
    """
    if entry_filter is None:
        entry_filter = pd.Series(True, index=features.index)

    LOOKBACK = lookback
    ub = features['high'].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
    lb = features['low'].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
    b_sig = features['close'] > ub
    s_sig = features['close'] < lb
    valid = features['atr_14'].notna()
    b_sig = b_sig & valid & entry_filter
    s_sig = s_sig & valid & entry_filter

    entries = {}
    for d, mask in [('BUY', b_sig), ('SELL', s_sig)]:
        for st in features.index[mask.fillna(False)]:
            bar = int(ohlcv.index.get_loc(st)); eb = bar+1
            if eb >= len(ohlcv): continue
            e = float(ohlcv.iloc[eb]['open']); a = float(features.loc[st, 'atr_14'])
            if not math.isfinite(a) or a <= 0: continue
            stop = e - 2*a if d == 'BUY' else e + 2*a
            if (d == 'BUY' and stop >= e) or (d == 'SELL' and stop <= e): continue
            entries.setdefault(eb, []).append({'d':d,'e':e,'stop':stop,'a':a})

    eq = 10000.0; pos = None; trades = []; eq_curve = [10000.0]; peak_eq = 10000.0
    for bar_idx, (ts, row) in enumerate(features.iterrows()):
        # Position management
        if pos:
            o,h,l = float(row['open']),float(row['high']),float(row['low']); d = pos['d']
            # Risk management: check spread
            if pos is not None and pos.get('spr_val', 0) > 3.0 * spec.pip_size:
                actual = float(row['close']); actual += -sd if d == 'BUY' else sd
                net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
                rv = net/pos['risk'] if pos['risk']>0 else 0; trades.append(rv); eq += net; pos = None
                eq_curve.append(eq); peak_eq = max(peak_eq, eq); continue

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
            elif exit_mode == 'PARTIAL_1R':
                if not pos.get('stage'):
                    pos['stage'] = 0
                    pos['tp1'] = pos['entry'] + pos['orig_r'] if d == 'BUY' else pos['entry'] - pos['orig_r']
                    pos['first_half'] = 0
                if pos['stage'] == 0:
                    atr_v2 = float(atr.iloc[bar_idx]) or 0.01
                    if (d == 'BUY' and l <= pos['stop']) or (d == 'SELL' and h >= pos['stop']):
                        ex = pos['stop']; actual = ex - sd if d == 'BUY' else ex + sd
                        net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
                        rv = net/pos['risk']; trades.append(rv); eq += net; pos = None
                        continue
                    elif (d == 'BUY' and h >= pos['tp1']) or (d == 'SELL' and l <= pos['tp1']):
                        # Close half
                        actual_half = pos['tp1'] - sd if d == 'BUY' else pos['tp1'] + sd
                        pos['first_half'] = spec.pnl(d, pos['entry'], actual_half, pos['units']/2)
                        eq += pos['first_half'] - 0
                        pos['stage'] = 1
                        pos['trail'] = pos['entry'] + sd if d == 'BUY' else pos['entry'] - sd
                        pos['extreme'] = pos['entry']
                        continue
                    pos['bars'] = pos.get('bars', 0) + 1
                    if pos['bars'] > 192:
                        actual = float(row['close']); actual += -sd if d == 'BUY' else sd
                        net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
                        rv = net/pos['risk']; trades.append(rv); eq += net; pos = None
                if pos is not None and pos['stage'] == 1:
                    atr_v2 = float(atr.iloc[bar_idx]) or 0.01
                    trail_mult = exit_param or 2.0
                    if d == 'BUY':
                        pos['extreme'] = max(pos['extreme'], h)
                        ns = pos['extreme'] - (trail_mult * atr_v2)
                        pos['trail'] = max(pos['trail'], ns)
                        if l <= pos['trail']:
                            actual_rem = pos['trail'] - sd
                            second = spec.pnl(d, pos['entry'], actual_rem, pos['units']/2)
                            total_r = (pos['first_half'] + second) / pos['risk']; trades.append(total_r); eq += second; pos = None
                    else:
                        pos['extreme'] = min(pos['extreme'], l)
                        ns = pos['extreme'] + (trail_mult * atr_v2)
                        pos['trail'] = min(pos['trail'], ns)
                        if h >= pos['trail']:
                            actual_rem = pos['trail'] + sd
                            second = spec.pnl(d, pos['entry'], actual_rem, pos['units']/2)
                            total_r = (pos['first_half'] + second) / pos['risk']; trades.append(total_r); eq += second; pos = None

        # Entry
        for sig in entries.get(bar_idx, []):
            if pos: continue
            sa = sd if sig['d'] == 'BUY' else -sd; adj = sig['e'] + sa
            orig_r = abs(sig['e'] - sig['stop'])
            stop_a = adj - orig_r if sig['d'] == 'BUY' else adj + orig_r
            tgt = adj + 2*orig_r if sig['d'] == 'BUY' else adj - 2*orig_r
            risk_amt = eq * RISK_PCT
            if vol_scale:
                stop_dist = abs(adj - stop_a)
                risk_amt = eq * RISK_PCT * (features['atr_14'].median() / max(features['atr_14'].iloc[bar_idx], 0.01)) if bar_idx > 100 else risk_amt
            u = max(1, int(risk_amt/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
            act_r = orig_r * u * spec.ounces_per_unit; spr = 2*sp*spec.pip_value_per_unit*u
            pos = {'d':sig['d'],'entry':adj,'stop':stop_a,'tgt':tgt,'units':u,'risk':act_r,'spr':spr,'ae':sig['a'],'orig_r':orig_r,'spr_val':2*sp*spec.pip_value_per_unit*u}

        eq_curve.append(eq)
        peak_eq = max(peak_eq, eq)

    rvs = np.array(trades) if trades else np.array([0])
    w = sum(1 for r in rvs if r>0); g = sum(abs(r) for r in rvs if r>0); ls = sum(abs(r) for r in rvs if r<0)
    dd = 0
    if equity_curve and len(eq_curve) > 1:
        peak = np.maximum.accumulate(eq_curve)
        dd = np.min(eq_curve / peak - 1)
    result = {'trades': len(trades), 'wr': w/len(trades)*100 if trades else 0,
              'pf': g/ls if ls > 0 else 0, 'pnl': eq-10000, 'avg_r': float(np.mean(rvs)) if len(rvs)>0 else 0, 'max_dd': abs(dd)}
    if equity_curve:
        result['eq_curve'] = eq_curve
    return result

# ── Run everything ──

# Create all-true filter (no filter applied)
true_filter = pd.Series(True, index=features.index)

print('=' * 72)
print('SWEEP 002: 10-BAR DONCHIAN FULL IMPROVEMENT SCAN')
print(f'Data: {len(ohlcv)} candles ({ohlcv.index[0]} to {ohlcv.index[-1]})')
print('=' * 72)

# ── 1. BASELINES (10-bar vs 20-bar for reference) ──
print('\n[1/8] BASELINES')
r20 = run(true_filter, 'FIXED_2R', lookback=20)
log_result('baseline_20r', '20-bar Donchian + fixed 2R (current D4)', r20, {'lookback':20,'exit':'FIXED_2R'})
print(f'  20-bar + 2R:     {r20["trades"]:>5}t  WR:{r20["wr"]:>5.1f}%  PF:{r20["pf"]:.3f}  PnL:${r20["pnl"]:>+.0f}')

r10 = run(true_filter, 'FIXED_2R', lookback=10)
log_result('baseline_10r', '10-bar Donchian + fixed 2R', r10, {'lookback':10,'exit':'FIXED_2R'})
print(f'  10-bar + 2R:     {r10["trades"]:>5}t  WR:{r10["wr"]:>5.1f}%  PF:{r10["pf"]:.3f}  PnL:${r10["pnl"]:>+.0f}')

# ── 2. CHANDELIER ON 10-BAR ──
print('\n[2/8] 10-BAR + CHANDELIER SWEEP')
for m in [5.0, 5.5, 6.0, 6.5]:
    r = run(true_filter, 'CHANDELIER', exit_param=m, lookback=10)
    log_result(f'10bar_ch_{m:.1f}x', f'10-bar + Chandelier {m:.1f}x', r, {'lookback':10,'exit':'CHANDELIER','mult':m})
    vs = ((r['pf'] - r10['pf'])/r10['pf'])*100
    print(f'  Ch {m:.1f}x:       {r["trades"]:>5}t  WR:{r["wr"]:>5.1f}%  PF:{r["pf"]:.3f}  PnL:${r["pnl"]:>+.0f}  {vs:>+.1f}% vs 10-bar')

# ── 3. VOLATILITY SCALING ──
print('\n[3/8] VOLATILITY SCALING (constant risk)')
r = run(true_filter, 'FIXED_2R', lookback=10, vol_scale=True)
log_result('10bar_volscale_fixed2r', '10-bar + vol scaling + fixed 2R', r, {'lookback':10,'exit':'FIXED_2R','vol_scale':True})
vs = ((r['pf'] - r10['pf'])/r10['pf'])*100
print(f'  VolScale + 2R:    {r["trades"]:>5}t  WR:{r["wr"]:>5.1f}%  PF:{r["pf"]:.3f}  PnL:${r["pnl"]:>+.0f}  {vs:>+.1f}%')

r = run(true_filter, 'CHANDELIER', exit_param=6.0, lookback=10, vol_scale=True)
log_result('10bar_volscale_ch6x', '10-bar + vol scaling + Chandelier 6.0x', r, {'lookback':10,'exit':'CHANDELIER','mult':6.0,'vol_scale':True})
vs = ((r['pf'] - r10['pf'])/r10['pf'])*100
print(f'  VolScale + Ch6x:  {r["trades"]:>5}t  WR:{r["wr"]:>5.1f}%  PF:{r["pf"]:.3f}  PnL:${r["pnl"]:>+.0f}  {vs:>+.1f}%')

# ── 4. PARTIAL TP (50% at 1R + trail) ──
print('\n[4/8] PARTIAL TP (50% @ 1R + trail)')
for m in [1.5, 2.0, 2.5, 3.0]:
    r = run(true_filter, 'PARTIAL_1R', exit_param=m, lookback=10)
    log_result(f'10bar_partial_{m:.1f}x', f'10-bar + 50%@1R + trail {m:.1f}x', r, {'lookback':10,'exit':'PARTIAL_1R','trail_mult':m})
    vs = ((r['pf'] - r10['pf'])/r10['pf'])*100
    print(f'  50%@1R + Tr {m:.1f}x:  {r["trades"]:>5}t  WR:{r["wr"]:>5.1f}%  PF:{r["pf"]:.3f}  PnL:${r["pnl"]:>+.0f}  {vs:>+.1f}%')

# ── 5. ADX FILTERS ──
print('\n[5/8] ADX TREND STRENGTH FILTERS (on 10-bar)')
buy_sig = features['close'] > features['high'].rolling(10, min_periods=10).max().shift(1)
sell_sig = features['close'] < features['low'].rolling(10, min_periods=10).min().shift(1)

# Compute ADX properly
high, low, close = features['high'], features['low'], features['close']
up_move = high.diff(); down_move = -low.diff()
plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)
tr = pd.concat([high - low, (high-close.shift(1)).abs(), (low-close.shift(1)).abs()], axis=1).max(axis=1)
ar = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
pdi = 100.0 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / ar.replace(0, np.nan)
mdi = 100.0 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / ar.replace(0, np.nan)
adx_val = (100.0 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)).ewm(alpha=1/14, adjust=False).mean()

for name, cond in [('ADX > 20', adx_val.shift(1) > 20), ('ADX > 25', adx_val.shift(1) > 25),
                    ('ADX > 30', adx_val.shift(1) > 30), ('ADX 20-30', (adx_val.shift(1) > 20) & (adx_val.shift(1) < 30))]:
    valid_adx = atr.notna() & cond
    entries = {}
    for d, mask in [('BUY', buy_sig & valid_adx), ('SELL', sell_sig & valid_adx)]:
        for st in features.index[mask.fillna(False)]:
            bar = int(ohlcv.index.get_loc(st)); eb = bar+1
            if eb >= len(ohlcv): continue
            e = float(ohlcv.iloc[eb]['open']); a = float(features.loc[st, 'atr_14'])
            if not math.isfinite(a) or a <= 0: continue
            stop = e - 2*a if d == 'BUY' else e + 2*a
            if (d == 'BUY' and stop >= e) or (d == 'SELL' and stop <= e): continue
            entries.setdefault(eb, []).append({'d':d,'e':e,'stop':stop,'a':a})
    eq = 10000.0; pos = None; trades = []
    for bar_idx, (ts, row) in enumerate(features.iterrows()):
        if pos:
            o,h,l = float(row['open']),float(row['high']),float(row['low']); d = pos['d']
            if (d == 'BUY' and l <= pos['stop']) or (d == 'SELL' and h >= pos['stop']):
                ex = pos['stop']; actual = ex - sd if d == 'BUY' else ex + sd
                net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
                rv = net/pos['risk'] if pos['risk']>0 else 0; trades.append(rv); eq += net; pos = None
            elif (d == 'BUY' and h >= pos['tgt']) or (d == 'SELL' and l <= pos['tgt']):
                ex = pos['tgt']; actual = ex - sd if d == 'BUY' else ex + sd
                net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
                rv = net/pos['risk']; trades.append(rv); eq += net; pos = None
        for sig in entries.get(bar_idx, []):
            if pos: continue
            sa = sd if sig['d'] == 'BUY' else -sd; adj = sig['e'] + sa; orig_r = abs(sig['e'] - sig['stop'])
            stop_a = adj - orig_r if sig['d'] == 'BUY' else adj + orig_r
            tgt = adj + 2*orig_r if sig['d'] == 'BUY' else adj - 2*orig_r
            risk_amt = eq * RISK_PCT; u = max(1, int(risk_amt/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
            act_r = orig_r * u * spec.ounces_per_unit; spr = 2*sp*spec.pip_value_per_unit*u
            pos = {'d':sig['d'],'entry':adj,'stop':stop_a,'tgt':tgt,'units':u,'risk':act_r,'spr':spr}
    rvs = np.array(trades) if trades else np.array([0])
    w = sum(1 for r in rvs if r>0); g = sum(abs(r) for r in rvs if r>0); ls = sum(abs(r) for r in rvs if r<0)
    res = {'trades': len(trades), 'wr': w/len(trades)*100 if trades else 0, 'pf': g/ls if ls > 0 else 0, 'pnl': eq-10000, 'avg_r': float(np.mean(rvs)) if len(rvs)>0 else 0}
    log_result(f'10bar_adx_{name.replace(" ","_")}', f'10-bar + {name} + fixed 2R', res, {'lookback':10,'exit':'FIXED_2R','filter':name})
    vs_pct = ((res['pf'] - r10['pf'])/r10['pf'])*100 if r10['pf'] > 0 else 0
    print(f'  {name:<12}  {res["trades"]:>5}t  WR:{res["wr"]:>5.1f}%  PF:{res["pf"]:.3f}  PnL:${res["pnl"]:>+.0f}  {vs_pct:>+.1f}%')

# ── 6. MULTI-ASSET VALIDATION (GC=F daily, 26 years) ──
print('\n[6/8] GC=F DAILY VALIDATION (26 years, out-of-sample)')
try:
    gc = pd.read_csv(Path(__file__).resolve().parents[1] / 'aurum1' / 'data' / 'gc_futures_daily_2000_2026.csv',
                     index_col=0, parse_dates=True)
    for c in ['Open','High','Low','Close','Volume']:
        gc[c] = pd.to_numeric(gc[c], errors='coerce')
    gc = gc.dropna(subset=['Close'])

    for lb in [10, 15, 20]:
        ub_gc = gc['High'].rolling(lb).max().shift(1)
        lb_gc = gc['Low'].rolling(lb).min().shift(1)
        tr_gc = pd.concat([gc['High']-gc['Low'], (gc['High']-gc['Close'].shift(1)).abs(), (gc['Low']-gc['Close'].shift(1)).abs()], axis=1).max(axis=1)
        atr_gc = tr_gc.ewm(alpha=1/14, min_periods=14).mean()
        b_sig = gc['Close'] > ub_gc; s_sig = gc['Close'] < lb_gc
        valid_gc = atr_gc.notna(); b_sig = b_sig & valid_gc; s_sig = s_sig & valid_gc
        eq = 10000.0; pos = None; trades = []
        for i in range(lb + 2, len(gc)):
            if pos:
                o,h,l = gc.iloc[i][['Open','High','Low']]; d = pos['d']
                if (d == 'BUY' and l <= pos['stop']) or (d == 'SELL' and h >= pos['stop']):
                    net = (pos['stop']-pos['entry']) if d=='BUY' else (pos['entry']-pos['stop'])
                    rv = net/pos['risk']; trades.append(rv); eq += net; pos = None
                elif (d == 'BUY' and h >= pos['tgt']) or (d == 'SELL' and l <= pos['tgt']):
                    net = (pos['tgt']-pos['entry']) if d=='BUY' else (pos['entry']-pos['tgt'])
                    rv = net/pos['risk']; trades.append(rv); eq += net; pos = None
            if pos: continue
            a = float(atr_gc.iloc[i]); c = float(gc['Close'].iloc[i])
            if pd.isna(a) or a <= 0: continue
            ub_v, lb_v = float(ub_gc.iloc[i]), float(lb_gc.iloc[i])
            if c > ub_v:
                sl = c - 2*a; tp = c + 4*a; risk = abs(c - sl)
                pos = {'d':'BUY','entry':c,'stop':sl,'tgt':tp,'risk':risk}
            elif c < lb_v:
                sl = c + 2*a; tp = c - 4*a; risk = abs(c - sl)
                pos = {'d':'SELL','entry':c,'stop':sl,'tgt':tp,'risk':risk}
        rvs = np.array(trades) if trades else np.array([0])
        w = sum(1 for r in rvs if r>0); g = sum(abs(r) for r in rvs if r>0); ls = sum(abs(r) for r in rvs if r<0)
        res = {'trades': len(trades), 'wr': w/len(trades)*100 if trades else 0, 'pf': g/ls if ls > 0 else 0, 'pnl': eq-10000, 'avg_r': float(np.mean(rvs)) if len(rvs)>0 else 0}
        log_result(f'gcf_{lb}bar', f'GC=F daily {lb}-bar Donchian', res, {'instrument':'GC=F','lookback':lb,'exit':'FIXED_2R'})
        print(f'  GC=F {lb}-bar + 2R:  {res["trades"]:>4}t  WR:{res["wr"]:>5.1f}%  PF:{res["pf"]:.3f}  PnL:${res["pnl"]:>+.0f}')
except Exception as e:
    print(f'  GC=F validation FAILED: {e}')

# ── 7. GLD VALIDATION (22 years) ──
print('\n[7/8] GLD ETF VALIDATION (22 years, out-of-sample)')
try:
    gld = pd.read_csv(Path(__file__).resolve().parents[1] / 'aurum1' / 'data' / 'gld_etf_daily_2004_2026.csv',
                      index_col=0, parse_dates=True)
    for c in ['Open','High','Low','Close','Volume']:
        gld[c] = pd.to_numeric(gld[c], errors='coerce')
    gld = gld.dropna(subset=['Close'])

    for lb in [10, 15, 20]:
        ub_g = gld['High'].rolling(lb).max().shift(1)
        lb_g = gld['Low'].rolling(lb).min().shift(1)
        tr_g = pd.concat([gld['High']-gld['Low'], (gld['High']-gld['Close'].shift(1)).abs(), (gld['Low']-gld['Close'].shift(1)).abs()], axis=1).max(axis=1)
        atr_g = tr_g.ewm(alpha=1/14, min_periods=14).mean()
        b_sig = gld['Close'] > ub_g; s_sig = gld['Close'] < lb_g
        valid_g = atr_g.notna(); b_sig = b_sig & valid_g; s_sig = s_sig & valid_g
        eq = 10000.0; pos = None; trades = []
        for i in range(lb + 2, len(gld)):
            if pos:
                o,h,l = gld.iloc[i][['Open','High','Low']]; d = pos['d']
                if (d == 'BUY' and l <= pos['stop']) or (d == 'SELL' and h >= pos['stop']):
                    net = (pos['stop']-pos['entry']) if d=='BUY' else (pos['entry']-pos['stop']); rv = net/pos['risk']; trades.append(rv); eq += net; pos = None
                elif (d == 'BUY' and h >= pos['tgt']) or (d == 'SELL' and l <= pos['tgt']):
                    net = (pos['tgt']-pos['entry']) if d=='BUY' else (pos['entry']-pos['tgt']); rv = net/pos['risk']; trades.append(rv); eq += net; pos = None
            if pos: continue
            a = float(atr_g.iloc[i]); c = float(gld['Close'].iloc[i])
            if pd.isna(a) or a <= 0: continue
            ub_v, lb_v = float(ub_g.iloc[i]), float(lb_g.iloc[i])
            if c > ub_v:
                sl = c - 2*a; tp = c + 4*a; pos = {'d':'BUY','entry':c,'stop':sl,'tgt':tp,'risk':abs(c-sl)}
            elif c < lb_v:
                sl = c + 2*a; tp = c - 4*a; pos = {'d':'SELL','entry':c,'stop':sl,'tgt':tp,'risk':abs(c-sl)}
        rvs = np.array(trades) if trades else np.array([0])
        w = sum(1 for r in rvs if r>0); g = sum(abs(r) for r in rvs if r>0); ls = sum(abs(r) for r in rvs if r<0)
        res = {'trades': len(trades), 'wr': w/len(trades)*100 if trades else 0, 'pf': g/ls if ls > 0 else 0, 'pnl': eq-10000, 'avg_r': float(np.mean(rvs)) if len(rvs)>0 else 0}
        log_result(f'gld_{lb}bar', f'GLD daily {lb}-bar Donchian', res, {'instrument':'GLD','lookback':lb,'exit':'FIXED_2R'})
        print(f'  GLD {lb}-bar + 2R:    {res["trades"]:>4}t  WR:{res["wr"]:>5.1f}%  PF:{res["pf"]:.3f}  PnL:${res["pnl"]:>+.0f}')
except Exception as e:
    print(f'  GLD validation FAILED: {e}')

# ── 8. DRAW DOWN ESTIMATION ──
print('\n[8/8] MAX DD ESTIMATION (10-bar + fixed 2R)')
r_dd = run(true_filter, 'FIXED_2R', lookback=10, equity_curve=True)
max_dd_pct = f"{r_dd['max_dd']*100:.1f}%"
print(f'  10-bar + 2R max DD: {max_dd_pct}')

print(f'\nFull log saved to: {LOG_FILE}')
print('COMPLETE.')
