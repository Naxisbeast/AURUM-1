"""Parameterized D4 walk-forward: pure Donchian + BUY+SELL + 2R exit, configurable lookback."""
import sys, math, json, argparse
from collections import Counter
from pathlib import Path
from datetime import UTC, datetime

import numpy as np
import pandas as pd

ROOT = Path('/opt/aurum1')
sys.path.insert(0, str(ROOT))
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research.research_edge_prototypes import build_research_features

parser = argparse.ArgumentParser()
parser.add_argument('--lookback', type=int, default=20, help='Donchian channel lookback in bars')
parser.add_argument('--output-name', type=str, default=None, help='Output filename (default: d4_walk_forward_L{lookback}_results.json)')
parser.add_argument('--train-bars', type=int, default=33264, help='Training bars per window')
parser.add_argument('--test-bars', type=int, default=11088, help='Test bars per window')
parser.add_argument('--step-bars', type=int, default=11088, help='Step size between windows')
args = parser.parse_args()

LOOKBACK = args.lookback
output_name = args.output_name or f'd4_walk_forward_L{LOOKBACK}_results.json'

settings = load_settings(ROOT / 'aurum1' / 'config' / 'settings.yaml')
spec = InstrumentSpec.from_settings(settings)

ohlcv = load_ohlcv('M15', ROOT / 'aurum1' / 'data' / 'backtest_market_cache.sqlite3')
print(f'Data: {len(ohlcv)} M15 candles ({ohlcv.index[0].date()} to {ohlcv.index[-1].date()})')

RISK_PCT = 0.0025
SL_MULT = 2.0
R_MULT = 2.0
sp = 1.5
slip_pips = 0.5
slip_dist = slip_pips * float(InstrumentSpec.from_settings(load_settings(ROOT / 'aurum1' / 'config' / 'settings.yaml')).pip_size)

features = build_research_features(ohlcv)

train_bars = args.train_bars
test_bars = args.test_bars
step_bars = args.step_bars

