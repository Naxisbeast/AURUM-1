"""Run hybrid exit experiment: 50% at 1R + breakeven + Chandelier trail."""
import sys; sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from aurum1.data.ingestion import load_ohlcv, load_settings
from scripts.research.research_edge_prototypes import build_research_features
from aurum1.instruments import InstrumentSpec
import math, numpy as np

ohlcv = load_ohlcv('M15', 'aurum1/data/combined_market_cache.sqlite3')
features = build_research_features(ohlcv)
settings = load_settings('aurum1/config/settings.yaml')
spec = InstrumentSpec.from_settings(settings)
LOOKBACK = 20; RISK_PCT = 0.0025; sp = 1.5; slip = 0.5; sd = slip * spec.pip_size
TRAIL_MULT = 2.0

buy_m = features['close'] > features['high'].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
sell_m = features['close'] < features['low'].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
valid = features['atr_14'].notna()
buy_m = buy_m & valid; sell_m = sell_m & valid

entries = {}
for d, mask in [('BUY', buy_m), ('SELL', sell_m)]:
    for st in features.index[mask.fillna(False)]:
        bar = int(ohlcv.index.get_loc(st)); eb = bar+1
        if eb >= len(ohlcv): continue
        e = float(ohlcv.iloc[eb]['open']); a = float(features.loc[st, 'atr_14'])
        if not math.isfinite(a) or a <= 0: continue
        stop = e - 2*a if d == 'BUY' else e + 2*a
        if (d == 'BUY' and stop >= e) or (d == 'SELL' and stop <= e): continue
        entries.setdefault(eb, []).append({'d':d,'e':e,'stop':stop,'a':a})

eq = 10000.0; pos = None; trades = []

for bar_idx, (ts, row) in enumerate(ohlcv.iterrows()):
    if pos:
        o,h,l = float(row['open']),float(row['high']),float(row['low'])
        d = pos['d']
        atr = float(features['atr_14'].iloc[bar_idx]) or 0.01

        # Stage 0: waiting for partial TP at 1R
        if pos['stage'] == 0:
            sl_hit = (d == 'BUY' and l <= pos['stop']) or (d == 'SELL' and h >= pos['stop'])
            if sl_hit:
                ex = pos['stop']
                actual = ex - sd if d == 'BUY' else ex + sd
                gross = spec.pnl(d, pos['entry'], actual, pos['units'])
                net = gross - pos['spr']; rv = net/pos['risk'] if pos['risk']>0 else 0
                trades.append(rv); eq += net; pos = None; continue

            tp_hit = (d == 'BUY' and h >= pos['tp1']) or (d == 'SELL' and l <= pos['tp1'])
            if tp_hit:
                actual_half = pos['tp1'] - sd if d == 'BUY' else pos['tp1'] + sd
                pos['first_half'] = spec.pnl(d, pos['entry'], actual_half, pos['units'] / 2)
                eq += pos['first_half']
                pos['stage'] = 1
                be = pos['entry'] + sd if d == 'BUY' else pos['entry'] - sd
                pos['trail'] = be
                pos['extreme'] = pos['entry']
                continue

            pos['bars'] += 1
            if pos['bars'] > 192:
                ex = float(row['close'])
                actual = ex - sd if d == 'BUY' else ex + sd
                gross = spec.pnl(d, pos['entry'], actual, pos['units'])
                net = gross - pos['spr']; rv = net/pos['risk'] if pos['risk']>0 else 0
                trades.append(rv); eq += net; pos = None; continue

        # Stage 1: trailing stop on remaining half
        if pos['stage'] == 1:
            if d == 'BUY':
                pos['extreme'] = max(pos['extreme'], h)
                ns = pos['extreme'] - (TRAIL_MULT * atr)
                pos['trail'] = max(pos['trail'], ns)
                if l <= pos['trail']:
                    ex = pos['trail']
                    actual_rem = ex - sd
                    second_half = spec.pnl(d, pos['entry'], actual_rem, pos['units'] / 2)
                    total_r = (pos['first_half'] + second_half) / pos['risk'] if pos['risk'] > 0 else 0
                    trades.append(total_r); eq += second_half; pos = None; continue
            else:
                pos['extreme'] = min(pos['extreme'], l)
                ns = pos['extreme'] + (TRAIL_MULT * atr)
                pos['trail'] = min(pos['trail'], ns)
                if h >= pos['trail']:
                    ex = pos['trail']
                    actual_rem = ex + sd
                    second_half = spec.pnl(d, pos['entry'], actual_rem, pos['units'] / 2)
                    total_r = (pos['first_half'] + second_half) / pos['risk'] if pos['risk'] > 0 else 0
                    trades.append(total_r); eq += second_half; pos = None; continue

    for sig in entries.get(bar_idx, []):
        if pos: continue
        sa = sd if sig['d'] == 'BUY' else -sd
        adj = sig['e'] + sa; orig_r = abs(sig['e'] - sig['stop'])
        stop_a = adj - orig_r if sig['d'] == 'BUY' else adj + orig_r
        risk_amt = eq * RISK_PCT; u = max(1, int(risk_amt/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
        act_r = orig_r * u * spec.ounces_per_unit; spr = 2*sp*spec.pip_value_per_unit*u
        tp1 = adj + (orig_r) if sig['d'] == 'BUY' else adj - (orig_r)
        pos = {
            'd':sig['d'], 'entry':adj, 'stop':stop_a, 'units':u,
            'risk':act_r, 'spr':spr, 'stage':0, 'tp1':tp1,
            'bars':0, 'extreme':adj, 'first_half':0, 'trail':stop_a,
        }

rvs = np.array(trades) if trades else np.array([0])
w = sum(1 for r in rvs if r>0); l_ = sum(1 for r in rvs if r<0)
g = sum(abs(r) for r in rvs if r>0); ls = sum(abs(r) for r in rvs if r<0)

print('===== HYBRID EXIT: 50% @ 1R + BREAKEVEN + CHANDELIER 2x =====')
print(f'Trades: {len(trades)}')
if len(trades) > 0:
    print(f'WR:    {w/len(trades)*100:.1f}%')
    print(f'PF:    {g/ls:.3f}' if ls > 0 else 'N/A')
    print(f'PnL:   ${eq-10000:+.2f}')
    print(f'Final: ${eq:.2f}')
    print(f'Avg R: {np.mean(rvs):.3f}')
    print(f'TotalR:{np.sum(rvs):.2f}')
print()
print('--- VS D4 FIXED 2R ---')
print(f'  Trades: {len(trades):>6} vs 8,178')
print(f'  WR:     {w/len(trades)*100 if trades else 0:>5.1f}% vs 36.9%')
print(f'  PF:     {g/ls:.3f} vs 1.134' if ls > 0 else 'N/A')
print(f'  PnL:    ${eq-10000:>+.0f} vs +$42,131')
