"""Test volatility compression filter on entry."""
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

# === VOLATILITY COMPRESSION FILTER ===
# Only take breakouts when ATR(14) is below ATR SMA(20) (compressed state)
atr = features['atr_14']
atr_sma = atr.rolling(20, min_periods=20).mean()
compressed = atr.shift(1) < atr_sma.shift(1)  # Checked on bar BEFORE signal

buy_signal = features['close'] > features['high'].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
sell_signal = features['close'] < features['low'].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
valid = features['atr_14'].notna()

# Apply compression filter
buy_m = buy_signal & valid & compressed
sell_m = sell_signal & valid & compressed

print(f'BUY signals:  {buy_signal.sum()} -> {buy_m.sum()} (filtered)')
print(f'SELL signals: {sell_signal.sum()} -> {sell_m.sum()} (filtered)')
print(f'Filter blocks: {(buy_signal & valid).sum() - buy_m.sum() + (sell_signal & valid).sum() - sell_m.sum()}')

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
        o,h,l = float(row['open']),float(row['high']),float(row['low']); d = pos['d']
        if (d == 'BUY' and l <= pos['stop']) or (d == 'SELL' and h >= pos['stop']):
            ex = pos['stop']
            actual = ex - sd if d == 'BUY' else ex + sd
            net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
            rv = net/pos['risk']; trades.append(rv); eq += net; pos = None
        elif (d == 'BUY' and h >= pos['tgt']) or (d == 'SELL' and l <= pos['tgt']):
            ex = pos['tgt']
            actual = ex - sd if d == 'BUY' else ex + sd
            net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
            rv = net/pos['risk']; trades.append(rv); eq += net; pos = None
    for sig in entries.get(bar_idx, []):
        if pos: continue
        sa = sd if sig['d'] == 'BUY' else -sd
        adj = sig['e'] + sa; orig_r = abs(sig['e'] - sig['stop'])
        stop_a = adj - orig_r if sig['d'] == 'BUY' else adj + orig_r
        tgt = adj + 2*orig_r if sig['d'] == 'BUY' else adj - 2*orig_r
        risk_amt = eq * RISK_PCT; u = max(1, int(risk_amt/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
        act_r = orig_r * u * spec.ounces_per_unit; spr = 2*sp*spec.pip_value_per_unit*u
        pos = {'eb':bar_idx,'d':sig['d'],'entry':adj,'stop':stop_a,'tgt':tgt,'units':u,'risk':act_r,'spr':spr}

rvs = np.array(trades) if trades else np.array([0])
w = sum(1 for r in rvs if r>0); l_ = sum(1 for r in rvs if r<0)
g = sum(abs(r) for r in rvs if r>0); ls = sum(abs(r) for r in rvs if r<0)

print()
print('===== VOLATILITY COMPRESSION FILTER + FIXED 2R EXIT =====')
print(f'Trades: {len(trades)}')
if len(trades) > 0:
    print(f'WR:    {w/len(trades)*100:.1f}%')
    print(f'PF:    {g/ls:.3f}' if ls > 0 else 'N/A')
    print(f'PnL:   ${eq-10000:+.2f}')
    print(f'Final: ${eq:.2f}')
    print(f'Avg R: {np.mean(rvs):.3f}')
print()
print('--- VS D4 FIXED 2R (NO FILTER) ---')
print(f'  Trades: {len(trades):>6} vs 8,178 (-{(8178-len(trades))/8178*100:.0f}%)')
print(f'  WR:     {w/len(trades)*100 if trades else 0:>5.1f}% vs 36.9%')
print(f'  PF:     {g/ls:.3f} vs 1.134' if ls > 0 else 'N/A')
print(f'  PnL:    ${eq-10000:>+.0f} vs +$42,131')
