"""D4 safety check: run on last 2 months (forward shadow period) to verify performance."""
import sys, math
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research.research_edge_prototypes import build_research_features

settings = load_settings(Path('aurum1/config/settings.yaml'))
ohlcv = load_ohlcv('M15', Path('aurum1/data/forward_shadow_market_cache.sqlite3'))
print(f'Shadow cache: {len(ohlcv)} M15 candles ({ohlcv.index[0].date()} to {ohlcv.index[-1].date()})')

cutoff = pd.Timestamp('2026-05-01', tz='UTC')
ohlcv = ohlcv[ohlcv.index >= cutoff].copy()
print(f'Test period: {ohlcv.index[0].date()} to {ohlcv.index[-1].date()} ({len(ohlcv)} candles)')
features = build_research_features(ohlcv)
spec = InstrumentSpec.from_settings(settings)
sp = 1.5; slip = 0.5; sd = slip * spec.pip_size
LOOKBACK = 20; RISK_PCT = 0.0025

buy_m = features['close'] > features['high'].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
sell_m = features['close'] < features['low'].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
valid = features['atr_14'].notna(); buy_m = buy_m & valid; sell_m = sell_m & valid
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

for label, en_sell in [('Raw BUY-only 2R (matches live shadow)', False), ('D4 BUY+SELL 2R', True)]:
    eq = 10000.0; pos = None; trades = []
    for bar_idx, (ts, row) in enumerate(ohlcv.iterrows()):
        if pos and bar_idx > pos['eb']:
            o,h,l = float(row['open']),float(row['high']),float(row['low'])
            d = pos['d']; ex=None; rn=None
            if d == 'BUY':
                if o <= pos['stop']: ex,rn = o,'stop_loss_gap'
                elif l <= pos['stop']: ex,rn = pos['stop'],'stop_loss'
                elif h >= pos['tgt']: ex,rn = pos['tgt'],'take_profit'
            else:
                if o >= pos['stop']: ex,rn = o,'stop_loss_gap'
                elif h >= pos['stop']: ex,rn = pos['stop'],'stop_loss'
                elif l <= pos['tgt']: ex,rn = pos['tgt'],'take_profit'
            if ex and rn:
                actual = ex - sd if d == 'BUY' else ex + sd
                gross = spec.pnl(d, pos['entry'], actual, pos['units'])
                net = gross - pos['spr']; rv = net/pos['risk'] if pos['risk']>0 else 0
                trades.append({'r':rv,'p':net,'x':rn,'d':d})
                eq += net; pos = None
        for sig in entries.get(bar_idx, []):
            if not en_sell and sig['d'] == 'SELL': continue
            if pos: continue
            sa = sd if sig['d'] == 'BUY' else -sd
            adj = sig['e'] + sa; orig_r = abs(sig['e'] - sig['stop'])
            stop_a = adj - orig_r if sig['d'] == 'BUY' else adj + orig_r
            tgt = adj + 2*orig_r if sig['d'] == 'BUY' else adj - 2*orig_r
            risk = eq * RISK_PCT; u = max(1, int(risk/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
            act_r = orig_r * u * spec.ounces_per_unit; spread = 2*sp*spec.pip_value_per_unit*u
            pos = {'eb':bar_idx,'d':sig['d'],'entry':adj,'stop':stop_a,'tgt':tgt,'units':u,'risk':act_r,'spr':spread}
            break
    if pos and len(ohlcv)>0:
        last = float(ohlcv.iloc[-1]['close']); gross = spec.pnl(pos['d'],pos['entry'],last,pos['units'])
        net = gross-pos['spr']; rv = net/pos['risk'] if pos['risk']>0 else 0
        trades.append({'r':rv,'p':net,'x':'end_of_data','d':pos['d']})

    rvs = [t['r'] for t in trades]; w=sum(1 for r in rvs if r>0); l=sum(1 for r in rvs if r<0)
    g=sum(abs(r) for r in rvs if r>0); ls=sum(abs(r) for r in rvs if r<0)
    pf = g/ls if ls>0 else 0
    buy_t = [t for t in trades if t['d']=='BUY']; sell_t = [t for t in trades if t['d']=='SELL']
    print(f'\n{label}')
    print(f'  Trades: {len(trades)}  WR: {w/len(trades)*100:.1f}%  PF: {pf:.4f}  R: {sum(rvs):+.2f}')
    print(f'  PnL: ${sum(t["p"] for t in trades):+.2f}  Final equity: ${eq:.2f}')
    if buy_t: print(f'  BUY: {len(buy_t)} @ {sum(1 for t in buy_t if t["r"]>0)/len(buy_t)*100:.1f}%')
    if sell_t: print(f'  SELL: {len(sell_t)} @ {sum(1 for t in sell_t if t["r"]>0)/len(sell_t)*100:.1f}%')
    print(f'  Exits: {dict(Counter(t["x"] for t in trades))}')

print(f'\n{"="*60}')
print('Live forward shadow comparison:')
print(f'  Raw (actual): 34 trades, 23.5% WR, 0.61 PF, -$254.01')
