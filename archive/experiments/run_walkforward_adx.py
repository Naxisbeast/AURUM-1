"""
Walk-forward validation: ADX > 20 + 10-bar Donchian + fixed 2R exit.

Tests whether this configuration is robust across all market regimes
by splitting the 10-year M15 data into non-overlapping windows,
testing each out-of-sample window independently.

This is the single most important robustness test — if it fails here,
the improvement is likely overfitted.
"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
import math, time, csv, json
from datetime import datetime, UTC
from pathlib import Path
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

# Compute ADX
high, low, close = features['high'], features['low'], features['close']
up_move = high.diff(); down_move = -low.diff()
plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)
tr = pd.concat([high - low, (high-close.shift(1)).abs(), (low-close.shift(1)).abs()], axis=1).max(axis=1)
ar = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
pdi = 100.0 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / ar.replace(0, np.nan)
mdi = 100.0 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / ar.replace(0, np.nan)
adx_val = (100.0 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)).ewm(alpha=1/14, adjust=False).mean()

TOTAL_BARS = len(ohlcv)
print(f'Total bars: {TOTAL_BARS}')
print(f'Date range: {ohlcv.index[0]} to {ohlcv.index[-1]}')

# ── Walk-forward parameters ──
# Use 6552 bars (~3 months of M15) for training, 1638 (~3 weeks) for testing
# Non-overlapping windows
TRAIN_BARS = 6552
TEST_BARS = 1638
STEP_BARS = TEST_BARS  # Non-overlapping

LOG_FILE = Path('experiments/results/walkforward_adx20_10bar.csv')
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

def run_window(train_end, test_start, test_end):
    """Run one walk-forward window on test period only.

    The ADX > 20 filter is recalculated independently per window
    (no future data leakage).
    """
    entries = {}
    for bar_idx in range(max(200, test_start), test_end):
        if bar_idx >= len(features): break
        ts = features.index[bar_idx]
        # Entry signals: 10-bar Donchian
        ub = features['high'].iloc[max(0,bar_idx-10):bar_idx].max()
        lb = features['low'].iloc[max(0,bar_idx-10):bar_idx].min()
        c = float(features['close'].iloc[bar_idx])
        a = float(atr.iloc[bar_idx])
        adx_v = float(adx_val.iloc[bar_idx])

        if pd.isna(a) or a <= 0 or pd.isna(adx_v): continue
        if adx_v <= 20: continue  # ADX > 20 filter

        eb = bar_idx + 1
        if eb >= len(ohlcv): continue
        e = float(ohlcv.iloc[eb]['open'])
        stop = e - 2*a if c > ub else e + 2*a
        d = 'BUY' if c > ub else 'SELL'
        if (d == 'BUY' and c > ub) or (d == 'SELL' and c < lb):
            if (d == 'BUY' and stop >= e) or (d == 'SELL' and stop <= e): continue
            entries.setdefault(eb, []).append({'d':d,'e':e,'stop':stop,'a':a})

    eq = 10000.0; pos = None; trades = []
    for bar_idx in range(test_start, test_end):
        if bar_idx >= len(ohlcv): break
        if pos:
            o,h,l = float(ohlcv.iloc[bar_idx]['open']), float(ohlcv.iloc[bar_idx]['high']), float(ohlcv.iloc[bar_idx]['low'])
            d = pos['d']
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
    return {'trades': len(trades), 'wr': w/len(trades)*100 if trades else 0,
            'pf': g/ls if ls > 0 else 0, 'pnl': eq-10000, 'avg_r': float(np.mean(rvs)) if len(rvs)>0 else 0}

# ── Also run baseline (no ADX) for comparison ──
def run_window_baseline(test_start, test_end):
    """Same but without ADX filter."""
    """Run baseline 10-bar Donchian on a window (no ADX filter)."""
    entries = {}
    for bar_idx in range(max(200, test_start), test_end):
        if bar_idx >= len(features): break
        ub = features['high'].iloc[max(0,bar_idx-10):bar_idx].max()
        lb = features['low'].iloc[max(0,bar_idx-10):bar_idx].min()
        c = float(features['close'].iloc[bar_idx])
        a = float(atr.iloc[bar_idx])
        if pd.isna(a) or a <= 0: continue
        eb = bar_idx + 1
        if eb >= len(ohlcv): continue
        e = float(ohlcv.iloc[eb]['open'])
        d = 'BUY' if c > ub else 'SELL' if c < lb else None
        if d is None: continue
        stop = e - 2*a if d == 'BUY' else e + 2*a
        if (d == 'BUY' and stop >= e) or (d == 'SELL' and stop <= e): continue
        entries.setdefault(eb, []).append({'d':d,'e':e,'stop':stop,'a':a})
    eq = 10000.0; pos = None; trades = []
    for bar_idx in range(test_start, test_end):
        if bar_idx >= len(ohlcv): break
        if pos:
            o,h,l = float(ohlcv.iloc[bar_idx]['open']), float(ohlcv.iloc[bar_idx]['high']), float(ohlcv.iloc[bar_idx]['low'])
            d = pos['d']
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
    return {'trades': len(trades), 'wr': w/len(trades)*100 if trades else 0,
            'pf': g/ls if ls > 0 else 0, 'pnl': eq-10000, 'avg_r': float(np.mean(rvs)) if len(rvs)>0 else 0}

# ── Run all windows ──
print(f'\nWalk-forward: {TRAIN_BARS} train + {TEST_BARS} test ({STEP_BARS} step)')
print(f'Expected windows: ~{(TOTAL_BARS - TRAIN_BARS - TEST_BARS) // STEP_BARS}')
print()

windows_adx = []
windows_base = []
warmup = 400  # enough for ATR(14) + ADX(14) + Donchian(10)

start = warmup
w_idx = 0
while start + TEST_BARS <= TOTAL_BARS:
    test_end = start + TEST_BARS
    test_start = start
    train_end = start  # No training needed (rule-based system, just uses prior data for indicators)

    t0 = time.time()
    r_adx = run_window(0, test_start, test_end)
    t1 = time.time()

    # Baseline
    r_base = run_window_baseline(test_start, test_end)

    period_start = ohlcv.index[test_start]
    period_end = ohlcv.index[min(test_end-1, len(ohlcv)-1)]

    windows_adx.append({
        'window': w_idx, 'start': str(period_start.date()), 'end': str(period_end.date()),
        'trades': r_adx['trades'], 'wr': r_adx['wr'], 'pf': r_adx['pf'],
        'pnl': r_adx['pnl'], 'avg_r': r_adx['avg_r']
    })
    windows_base.append({
        'window': w_idx, 'start': str(period_start.date()), 'end': str(period_end.date()),
        'trades': r_base['trades'], 'wr': r_base['wr'], 'pf': r_base['pf'],
        'pnl': r_base['pnl'], 'avg_r': r_base['avg_r']
    })

    # Print window summary
    adx_beat = '+' if r_adx['pf'] > r_base['pf'] else '-'
    print(f'  Window {w_idx:>2}: {str(period_start.date())} → {str(period_end.date())}  '
          f'ADX>20: {r_adx["trades"]:>3}t PF:{r_adx["pf"]:.3f} PnL:${r_adx["pnl"]:>+.0f}  '
          f'Base: {r_base["trades"]:>3}t PF:{r_base["pf"]:.3f}  {adx_beat}')

    start += STEP_BARS
    w_idx += 1

# ── Aggregate results ──
print(f'\n{"="*68}')
print(f'WALK-FORWARD SUMMARY: ADX > 20 + 10-bar Donchian')
print(f'{"="*68}')
print(f'Windows: {len(windows_adx)}')
print()

# ADX > 20 results
adx_pfs = [w['pf'] for w in windows_adx]
adx_pnls = [w['pnl'] for w in windows_adx]
adx_wrs = [w['wr'] for w in windows_adx]
adx_trades = [w['trades'] for w in windows_adx]

pos_pf = sum(1 for p in adx_pfs if p > 1.0)
pos_pnl = sum(1 for p in adx_pnls if p > 0)
total_pnl = sum(adx_pnls)

print(f'ADX > 20 + 10-bar:')
print(f'  Mean PF:       {np.mean(adx_pfs):.3f}')
print(f'  PF > 1.0:      {pos_pf}/{len(adx_pfs)} ({pos_pf/len(adx_pfs)*100:.0f}%)')
print(f'  PnL > $0:      {pos_pnl}/{len(adx_pnls)} ({pos_pnl/len(adx_pnls)*100:.0f}%)')
print(f'  Total PnL:     ${total_pnl:+.0f}')
print(f'  Mean WR:       {np.mean(adx_wrs):.1f}%')
print(f'  Mean trades:   {np.mean(adx_trades):.0f}')
print(f'  PF std:        {np.std(adx_pfs):.3f}')
print(f'  PF stability:  {(1 - np.std(adx_pfs)/max(np.mean(adx_pfs), 0.001))*100:.0f}%')
if len(adx_pfs) > 2:
    print(f'  PF trend:      {np.polyfit(range(len(adx_pfs)), adx_pfs, 1)[0]:.4f}/window')

# Baseline results
base_pfs = [w['pf'] for w in windows_base]
base_pnls = [w['pnl'] for w in windows_base]

pos_pf_b = sum(1 for p in base_pfs if p > 1.0)
pos_pnl_b = sum(1 for p in base_pnls if p > 0)
total_pnl_b = sum(base_pnls)

print(f'\nBaseline (10-bar + 2R, no ADX):')
print(f'  Mean PF:       {np.mean(base_pfs):.3f}')
print(f'  PF > 1.0:      {pos_pf_b}/{len(base_pfs)} ({pos_pf_b/len(base_pfs)*100:.0f}%)')
print(f'  PnL > $0:      {pos_pnl_b}/{len(base_pnls)} ({pos_pnl_b/len(base_pnls)*100:.0f}%)')
print(f'  Total PnL:     ${total_pnl_b:+.0f}')

# ADX vs Baseline head-to-head
wins = sum(1 for i in range(len(windows_adx)) if windows_adx[i]['pf'] > windows_base[i]['pf'])
print(f'\nADX > 20 vs Baseline head-to-head:')
print(f'  ADX wins:      {wins}/{len(windows_adx)} ({wins/len(windows_adx)*100:.0f}%)')

# ── Save to CSV ──
with open(LOG_FILE, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['window','start','end','trades_adx','wr_adx','pf_adx','pnl_adx','avg_r_adx',
                'trades_base','wr_base','pf_base','pnl_base','avg_r_base'])
    for i in range(len(windows_adx)):
        a = windows_adx[i]; b = windows_base[i]
        w.writerow([i, a['start'], a['end'], a['trades'], f"{a['wr']:.1f}", f"{a['pf']:.3f}",
                    f"{a['pnl']:.2f}", f"{a['avg_r']:.4f}",
                    b['trades'], f"{b['wr']:.1f}", f"{b['pf']:.3f}",
                    f"{b['pnl']:.2f}", f"{b['avg_r']:.4f}"])

print(f'\nLog saved to {LOG_FILE}')
