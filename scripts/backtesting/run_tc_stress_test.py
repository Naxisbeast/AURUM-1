"""TC Stress Test — D4 Donchian walk-forward under varying transaction costs.

Tests: spread (1.5, 2.5, 4.0, 6.0 pips), slippage (0.5, 1.0, 2.0 pips),
       and combined worst-case. Reports Sharpe, PF, WR, MaxDD, and pos window rate.
"""
import sys, math, json
from pathlib import Path
from datetime import UTC, datetime

import numpy as np
import pandas as pd

LOCAL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LOCAL))
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research.research_edge_prototypes import build_research_features

# TC scenarios: (spread_pips, slippage_pips, label)
SCENARIOS = [
    (1.5, 0.5, "Baseline (1.5s/0.5sl)"),
    (2.5, 0.5, "Wide spread"),
    (1.5, 1.0, "High slippage"),
    (2.5, 1.0, "Wide spread + high slippage"),
    (4.0, 1.0, "Stress: 4p spread"),
    (4.0, 2.0, "Stress: 4p spread + 2p slippage"),
    (6.0, 2.0, "Max stress: 6p spread + 2p slippage"),
]

LOOKBACK = 20
RISK_PCT = 0.0025
SL_MULT = 2.0
R_MULT = 2.0
TRAIN_BARS = 33264
TEST_BARS = 11088
STEP_BARS = 11088

settings = load_settings(LOCAL / 'aurum1' / 'config' / 'settings.yaml')
spec = InstrumentSpec.from_settings(settings)

ohlcv = load_ohlcv('M15', LOCAL / 'aurum1' / 'data' / 'backtest_market_cache.sqlite3')
features = build_research_features(ohlcv)

results = []

for sp_pips, slip_pips, label in SCENARIOS:
    slip_dist = slip_pips * float(spec.pip_size)
    windows = []

    for start in range(0, len(ohlcv) - TRAIN_BARS - TEST_BARS + 1, STEP_BARS):
        train_end = start + TRAIN_BARS
        test_end = start + TRAIN_BARS + TEST_BARS
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
                    pos = {'entry_bar': bar_idx, 'd': sig['d'], 'entry': adj, 'stop': stop_adj, 'target': target, 'units': raw_units, 'risk_amt': orig_r * raw_units * spec.ounces_per_unit, 'spread': 2 * sp_pips * spec.pip_value_per_unit * raw_units}

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
        windows.append({'sharpe': round(sharpe, 4), 'pf': round(pf, 4), 'wr': round(wr, 4), 'trades': tot, 'return': round((equity - 10000) / 10000, 4), 'max_dd': round(max_dd, 4)})

    positive = sum(1 for w in windows if w['sharpe'] > 0)
    mean_sharpe = float(np.mean([w['sharpe'] for w in windows]))
    mean_pf = float(np.mean([w['pf'] for w in windows]))
    mean_wr = float(np.mean([w['wr'] for w in windows]))
    mean_dd = float(np.mean([w['max_dd'] for w in windows]))
    tot_trades = sum(w['trades'] for w in windows)

    results.append({
        'scenario': label, 'spread_pips': sp_pips, 'slippage_pips': slip_pips,
        'windows': len(windows), 'positive': positive,
        'pos_window_rate': round(positive / len(windows), 4) if windows else 0,
        'mean_sharpe': round(mean_sharpe, 4), 'mean_pf': round(mean_pf, 4),
        'mean_wr': round(mean_wr, 4), 'mean_maxdd': round(mean_dd, 4),
        'total_trades': tot_trades,
    })

    print(f'{label}: S={mean_sharpe:.4f} PF={mean_pf:.4f} WR={mean_wr:.2%} DD={mean_dd:.2%} T={tot_trades} PosWin={positive}/{len(windows)}')

# Side-by-side comparison table
print(f'\n{"="*100}')
print(f'{"TC STRESS TEST — D4 Donchian Walk-Forward":^100}')
print(f'{"="*100}')
print(f'{"Scenario":<40s} {"Sharpe":>8s} {"PF":>8s} {"WR":>8s} {"MaxDD":>8s} {"Trades":>8s} {"PosWin":>8s} {"vs Base":>8s}')
print(f'{"-"*100}')
base_sharpe = results[0]['mean_sharpe']
for r in results:
    vs_base = f"{(r['mean_sharpe']/base_sharpe - 1)*100:+.1f}%" if base_sharpe else "-"
    print(f'{r["scenario"]:<40s} {r["mean_sharpe"]:>8.4f} {r["mean_pf"]:>8.4f} {r["mean_wr"]:>8.2%} {r["mean_maxdd"]:>8.2%} {r["total_trades"]:>8d} {r["pos_window_rate"]:>7.0%} {vs_base:>8s}')
print(f'{"="*100}')

out = LOCAL / 'reports' / 'forward_shadow' / 'd4_tc_stress_test_results.json'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'scenarios': results, 'generated_at': datetime.now(UTC).isoformat()}, indent=2))
print(f'\nSaved: {out}')
