"""
Historical backtest: AI co-pilot vs fixed 2R baseline.

Both use identical entry/exit logic. The ONLY difference:
- Baseline: takes every signal with fixed 2R
- AI:     may skip some signals based on context decisions

Usage:
    python experiments/run_ai_backtest.py
    python experiments/run_ai_backtest.py --quick   (5000 bars)
    python experiments/run_ai_backtest.py --api     (Claude API)
"""
import sys, os, math, argparse
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
import numpy as np
import pandas as pd
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research.research_edge_prototypes import build_research_features
from aurum1.ai_co_pilot.context import ContextBuilder
from aurum1.ai_co_pilot.safety import SafetyLayer
from aurum1.ai_co_pilot.agent import AiAgent

parser = argparse.ArgumentParser()
parser.add_argument('--quick', action='store_true')
parser.add_argument('--api', action='store_true')
args = parser.parse_args()

ohlcv = load_ohlcv('M15', 'aurum1/data/combined_market_cache.sqlite3')
features = build_research_features(ohlcv)
settings = load_settings('aurum1/config/settings.yaml')
spec = InstrumentSpec.from_settings(settings)
RISK_PCT = 0.0025; sp = 1.5; slip = 0.5; sd = slip * spec.pip_size; atr = features['atr_14']

if args.quick:
    ohlcv = ohlcv.iloc[:5000]
    features = features.iloc[:5000]
    atr = features['atr_14']

N = len(ohlcv)
print(f'Data: {N} bars ({ohlcv.index[0]} to {ohlcv.index[-1]})')
print(f'API mode: {args.api}')

# Build signal entries (used by both baseline and AI)
ub = features['high'].rolling(10, min_periods=10).max().shift(1)
lb = features['low'].rolling(10, min_periods=10).min().shift(1)
buy_sig = features['close'] > ub
sell_sig = features['close'] < lb
valid = atr.notna()

entries = {}
for d, mask in [('BUY', buy_sig & valid), ('SELL', sell_sig & valid)]:
    for st in features.index[mask.fillna(False)]:
        bar = int(features.index.get_loc(st)); eb = bar+1
        if eb >= N: continue
        e = float(ohlcv.iloc[eb]['open'])
        a = float(features.loc[st, 'atr_14'])
        if not math.isfinite(a) or a <= 0: continue
        stop = e - 2*a if d == 'BUY' else e + 2*a
        if (d == 'BUY' and stop >= e) or (d == 'SELL' and stop <= e): continue
        entries.setdefault(eb, []).append({'d':d,'e':e,'stop':stop,'a':a, 'sig_bar': bar})

print(f'Total signals: {sum(len(v) for v in entries.values())}')
print()

