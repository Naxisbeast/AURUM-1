"""Run batch of experiments comparing different parameters."""
import sys; sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
import math, numpy as np, pandas as pd
from aurum1.data.ingestion import load_ohlcv, load_settings
from scripts.research.research_edge_prototypes import build_research_features
from aurum1.instruments import InstrumentSpec

ohlcv = load_ohlcv('M15', 'aurum1/data/combined_market_cache.sqlite3')
features = build_research_features(ohlcv)
settings = load_settings('aurum1/config/settings.yaml')
spec = InstrumentSpec.from_settings(settings)
LOOKBACK = 20; RISK_PCT = 0.0025; sp = 1.5; slip = 0.5; sd = slip * spec.pip_size

buy_signal = features['close'] > features['high'].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
sell_signal = features['close'] < features['low'].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
valid = features['atr_14'].notna()
atr = features['atr_14']

def run_backtest(entry_filter, exit_mode, exit_param=None):
    """Run a backtest with given entry filter and exit mode."""
    entries = {}
    for d, mask in [('BUY', buy_signal & valid & entry_filter), ('SELL', sell_signal & valid & entry_filter)]:
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
            cv = exit_mode
            if cv == 'FIXED_2R':
                if (d == 'BUY' and l <= pos['stop']) or (d == 'SELL' and h >= pos['stop']):
                    ex = pos['stop']; actual = ex - sd if d == 'BUY' else ex + sd
                    net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
                    rv = net/pos['risk'] if pos['risk']>0 else 0
                    trades.append(rv); eq += net; pos = None
                elif (d == 'BUY' and h >= pos['tgt']) or (d == 'SELL' and l <= pos['tgt']):
                    ex = pos['tgt']; actual = ex - sd if d == 'BUY' else ex + sd
                    net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
                    rv = net/pos['risk']; trades.append(rv); eq += net; pos = None
            elif cv == 'CHANDELIER':
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
                                if low<=s2.stp:s2.trig=True;s2.ex=s2.stp;return True
                            else:
                                s2.ext=min(s2.ext,low);ns=s2.ext+(s2.m*atr2);s2.stp=min(s2.stp,ns)
                                if high>=s2.stp:s2.trig=True;s2.ex=s2.stp;return True
                            return False
                    pos['cx'] = CX()
                atr_v = float(atr.iloc[bar_idx]) or 0.01
                if pos['cx'].upd(h, l, atr_v):
                    actual = pos['cx'].ex - sd if d == 'BUY' else pos['cx'].ex + sd
                    net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
                    rv = net/pos['risk'] if pos['risk']>0 else 0
                    trades.append(rv); eq += net; pos = None
        for sig in entries.get(bar_idx, []):
            if pos: continue
            sa = sd if sig['d'] == 'BUY' else -sd; adj = sig['e'] + sa; orig_r = abs(sig['e'] - sig['stop'])
            stop_a = adj - orig_r if sig['d'] == 'BUY' else adj + orig_r
            tgt = adj + 2*orig_r if sig['d'] == 'BUY' else adj - 2*orig_r
            risk_amt = eq * RISK_PCT; u = max(1, int(risk_amt/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
            act_r = orig_r * u * spec.ounces_per_unit; spr = 2*sp*spec.pip_value_per_unit*u
            pos = {'d':sig['d'],'entry':adj,'stop':stop_a,'tgt':tgt,'units':u,'risk':act_r,'spr':spr,'ae':sig['a']}
    rvs = np.array(trades) if trades else np.array([0])
    w = sum(1 for r in rvs if r>0); g = sum(abs(r) for r in rvs if r>0); ls = sum(abs(r) for r in rvs if r<0)
    return {'trades': len(trades), 'wr': w/len(trades)*100 if trades else 0,
            'pf': g/ls if ls > 0 else 0, 'pnl': eq-10000, 'avg_r': float(np.mean(rvs)) if len(rvs)>0 else 0}

# ── BASELINE ──
baseline = run_backtest(pd.Series(True, index=buy_signal.index), 'FIXED_2R')
print('========== BASELINE: D4 Fixed 2R (no filters) ==========')
print(f'  Trades: {baseline["trades"]:>6}  WR: {baseline["wr"]:>5.1f}%  PF: {baseline["pf"]:.3f}  PnL: ${baseline["pnl"]:>+.0f}  AvgR: {baseline["avg_r"]:.3f}')
print()

# ── TEST 1: CHANDELIER MULTIPLIERS ──
print('========== CHANDELIER EXIT (no entry filter) ==========')
for m in [4.0, 4.5, 5.0, 5.5, 6.0]:
    r = run_backtest(pd.Series(True, index=buy_signal.index), 'CHANDELIER', exit_param=m)
    print(f'  {m:.1f}x ATR   Trades:{r["trades"]:>6}  WR:{r["wr"]:>5.1f}%  PF:{r["pf"]:.3f}  PnL:${r["pnl"]:>+.0f}  AvgR:{r["avg_r"]:.3f}')
print()

# ── TEST 2: ATR PERCENTILE FILTERS ──
print('========== ATR PERCENTILE FILTERS (100-bar) + Fixed 2R ==========')
atr_pct = atr.rolling(100, min_periods=100).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
for name, lo, hi in [('ATR pct 0-30', 0.0, 0.3), ('ATR pct 30-50', 0.3, 0.5),
                      ('ATR pct 50-70', 0.5, 0.7), ('ATR pct 70-100', 0.7, 1.0)]:
    filt = (atr_pct.shift(1) >= lo) & (atr_pct.shift(1) < hi)
    r = run_backtest(filt, 'FIXED_2R')
    print(f'  {name:<20} Trades:{r["trades"]:>6}  WR:{r["wr"]:>5.1f}%  PF:{r["pf"]:.3f}  PnL:${r["pnl"]:>+.0f}')
print()

# ── TEST 3: ADX FILTERS ──
print('========== ADX FILTERS + Fixed 2R ==========')
adx = features.get('adx_14', atr * 0)  # Fallback if not available
for name, cond in [('ADX > 20', adx.shift(1) > 20), ('ADX > 25', adx.shift(1) > 25),
                   ('ADX > 30', adx.shift(1) > 30), ('ADX < 20', adx.shift(1) < 20)]:
    r = run_backtest(cond, 'FIXED_2R')
    print(f'  {name:<20} Trades:{r["trades"]:>6}  WR:{r["wr"]:>5.1f}%  PF:{r["pf"]:.3f}  PnL:${r["pnl"]:>+.0f}')
print()

# ── TEST 4: COMBINATIONS ──
print('========== BEST COMBINATIONS ==========')
# Chandelier 5.5x + volatility compression (ATR < SMA20)
atr_sma = atr.rolling(20, min_periods=20).mean()
compressed = atr.shift(1) < atr_sma.shift(1)
r = run_backtest(compressed, 'CHANDELIER', exit_param=5.5)
print(f'  Compressed + Chandelier 5.5x  Trades:{r["trades"]:>6}  WR:{r["wr"]:>5.1f}%  PF:{r["pf"]:.3f}  PnL:${r["pnl"]:>+.0f}  AvgR:{r["avg_r"]:.3f}')

# Chandelier 5.5x + ATR pct 0-30
filt_low = atr_pct.shift(1) < 0.3
r = run_backtest(filt_low, 'CHANDELIER', exit_param=5.5)
print(f'  ATR pct<30 + Chandelier 5.5x  Trades:{r["trades"]:>6}  WR:{r["wr"]:>5.1f}%  PF:{r["pf"]:.3f}  PnL:${r["pnl"]:>+.0f}  AvgR:{r["avg_r"]:.3f}')

# Chandelier 6.0x + ATR pct 0-30
r = run_backtest(filt_low, 'CHANDELIER', exit_param=6.0)
print(f'  ATR pct<30 + Chandelier 6.0x  Trades:{r["trades"]:>6}  WR:{r["wr"]:>5.1f}%  PF:{r["pf"]:.3f}  PnL:${r["pnl"]:>+.0f}  AvgR:{r["avg_r"]:.3f}')
