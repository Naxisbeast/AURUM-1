"""D4 walk-forward (v2 cost model): Donchian + BUY+SELL + 2R exit.

Uses session-aware spread and folded-normal (asymmetric) slippage.
Run from repo root: python scripts/backtesting/run_d4_walk_forward_v2.py --lookback 20
"""
import sys, math, json, argparse, subprocess
from collections import Counter
from pathlib import Path
from datetime import UTC, datetime

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research.research_edge_prototypes import build_research_features

parser = argparse.ArgumentParser()
parser.add_argument('--lookback', type=int, default=20)
parser.add_argument('--output-name', type=str, default=None)
parser.add_argument('--train-bars', type=int, default=33264)
parser.add_argument('--test-bars', type=int, default=11088)
parser.add_argument('--step-bars', type=int, default=11088)
parser.add_argument('--seed', type=int, default=42, help='Random seed for slippage RNG')
args = parser.parse_args()

LOOKBACK = args.lookback
output_name = args.output_name or f'd4_walk_forward_L{LOOKBACK}_v2.json'
RNG = np.random.RandomState(args.seed)

settings = load_settings(ROOT / 'aurum1' / 'config' / 'settings.yaml')
spec = InstrumentSpec.from_settings(settings)

ohlcv = load_ohlcv('M15', ROOT / 'aurum1' / 'data' / 'backtest_market_cache.sqlite3')
print(f'Data: {len(ohlcv)} M15 candles ({ohlcv.index[0].date()} to {ohlcv.index[-1].date()})')
print(f'Sources: {ohlcv["source"].value_counts().to_dict()}')

RISK_PCT = 0.0025
SL_MULT = 2.0
R_MULT = 2.0
BASE_SPREAD = 1.5  # base spread pips (adjusted by session)
SLIPPAGE_STD_PIPS = 0.5
PIP_SIZE = float(InstrumentSpec.from_settings(load_settings(ROOT / 'aurum1' / 'config' / 'settings.yaml')).pip_size)


def session_spread(hour: int) -> float:
    """Session-aware spread multiplier. XAU/USD spreads vary by session."""
    if 13 <= hour < 16:
        return BASE_SPREAD * 1.0   # London/NY overlap
    elif 8 <= hour < 13:
        return BASE_SPREAD * 1.3   # London only
    elif 13 <= hour < 22:
        return BASE_SPREAD * 1.3   # NY only
    else:
        return BASE_SPREAD * 2.0   # Asian session


def folded_slippage() -> float:
    """Asymmetric (always adverse) slippage for market orders.

    Uses half-normal distribution: always positive, mode near zero, tail of larger slips.
    """
    std = SLIPPAGE_STD_PIPS * PIP_SIZE
    if std <= 0:
        return 0.0
    return abs(float(RNG.normal(0, std)))


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
    test_times = test_ohlcv.index

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
        bar_time = test_times[bar_idx]

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
                exit_slip = folded_slippage()
                actual_exit = ex_price - exit_slip if pos['d'] == 'BUY' else ex_price + exit_slip
                gross = spec.pnl(pos['d'], pos['entry'], actual_exit, pos['units'])
                net = gross - pos['spread']
                rv = net / pos['risk_amt'] if pos['risk_amt'] > 0 else 0
                trades.append({'r': rv, 'pnl': net, 'reason': reason})
                equity += net; pos = None

        if pos is None and bar_idx in entries:
            for sig in entries[bar_idx]:
                if pos: break
                entry_slip = folded_slippage()
                adj = sig['entry'] + entry_slip if sig['d'] == 'BUY' else sig['entry'] - entry_slip
                orig_r = sig['risk_dist']
                stop_adj = adj - orig_r if sig['d'] == 'BUY' else adj + orig_r
                target = adj + R_MULT * orig_r if sig['d'] == 'BUY' else adj - R_MULT * orig_r
                risk_dollars = equity * RISK_PCT
                raw_units = max(1, int(risk_dollars / (orig_r * spec.ounces_per_unit))) if orig_r > 0 else 1
                # Session-aware spread at entry time
                entry_hour = bar_time.hour if hasattr(bar_time, 'hour') else pd.Timestamp(bar_time).hour
                sp = session_spread(entry_hour)
                pos = {'entry_bar': bar_idx, 'd': sig['d'], 'entry': adj, 'stop': stop_adj, 'target': target,
                       'units': raw_units, 'risk_amt': orig_r * raw_units * spec.ounces_per_unit,
                       'spread': 2 * sp * spec.pip_value_per_unit * raw_units}

        eq_curve.append(equity)

    if pos and len(test_ohlcv):
        last = float(test_ohlcv.iloc[-1]['close'])
        exit_slip = folded_slippage()
        actual_exit = last - exit_slip if pos['d'] == 'BUY' else last + exit_slip
        gross = spec.pnl(pos['d'], pos['entry'], actual_exit, pos['units'])
        net = gross - pos['spread']
        rv = net / pos['risk_amt'] if pos['risk_amt'] > 0 else 0
        trades.append({'r': rv, 'pnl': net, 'reason': 'end_of_data'})
        equity += net

    r_vals = [t['r'] for t in trades]
    tot = len(trades)
    wr = sum(1 for r in r_vals if r > 0) / tot if tot else 0
    gp = sum(abs(r) for r in r_vals if r > 0) if r_vals else 0
    gl = sum(abs(r) for r in r_vals if r < 0) if r_vals else 0
    pf = gp / gl if gl > 0 else 0

    eq_s = pd.Series(eq_curve)
    di = pd.date_range(test_ohlcv.index[0], test_ohlcv.index[-1], periods=len(eq_s), tz='UTC')
    dr = pd.Series(eq_s.values, index=di).resample('1D').last().dropna().pct_change().dropna()
    sharpe = (dr.mean() / dr.std() * math.sqrt(252)) if len(dr) > 2 and dr.std() > 0 else 0
    peak = np.maximum.accumulate(eq_curve)
    max_dd = max([(p - e) / p for e, p in zip(eq_curve, peak) if p > 0] or [0])

    windows.append({'window': len(windows) + 1, 'sharpe': round(sharpe, 4), 'pf': round(pf, 4),
                    'wr': round(wr, 4), 'trades': tot, 'return': round((equity - 10000) / 10000, 4),
                    'max_dd': round(max_dd, 4)})
    start += step_bars

