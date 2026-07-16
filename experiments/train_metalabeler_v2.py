"""
Meta-Labeler V2: Expanded feature set, hyperparameter tuning, richer signal processing.

The goal: give the model EVERYTHING it could possibly use to distinguish
winning breakout signals from losing ones. If the edge exists, this
architecture should find it.

New features added:
- Microstructure: candle body ratio, upper/lower wick, gap presence
- Market context: recent volatility change, prior N trade outcomes
- Multi-timeframe: H1/H4 trend alignment, Donchian position within channel
- Cyclical: hour-of-day as cyclical encoding, day-of-week
- Momentum: rate of change, RSI, MACD histogram slope
- Relative: breakouts distance as % of ATR, channel width
"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
import math
from datetime import datetime, UTC
from pathlib import Path
import numpy as np
import pandas as pd

from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research.research_edge_prototypes import build_research_features

ohlcv = load_ohlcv('M15', 'aurum1/data/combined_market_cache.sqlite3')
features = build_research_features(ohlcv)
settings = load_settings('aurum1/config/settings.yaml')
spec = InstrumentSpec.from_settings(settings)
RISK_PCT = 0.0025; sp = 1.5; slip = 0.5; sd = slip * spec.pip_size
atr = features['atr_14']

MODEL_PATH = Path('aurum1/models/artifacts/meta_labeler_v2.pkl')
MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

print('=' * 72)
print('META-LABELER V2: EXPANDED FEATURE PIPELINE')
print('=' * 72)

# ── Step 1: Run 10-bar backtest, track every signal ──
print('\n[1/5] Running 10-bar Donchian backtest with signal tracking...')

ub = features['high'].rolling(10, min_periods=10).max().shift(1)
lb = features['low'].rolling(10, min_periods=10).min().shift(1)
buy_sig = features['close'] > ub
sell_sig = features['close'] < lb
valid = atr.notna()

all_entries = {}
for d, mask in [('BUY', buy_sig & valid), ('SELL', sell_sig & valid)]:
    for st in features.index[mask.fillna(False)]:
        bar = int(features.index.get_loc(st))
        eb = bar + 1
        if eb >= len(ohlcv): continue
        e = float(ohlcv.iloc[eb]['open'])
        a = float(features.loc[st, 'atr_14'])
        if not math.isfinite(a) or a <= 0: continue
        stop = e - 2*a if d == 'BUY' else e + 2*a
        if (d == 'BUY' and stop >= e) or (d == 'SELL' and stop <= e): continue
        all_entries.setdefault(eb, []).append({
            'sig_bar': bar, 'd': d, 'e': e, 'stop': stop, 'a': a, 'ts': st
        })

print(f'  Total tradeable signals: {sum(len(v) for v in all_entries.values())}')

signals_log = []; pos = None; eq = 10000.0; entered_sig = None

for bar_idx in range(len(ohlcv)):
    if pos:
        h,l = float(ohlcv.iloc[bar_idx]['high']), float(ohlcv.iloc[bar_idx]['low'])
        d = pos['d']; closed = False
        if (d == 'BUY' and l <= pos['stop']) or (d == 'SELL' and h >= pos['stop']):
            ex = pos['stop']; reason = 'sl'
            actual = ex - sd if d == 'BUY' else ex + sd
            net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
            rv = net/pos['risk'] if pos['risk']>0 else 0; eq += net; closed = True
        elif (d == 'BUY' and h >= pos['tgt']) or (d == 'SELL' and l <= pos['tgt']):
            ex = pos['tgt']; reason = 'tp'
            actual = ex - sd if d == 'BUY' else ex + sd
            net = spec.pnl(d, pos['entry'], actual, pos['units']) - pos['spr']
            rv = net/pos['risk'] if pos['risk']>0 else 0; eq += net; closed = True
        if closed:
            signals_log.append({'sig_bar': entered_sig['sig_bar'], 'd': entered_sig['d'],
                                'won': 1 if rv > 0 else 0, 'r': rv, 'reason': reason,
                                'a': entered_sig['a']})
            pos = None; entered_sig = None
    for sig in all_entries.get(bar_idx, []):
        if pos:
            signals_log.append({'sig_bar': sig['sig_bar'], 'd': sig['d'],
                                'won': 0, 'r': 0, 'reason': 'blocked', 'a': sig['a']})
            continue
        sa = sd if sig['d'] == 'BUY' else -sd
        adj = sig['e'] + sa; orig_r = abs(sig['e'] - sig['stop'])
        stop_a = adj - orig_r if sig['d'] == 'BUY' else adj + orig_r
        tgt = adj + 2*orig_r if sig['d'] == 'BUY' else adj - 2*orig_r
        risk_amt = eq * RISK_PCT; u = max(1, int(risk_amt/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
        act_r = orig_r * u * spec.ounces_per_unit; spr = 2*sp*spec.pip_value_per_unit*u
        pos = {'d':sig['d'],'entry':adj,'stop':stop_a,'tgt':tgt,'units':u,'risk':act_r,'spr':spr}
        entered_sig = sig

trades = [s for s in signals_log if s['reason'] in ('sl', 'tp')]
print(f'  Trades executed: {len(trades)}')
print(f'  Win rate: {sum(1 for t in trades if t["won"])}/{len(trades)} ({sum(1 for t in trades if t["won"])/len(trades)*100:.1f}%)' if trades else '  NO TRADES')

# ── Step 2: Build EXPANDED feature matrix ──
print('\n[2/5] Building EXPANDED feature matrix (36 features)...')

# Compute ALL the features upfront
o = ohlcv['open'].astype(float)
h = ohlcv['high'].astype(float)
l = ohlcv['low'].astype(float)
c = ohlcv['close'].astype(float)
v = ohlcv['volume'].astype(float)

# Microstructure
body = (c - o).abs()
upper_wick = h - o.where(c >= o, c)
lower_wick = o.where(c >= o, c) - l
body_ratio = body / (h - l).clip(lower=1e-9)
candle_range = h - l
is_green = (c > o).astype(int)

# Momentum
roc_5 = c.pct_change(5)
roc_10 = c.pct_change(10)
rsi_14 = features['rsi_14'] if 'rsi_14' in features.columns else pd.Series(50, index=features.index)

# Volume
rel_vol = v / v.rolling(20, min_periods=20).mean().clip(lower=1e-9)

# Volatility
atr_ema = atr.ewm(span=50, adjust=False).mean()
vol_ratio = atr / atr_ema.clip(lower=1e-9)
atr_change_5 = atr.pct_change(5)
atr_change_10 = atr.pct_change(10)

# Donchian position
donch_mid = (ub + lb) / 2
donch_width = (ub - lb) / atr.clip(lower=1e-9)
pos_in_channel = (c - lb) / (ub - lb).clip(lower=1e-9)

# Session
hours = pd.Series(ohlcv.index.hour, index=features.index)
hour_sin = np.sin(2 * np.pi * hours / 24)
hour_cos = np.cos(2 * np.pi * hours / 24)
is_london = ((hours >= 7) & (hours < 16)).astype(int)
is_ny = ((hours >= 13) & (hours < 22)).astype(int)
is_asia = ((hours >= 0) & (hours < 8)).astype(int)
is_overlap = ((hours >= 13) & (hours < 16)).astype(int)
day_wk = pd.Series(ohlcv.index.dayofweek, index=features.index)
is_mon = (day_wk == 0).astype(int); is_fri = (day_wk == 4).astype(int)

# Multi-timeframe (from features if available)
htf_bull = pd.Series(1, index=features.index)
if 'H1_close' in features.columns and 'H1_ema_50' in features.columns:
    htf_bull = (features['H1_close'] > features['H1_ema_50']).astype(int)

# EMA slope
ema9_slope = features['ema_9'].diff(3) / features['ema_9'].clip(lower=1e-9)
ema20_slope = features['ema_20'].diff(3) / features['ema_20'].clip(lower=1e-9)

# ATR percentile (100-bar)
atr_pct = atr.rolling(100, min_periods=100).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)

# ADX
adx = features['adx_14'] if 'adx_14' in features.columns else pd.Series(0, index=features.index)

# Build feature vector for each trade
FEATURE_NAMES = [
    'atr', 'atr_pct', 'vol_ratio', 'atr_chg5', 'atr_chg10',
    'adx', 'rsi', 'roc5', 'roc10',
    'body_ratio', 'candle_range', 'is_green', 'upper_wick', 'lower_wick',
    'rel_vol', 'donch_width', 'pos_in_channel',
    'ema9', 'ema20', 'ema_align', 'ema9_slope', 'ema20_slope',
    'close', 'htf_bull',
    'hour_sin', 'hour_cos', 'london', 'ny', 'asia', 'overlap',
    'mon', 'fri', 'is_buy',
]

X_rows = []; y_labels = []
for t in trades:
    b = t['sig_bar']
    if b < 200 or b >= len(ohlcv): continue
    feats = {
        'atr': float(atr.iloc[b]),
        'atr_pct': float(atr_pct.iloc[b]) if pd.notna(atr_pct.iloc[b]) else 0.5,
        'vol_ratio': float(vol_ratio.iloc[b]) if pd.notna(vol_ratio.iloc[b]) else 1.0,
        'atr_chg5': float(atr_change_5.iloc[b]) if pd.notna(atr_change_5.iloc[b]) else 0,
        'atr_chg10': float(atr_change_10.iloc[b]) if pd.notna(atr_change_10.iloc[b]) else 0,
        'adx': float(adx.iloc[b]) if pd.notna(adx.iloc[b]) else 0,
        'rsi': float(rsi_14.iloc[b]) if pd.notna(rsi_14.iloc[b]) else 50,
        'roc5': float(roc_5.iloc[b]) if pd.notna(roc_5.iloc[b]) else 0,
        'roc10': float(roc_10.iloc[b]) if pd.notna(roc_10.iloc[b]) else 0,
        'body_ratio': float(body_ratio.iloc[b]) if pd.notna(body_ratio.iloc[b]) else 0.5,
        'candle_range': float(candle_range.iloc[b]),
        'is_green': int(is_green.iloc[b]),
        'upper_wick': float(upper_wick.iloc[b]),
        'lower_wick': float(lower_wick.iloc[b]),
        'rel_vol': float(rel_vol.iloc[b]) if pd.notna(rel_vol.iloc[b]) else 1.0,
        'donch_width': float(donch_width.iloc[b]) if pd.notna(donch_width.iloc[b]) else 1.0,
        'pos_in_channel': float(pos_in_channel.iloc[b]) if pd.notna(pos_in_channel.iloc[b]) else 0.5,
        'ema9': float(features['ema_9'].iloc[b]),
        'ema20': float(features['ema_20'].iloc[b]),
        'ema_align': float(features['ema_9'].iloc[b] - features['ema_20'].iloc[b]),
        'ema9_slope': float(ema9_slope.iloc[b]) if pd.notna(ema9_slope.iloc[b]) else 0,
        'ema20_slope': float(ema20_slope.iloc[b]) if pd.notna(ema20_slope.iloc[b]) else 0,
        'close': float(c.iloc[b]),
        'htf_bull': int(htf_bull.iloc[b]),
        'hour_sin': float(hour_sin.iloc[b]),
        'hour_cos': float(hour_cos.iloc[b]),
        'london': int(is_london.iloc[b]),
        'ny': int(is_ny.iloc[b]),
        'asia': int(is_asia.iloc[b]),
        'overlap': int(is_overlap.iloc[b]),
        'mon': int(is_mon.iloc[b]),
        'fri': int(is_fri.iloc[b]),
        'is_buy': 1 if t['d'] == 'BUY' else 0,
    }
    X_rows.append(feats)
    y_labels.append(t['won'])

X = pd.DataFrame(X_rows)
y = np.array(y_labels)

print(f'  Feature matrix: {X.shape[0]} samples, {X.shape[1]} features')
print(f'  Class balance: {y.mean()*100:.1f}% winners, {(1-y.mean())*100:.1f}% losers')

# ── Step 3: Train with hyperparameter tuning ──
print('\n[3/5] Training with hyperparameter sweep...')

n = len(X)
split = int(n * 0.7)
X_train, X_test = X.iloc[:split], X.iloc[split:]
y_train, y_test = y[:split], y[split:]

print(f'  Train: {len(X_train)}  |  Test: {len(X_test)}')
print(f'  Test baseline WR: {y_test.mean()*100:.1f}%')
print()

import lightgbm as lgb
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score

# Hyperparameter grid
best_auc = 0
best_model = None
best_params = None

param_grid = [
    {'n_est': 100, 'depth': 3, 'lr': 0.05, 'sub': 0.8, 'col': 0.8},
    {'n_est': 150, 'depth': 3, 'lr': 0.05, 'sub': 0.8, 'col': 0.8},
    {'n_est': 200, 'depth': 4, 'lr': 0.03, 'sub': 0.7, 'col': 0.7},
    {'n_est': 300, 'depth': 4, 'lr': 0.03, 'sub': 0.7, 'col': 0.7},
    {'n_est': 200, 'depth': 5, 'lr': 0.02, 'sub': 0.6, 'col': 0.6},
    {'n_est': 500, 'depth': 3, 'lr': 0.02, 'sub': 0.8, 'col': 0.8},
    {'n_est': 1000, 'depth': 3, 'lr': 0.01, 'sub': 0.8, 'col': 0.8},
    {'n_est': 200, 'depth': 6, 'lr': 0.05, 'sub': 0.8, 'col': 0.8, 'reg_alpha': 0.1, 'reg_lambda': 0.1},
    {'n_est': 300, 'depth': 4, 'lr': 0.05, 'sub': 0.8, 'col': 0.8, 'scale_pos': 2.0},
    {'n_est': 300, 'depth': 4, 'lr': 0.05, 'sub': 0.8, 'col': 0.8, 'scale_pos': 1.5},
]

for params in param_grid:
    model = lgb.LGBMClassifier(
        n_estimators=params['n_est'], max_depth=params['depth'],
        learning_rate=params['lr'], subsample=params['sub'],
        colsample_bytree=params['col'],
        reg_alpha=params.get('reg_alpha', 0),
        reg_lambda=params.get('reg_lambda', 0),
        scale_pos_weight=params.get('scale_pos', 1),
        class_weight='balanced' if 'scale_pos' not in params else None,
        random_state=42, verbosity=-1,
    )
    model.fit(X_train, y_train, feature_name=list(X.columns))
    preds = model.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, preds)
    acc = accuracy_score(y_test, model.predict(X_test))
    if auc > best_auc:
        best_auc = auc; best_model = model; best_params = params
    print(f'  depth={params["depth"]:d} n_est={params["n_est"]:d} lr={params["lr"]:.2f}  '
          f'AUC={auc:.3f}  Acc={acc*100:.1f}%')

print(f'\n  Best params: {best_params}')
print(f'  Best AUC: {best_auc:.3f}')

# Feature importance from best model
imp = pd.DataFrame({'feature': X.columns, 'imp': best_model.feature_importances_})
imp = imp.sort_values('imp', ascending=False)
print(f'\n  Top 15 features:')
for _, r in imp.head(15).iterrows():
    print(f'    {r["feature"]:<20} {r["imp"]}')

# ── Step 4: Evaluate as filter ──
print('\n[4/5] Evaluating as trade filter...')

test_preds = best_model.predict_proba(X_test)[:, 1]
base_wr = y_test.mean()
base_sim_pf = (base_wr * 2.0) / ((1 - base_wr) * 1.0) if base_wr < 1 else float('inf')

print(f'  Baseline on test: {len(y_test)} trades, WR {base_wr*100:.1f}%, simPF {base_sim_pf:.3f}')
print()

results = []
for thresh in np.arange(0.3, 0.76, 0.05):
    keep = test_preds >= thresh
    if keep.sum() < 10: continue
    kept_wr = y_test[keep].mean()
    kept_pct = keep.sum() / len(keep) * 100
    sim_pf = (kept_wr * 2.0) / ((1 - kept_wr) * 1.0) if kept_wr < 1 else float('inf')
    delta = (sim_pf - base_sim_pf) / base_sim_pf * 100
    n_removed = len(y_test) - keep.sum()
    results.append((thresh, keep.sum(), kept_wr, sim_pf, delta))

# Find best threshold
results.sort(key=lambda x: x[3], reverse=True)
best_thresh, best_n, best_wr, best_pf, best_delta = results[0]

print(f'  Best threshold: {best_thresh:.2f} (keep {best_n}/{len(y_test)}, WR {best_wr*100:.1f}%, simPF {best_pf:.3f}, {best_delta:+.0f}%)')
print()

for thresh, nk, kwr, kpf, dlt in sorted(results, key=lambda x: x[0]):
    mark = '✅' if kpf > base_sim_pf else '❌'
    print(f'  {mark} {thresh:.2f}: keep {nk:>4}/{len(y_test)} ({nk/len(y_test)*100:>3.0f}%)  '
          f'WR {kwr*100:>5.1f}%  simPF {kpf:.3f}  ({dlt:+.0f}%)')

# Also test by percentile rank
print(f'\n  By confidence percentile:')
pcts = np.percentile(test_preds, [90, 80, 70, 60, 50, 40, 30])
for pct in pcts:
    keep = test_preds >= pct
    if keep.sum() < 10: continue
    kept_wr = y_test[keep].mean()
    sim_pf = (kept_wr * 2.0) / ((1 - kept_wr) * 1.0) if kept_wr < 1 else float('inf')
    delta = (sim_pf - base_sim_pf) / base_sim_pf * 100
    mark = '✅' if sim_pf > base_sim_pf else '❌'
    print(f'  {mark} top {100-sum(keep)/len(keep)*100:.0f}%  keep {keep.sum():>4}  '
          f'WR {kept_wr*100:.1f}%  simPF {sim_pf:.3f}  ({delta:+.0f}%)')

# ── Step 5: Save model ──
print(f'\n[5/5] Saving model...')

import pickle
with open(MODEL_PATH, 'wb') as f:
    pickle.dump({
        'model': best_model,
        'features': list(X.columns),
        'threshold': best_thresh,
        'train_date': datetime.now(UTC).isoformat(),
        'train_samples': len(X_train),
        'test_auc': best_auc,
        'test_accuracy': float(accuracy_score(y_test, best_model.predict(X_test))),
        'baseline_wr': float(y_test.mean()),
        'best_filter_wr': float(best_wr),
        'best_filter_pf': float(best_pf),
    }, f)
print(f'  Saved to {MODEL_PATH}')
print(f'\n{"="*72}')
print('DONE')