windows = []
start = 0
while start + train_bars + test_bars <= len(ohlcv):
    train_end = start + train_bars
    test_end = start + train_bars + test_bars
    test_ohlcv = ohlcv.iloc[train_end:test_end]
    test_feat = features.iloc[train_end:test_end]

    buy_mask = test_feat['close'] > test_feat['high'].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
    sell_mask = test_feat['close'] < test_feat['low'].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
    valid = test_feat['atr_14'].notna()
    buy_mask = buy_mask & valid; sell_mask = sell_mask & valid

    entries = {}
    for direction, mask in [('BUY', buy_mask), ('SELL', sell_mask)]:
        for sig_ts in test_feat.index[mask.fillna(False)]:
            sig_bar = test_ohlcv.index.get_loc(sig_ts)
            entry_bar = sig_bar + 1
            if entry_bar >= len(test_ohlcv): continue
            ep = float(test_ohlcv.iloc[entry_bar]['open'])
            atr_val = float(test_feat.loc[sig_ts, 'atr_14'])
            if not math.isfinite(atr_val) or atr_val <= 0: continue
            stop = ep - SL_MULT * atr_val if direction == 'BUY' else ep + SL_MULT * atr_val
            if (direction == 'BUY' and stop >= ep) or (direction == 'SELL' and stop <= ep): continue
            entries.setdefault(entry_bar, []).append({'d': direction, 'entry': ep, 'stop': stop, 'risk_dist': abs(ep - stop)})

    equity = 10000.0; pos = None; trades = []; eq_curve = [equity]
    for bar_idx in range(len(test_ohlcv)):
        row = test_ohlcv.iloc[bar_idx]
        o, h, l = float(row['open']), float(row['high']), float(row['low'])

        if pos is not None and bar_idx > pos['entry_bar']:
            ex_price, reason = None, None
            if pos['d'] == 'BUY':
                if o <= pos['stop']: ex_price, reason = o, 'stop_loss_gap'
                elif l <= pos['stop']: ex_price, reason = pos['stop'], 'stop_loss'
                elif h >= pos['target']: ex_price, reason = pos['target'], 'take_profit'
            else:
                if o >= pos['stop']: ex_price, reason = o, 'stop_loss_gap'
                elif h >= pos['stop']: ex_price, reason = pos['stop'], 'stop_loss'
                elif l <= pos['target']: ex_price, reason = pos['target'], 'take_profit'
            if ex_price and reason:
                actual_exit = ex_price - slip_dist if pos['d'] == 'BUY' else ex_price + slip_dist
                gross = spec.pnl(pos['d'], pos['entry'], actual_exit, pos['units'])
                net = gross - pos['spread']
                rv = net / pos['risk_amt'] if pos['risk_amt'] > 0 else 0
                trades.append({'r': rv, 'pnl': net, 'reason': reason})
                equity += net; pos = None

        if pos is None and bar_idx in entries:
            for sig in entries[bar_idx]:
                if pos: break
                adj = sig['entry'] + slip_dist if sig['d'] == 'BUY' else sig['entry'] - slip_dist
                orig_r = sig['risk_dist']
                stop_adj = adj - orig_r if sig['d'] == 'BUY' else adj + orig_r
                target = adj + R_MULT * orig_r if sig['d'] == 'BUY' else adj - R_MULT * orig_r
                risk_dollars = equity * RISK_PCT
                raw_units = max(1, int(risk_dollars / (orig_r * spec.ounces_per_unit))) if orig_r > 0 else 1
                pos = {'entry_bar': bar_idx, 'd': sig['d'], 'entry': adj, 'stop': stop_adj, 'target': target, 'units': raw_units, 'risk_amt': orig_r * raw_units * spec.ounces_per_unit, 'spread': 2 * sp * spec.pip_value_per_unit * raw_units}

        eq_curve.append(equity)

    if pos and len(test_ohlcv):
        last = float(test_ohlcv.iloc[-1]['close'])
        gross = spec.pnl(pos['d'], pos['entry'], last, pos['units'])
        net = gross - pos['spread']
        rv = net / pos['risk_amt'] if pos['risk_amt'] > 0 else 0
        trades.append({'r': rv, 'pnl': net, 'reason': 'end_of_data'})
        equity += net

    r_vals = [t['r'] for t in trades]
    tot = len(trades)
    wr = sum(1 for r in r_vals if r > 0) / tot if tot else 0
    gp = sum(abs(r) for r in r_vals if r > 0)
    gl = sum(abs(r) for r in r_vals if r < 0)
    pf = gp / gl if gl > 0 else 0

    eq_s = pd.Series(eq_curve)
    di = pd.date_range(test_ohlcv.index[0], test_ohlcv.index[-1], periods=len(eq_s), tz='UTC')
    dr = pd.Series(eq_s.values, index=di).resample('1D').last().dropna().pct_change().dropna()
    sharpe = (dr.mean() / dr.std() * math.sqrt(252)) if len(dr) > 2 and dr.std() > 0 else 0
    peak = np.maximum.accumulate(eq_curve)
    max_dd = max([(p - e) / p for e, p in zip(eq_curve, peak) if p > 0] or [0])

    windows.append({'window': len(windows) + 1, 'sharpe': round(sharpe, 4), 'pf': round(pf, 4), 'wr': round(wr, 4), 'trades': tot, 'return': round((equity - 10000) / 10000, 4), 'max_dd': round(max_dd, 4)})
    start += step_bars

positive = sum(1 for w in windows if w['sharpe'] > 0)
neg = len(windows) - positive
all_sharpes = [w['sharpe'] for w in windows]
all_pfs = [w['pf'] for w in windows]

print(f'\n=== D4 DONCHIAN WALK-FORWARD L{LOOKBACK} ({len(windows)} windows) ===')
print(f'{positive} positive, {neg} negative')
print(f'Mean Sharpe: {np.mean(all_sharpes):.4f}')
print(f'Mean PF: {np.mean(all_pfs):.4f}')
print(f'Mean WR: {np.mean([w["wr"] for w in windows]):.4%}')
print(f'Mean MaxDD: {np.mean([w["max_dd"] for w in windows]):.4%}')
print(f'Pos window rate: {positive/len(windows):.4%}')
print('\nPer-window:')
for w in windows:
    m = '+' if w['sharpe'] > 0 else ' '
    print(f'  [{m}] W{w["window"]:02d}: S={w["sharpe"]:.4f} PF={w["pf"]:.4f} T={w["trades"]} R={w["return"]:.3f} DD={w["max_dd"]:.3f}')

out = ROOT / 'reports' / 'forward_shadow' / output_name
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'windows': windows, 'summary': {'n': len(windows), 'positive': positive, 'negative': neg, 'mean_sharpe': float(np.mean(all_sharpes)), 'mean_pf': float(np.mean(all_pfs)), 'pos_window_rate': positive/len(windows) if windows else 0}, 'generated_at': datetime.now(UTC).isoformat()}, indent=2, default=str))
print(f'\nSaved: {out}')