try:
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
                            capture_output=True, text=True, timeout=5, cwd=ROOT).stdout.strip()
except Exception:
    commit = 'unknown'

positive = sum(1 for w in windows if w['sharpe'] > 0)
neg = len(windows) - positive
all_sharpes = [w['sharpe'] for w in windows]
all_pfs = [w['pf'] for w in windows]

print(f'\n=== D4 DONCHIAN WALK-FORWARD L{LOOKBACK} V2 ({len(windows)} windows) ===')
print(f'Commit: {commit}')
print(f'Cost model: session-aware spread (1.0x-2.0x), folded-normal slip')
print(f'{positive} positive, {neg} negative')
print(f'Mean Sharpe: {np.mean(all_sharpes):.4f}  (v1: 1.27)')
print(f'Mean PF: {np.mean(all_pfs):.4f}  (v1: 1.14)')
print(f'Mean WR: {np.mean([w["wr"] for w in windows]):.4%}')
print(f'Mean MaxDD: {np.mean([w["max_dd"] for w in windows]):.4%}')
print(f'Pos window rate: {positive/len(windows):.4%}  (v1: 88.9%)')
print('\nPer-window:')
for w in windows:
    m = '+' if w['sharpe'] > 0 else ' '
    print(f'  [{m}] W{w["window"]:02d}: S={w["sharpe"]:.4f} PF={w["pf"]:.4f} T={w["trades"]} R={w["return"]:.3f} DD={w["max_dd"]:.3f}')

out = ROOT / 'reports' / 'forward_shadow' / output_name
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({
    'aurum1_commit': commit,
    'generated_at': datetime.now(UTC).isoformat(),
    'cost_model_version': 'v2_session_aware_asymmetric',
    'lookback': LOOKBACK,
    'windows': windows,
    'summary': {
        'n': len(windows), 'positive': positive, 'negative': neg,
        'mean_sharpe': float(np.mean(all_sharpes)),
        'mean_pf': float(np.mean(all_pfs)),
        'pos_window_rate': positive/len(windows) if windows else 0,
        'mean_max_dd': float(np.mean([w['max_dd'] for w in windows])),
    },
}, indent=2, default=str))
print(f'\nSaved: {out}')

# --- Auto-log to the trial ledger for DSR (Deflated Sharpe Ratio) ---
# The ledger is the deflation pool for the 100-trade gate. The DSR needs
# UNAANNUALIZED per-window Sharpe, but the loop above stores the ANNUALIZED
# value (multiplied by sqrt(252)). All windows are equal length (test_bars),
# so unannualized = annualized / sqrt(252) is an exact inverse.
from scipy.stats import kurtosis, skew as _skew  # noqa: E402

unann_sharpes = np.array([w['sharpe'] / math.sqrt(252) for w in windows])
if unann_sharpes.size >= 3:
    from aurum1.research.trial_ledger import TrialRecord, log_trial

    variant_id = f'D4_walkforward_L{LOOKBACK}_v2'
    rec = TrialRecord(
        variant_id=variant_id,
        parent_family='donchian_breakout',
        n_obs=len(windows),
        sharpe=float(unann_sharpes.mean()),
        skew=float(_skew(unann_sharpes)),
        kurtosis=float(kurtosis(unann_sharpes, fisher=False)),
        return_series_path=f'reports/forward_shadow/{output_name}',
        notes=f'Donchian LOOKBACK={LOOKBACK}, 2R exit, BUY+SELL, no filters, '
              f'v2 session-aware cost model. {len(windows)} non-overlapping windows. '
              'Unannualized per-window Sharpe (annualized/sqrt(252)). Auto-logged by runner.',
    )
    log_trial(rec)
    print(f'Trial ledger: logged {variant_id} (n_obs={rec.n_obs}, '
          f'sharpe={rec.sharpe:.4f})')
else:
    print(f'Trial ledger: skipping {LOOKBACK}-bar run ({len(windows)} windows < 3)')