# ─── BASELINE: Fixed 2R (take every signal) ───
eq_b = 10000.0; pos_b = None; trades_b = []; peak_b = 10000.0
for bar_idx in range(N):
    if pos_b:
        h,l = float(ohlcv.iloc[bar_idx]['high']), float(ohlcv.iloc[bar_idx]['low']); d = pos_b['d']
        if (d == 'BUY' and l <= pos_b['stop']) or (d == 'SELL' and h >= pos_b['stop']):
            ex = pos_b['stop']; actual = ex - sd if d == 'BUY' else ex + sd
            net = spec.pnl(d, pos_b['entry'], actual, pos_b['units']) - pos_b['spr']
            rv = net/pos_b['risk'] if pos_b['risk']>0 else 0; trades_b.append(rv); eq_b += net; pos_b = None
        elif (d == 'BUY' and h >= pos_b['tgt']) or (d == 'SELL' and l <= pos_b['tgt']):
            ex = pos_b['tgt']; actual = ex - sd if d == 'BUY' else ex + sd
            net = spec.pnl(d, pos_b['entry'], actual, pos_b['units']) - pos_b['spr']
            rv = net/pos_b['risk']; trades_b.append(rv); eq_b += net; pos_b = None
    for sig in entries.get(bar_idx, []):
        if pos_b: continue
        sa = sd if sig['d'] == 'BUY' else -sd; adj = sig['e'] + sa; orig_r = abs(sig['e'] - sig['stop'])
        stop_a = adj - orig_r if sig['d'] == 'BUY' else adj + orig_r
        tgt = adj + 2*orig_r if sig['d'] == 'BUY' else adj - 2*orig_r
        risk_amt = eq_b * RISK_PCT; u = max(1, int(risk_amt/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
        act_r = orig_r * u * spec.ounces_per_unit; spr = 2*sp*spec.pip_value_per_unit*u
        pos_b = {'d':sig['d'],'entry':adj,'stop':stop_a,'tgt':tgt,'units':u,'risk':act_r,'spr':spr}

r_b = np.array(trades_b) if trades_b else np.array([0])
b_w = sum(1 for r in r_b if r>0); b_g = sum(r for r in r_b if r>0); b_ls = abs(sum(r for r in r_b if r<0))
print('=== BASELINE: Fixed 2R (take every signal) ===')
print(f'  Trades: {len(trades_b):>5}  WR: {b_w/len(trades_b)*100 if trades_b else 0:.1f}%  PF: {b_g/b_ls:.3f}  PnL: ${eq_b-10000:>+.0f}  AvgR: {np.mean(r_b):.3f}')
print()

# ─── AI CO-PILOT (entry decisions only, same exit logic) ───
cb = ContextBuilder()
safety = SafetyLayer()
ai = AiAgent(safety=safety, api_key=os.environ.get('ANTHROPIC_API_KEY') if args.api else None)

eq_a = 10000.0; peak_a = 10000.0; pos_a = None; trades_a = []
taken = 0; skipped = 0; ai_calls = 0; errors = 0
recent_trades_for_ctx = []  # track for context builder

for bar_idx in range(N):
    feats = features.iloc[bar_idx]
    candle = ohlcv.iloc[bar_idx]
    ts = ohlcv.index[bar_idx]

    # Exit check — IDENTICAL to baseline
    if pos_a:
        h,l = float(candle['high']), float(candle['low']); d = pos_a['d']
        if (d == 'BUY' and l <= pos_a['stop']) or (d == 'SELL' and h >= pos_a['stop']):
            ex = pos_a['stop']; actual = ex - sd if d == 'BUY' else ex + sd
            net = spec.pnl(d, pos_a['entry'], actual, pos_a['units']) - pos_a['spr']
            rv = net/pos_a['risk'] if pos_a['risk']>0 else 0; trades_a.append(rv); eq_a += net; pos_a = None
            safety.record_trade_result(rv)
            cb.add_trade_result(rv)
        elif (d == 'BUY' and h >= pos_a['tgt']) or (d == 'SELL' and l <= pos_a['tgt']):
            ex = pos_a['tgt']; actual = ex - sd if d == 'BUY' else ex + sd
            net = spec.pnl(d, pos_a['entry'], actual, pos_a['units']) - pos_a['spr']
            rv = net/pos_a['risk']; trades_a.append(rv); eq_a += net; pos_a = None
            safety.record_trade_result(rv)
            cb.add_trade_result(rv)

    # Entry check — ONLY DIFFERENCE: AI decides
    for sig in entries.get(bar_idx, []):
        if pos_a: continue

        # Build context and ask AI (or fallback)
        ctx = cb.build(
            timestamp=ts, signal_direction=sig['d'], signal_bar_index=sig['sig_bar'],
            entry_price=None, atr_at_signal=sig['a'],
            current_candle=candle, current_features=feats,
            position=None, equity=eq_a, peak_equity=peak_a, daily_pnl=0,
        )
        ai_calls += 1
        try:
            decision = ai.decide_on_signal(ctx)
            if decision.get('action') == 'take':
                decision = safety.validate_new_signal(decision, ctx)
        except Exception:
            errors += 1; decision = {"action": "skip", "reason": "error"}

        if decision.get('action') == 'skip':
            skipped += 1
            continue

        taken += 1
        # Use IDENTICAL entry parameters as baseline
        sa = sd if sig['d'] == 'BUY' else -sd; adj = sig['e'] + sa; orig_r = abs(sig['e'] - sig['stop'])
        stop_a = adj - orig_r if sig['d'] == 'BUY' else adj + orig_r
        tgt = adj + 2*orig_r if sig['d'] == 'BUY' else adj - 2*orig_r
        risk_amt = eq_a * RISK_PCT; u = max(1, int(risk_amt/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
        act_r = orig_r * u * spec.ounces_per_unit; spr = 2*sp*spec.pip_value_per_unit*u
        pos_a = {'d':sig['d'],'entry':adj,'stop':stop_a,'tgt':tgt,'units':u,'risk':act_r,'spr':spr}

    # Update peak equity
    peak_a = max(peak_a, eq_a)

r_a = np.array(trades_a) if trades_a else np.array([0])
a_w = sum(1 for r in r_a if r>0); a_g = sum(r for r in r_a if r>0); a_ls = abs(sum(r for r in r_a if r<0))
print('=== AI CO-PILOT (entry decisions only) ===')
print(f'  Trades: {len(trades_a):>5}  WR: {a_w/len(trades_a)*100 if trades_a else 0:.1f}%  PF: {a_g/a_ls:.3f}  PnL: ${eq_a-10000:>+.0f}  AvgR: {np.mean(r_a):.3f}')
print(f'  Taken: {taken}  Skipped: {skipped}  AI calls: {ai_calls}  Errors: {errors}')
print()

# ─── COMPARISON ───
print('=== COMPARISON ===')
print(f'{"Metric":<25} {"Fixed 2R":>12} {"AI Co-Pilot":>12} {"Delta":>10}')
print('-' * 62)
for name, bv, av in [
    ('Trades', len(trades_b), len(trades_a)),
    ('Win Rate', b_w/len(trades_b)*100 if trades_b else 0, a_w/len(trades_a)*100 if trades_a else 0),
    ('Profit Factor', b_g/b_ls if b_ls > 0 else 0, a_g/a_ls if a_ls > 0 else 0),
    ('Net PnL', eq_b-10000, eq_a-10000),
    ('Avg R', float(np.mean(r_b)) if len(r_b) > 0 else 0, float(np.mean(r_a)) if len(r_a) > 0 else 0),
]:
    delta = ((av - bv) / abs(bv) * 100) if abs(bv) > 0.001 else 0
    print(f'  {name:<23} {bv:>10.3f}  {av:>10.3f}  {delta:>+9.1f}%')

print()
print('=== VERDICT ===')
pf_impr = (a_g/a_ls > b_g/b_ls) if a_ls > 0 and b_ls > 0 else False
wr_impr = (a_w/len(trades_a) > b_w/len(trades_b)) if trades_a and trades_b else False
pnl_impr = (eq_a > eq_b)
passed = sum([pf_impr, wr_impr, pnl_impr])
print(f'  PF improved:   {"✅" if pf_impr else "❌"}')
print(f'  WR improved:   {"✅" if wr_impr else "❌"}')
print(f'  PnL improved:  {"✅" if pnl_impr else "❌"}')
print(f'  Passed: {passed}/3 criteria')
print(f'  {"✅ AI Co-Pilot works" if passed >= 2 else "❌ AI Co-Pilot needs work"}')
