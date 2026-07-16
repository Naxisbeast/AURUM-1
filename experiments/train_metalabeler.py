"""
Train and evaluate the meta-labeler on 10-bar Donchian signals.

Meta-labeling (Lopez de Prado, 2018) trains a binary classifier to predict
whether a breakout signal will succeed or fail.

Pipeline:
1. Run 10-bar Donchian backtest -> collect all signals + trade outcomes
2. Build feature matrix at each signal time
3. Train LightGBM classifier (temporal split, no leakage)
4. Evaluate as trade filter vs baseline
"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
import math, json, time
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

MODEL_PATH = Path('aurum1/models/artifacts/meta_labeler_latest.pkl')
MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

print('=' * 68)
print('META-LABELER TRAINING PIPELINE')
print('=' * 68)

# Step 1: Run backtest, tracking each signal with its outcome
print('\n[1/4] Running 10-bar Donchian backtest with signal tracking...')

ub = features['high'].rolling(10, min_periods=10).max().shift(1)
lb = features['low'].rolling(10, min_periods=10).min().shift(1)
buy_sig = features['close'] > ub
sell_sig = features['close'] < lb
valid = atr.notna()

# Map signal bar -> signal info
all_entries = {}  # bar -> list of signal dicts
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
            'sig_bar': bar, 'd': d, 'e': e, 'stop': stop, 'a': a,
            'ts': st
        })

print(f'  Total tradeable signals: {sum(len(v) for v in all_entries.values())}')

# Run backtest -- track each signal's fate
signals_log = []  # list of {sig_bar, dir, won, r, reason}
pos = None; eq = 10000.0
entered_sig = None  # which signal we entered

for bar_idx in range(len(ohlcv)):
    # Exit
    if pos:
        h,l = float(ohlcv.iloc[bar_idx]['high']), float(ohlcv.iloc[bar_idx]['low'])
        d = pos['d']
        closed = False
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
                                'a': entered_sig['a'], 'dir': 1 if entered_sig['d'] == 'BUY' else 0,
                                'entry_price': pos['entry']})
            pos = None; entered_sig = None

    # Entry
    for sig in all_entries.get(bar_idx, []):
        if pos:
            # Signal was blocked by existing position
            signals_log.append({'sig_bar': sig['sig_bar'], 'd': sig['d'],
                                'won': 0, 'r': 0, 'reason': 'blocked',
                                'a': sig['a'], 'dir': 1 if sig['d'] == 'BUY' else 0,
                                'entry_price': 0})
            continue
        sa = sd if sig['d'] == 'BUY' else -sd
        adj = sig['e'] + sa; orig_r = abs(sig['e'] - sig['stop'])
        stop_a = adj - orig_r if sig['d'] == 'BUY' else adj + orig_r
        tgt = adj + 2*orig_r if sig['d'] == 'BUY' else adj - 2*orig_r
        risk_amt = eq * RISK_PCT; u = max(1, int(risk_amt/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
        act_r = orig_r * u * spec.ounces_per_unit; spr = 2*sp*spec.pip_value_per_unit*u
        pos = {'d':sig['d'],'entry':adj,'stop':stop_a,'tgt':tgt,'units':u,'risk':act_r,'spr':spr}
        entered_sig = sig

# Filter to actual trades (not blocked)
trades = [s for s in signals_log if s['reason'] in ('sl', 'tp')]
n_skipped = sum(1 for s in signals_log if s['reason'] == 'blocked')
wins = sum(1 for t in trades if t['won'])

print(f'  Trades executed: {len(trades)}')
print(f'  Signals blocked (position existed): {n_skipped}')
print(f'  Win rate: {wins}/{len(trades)} ({wins/len(trades)*100:.1f}%)' if trades else '  NO TRADES')
if trades:
    r_vals = [t['r'] for t in trades]
    print(f'  Avg R: {np.mean(r_vals):.3f}  |  PF: {sum(r for r in r_vals if r>0)/abs(sum(r for r in r_vals if r<0)):.3f}')

# Step 2: Feature engineering at signal time
print('\n[2/4] Building feature matrix at signal time...')

# Pre-compute rolling features
atr_pct = atr.rolling(100, min_periods=100).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
atr_median = atr.rolling(100, min_periods=100).median()
adx = features['adx_14'] if 'adx_14' in features.columns else pd.Series(0, index=features.index)
hours = pd.Series(ohlcv.index.hour, index=features.index)
is_london = ((hours >= 7) & (hours < 16)).astype(int)
is_ny = ((hours >= 13) & (hours < 22)).astype(int)
day_wk = pd.Series(ohlcv.index.dayofweek, index=features.index)
is_mon = (day_wk == 0).astype(int); is_fri = (day_wk == 4).astype(int)

X_rows = []; y_labels = []
for t in trades:
    b = t['sig_bar']
    if b < 100 or b >= len(ohlcv): continue
    d = t['dir']
    feats = {
        'atr': float(atr.iloc[b]),
        'atr_pct': float(atr_pct.iloc[b]) if pd.notna(atr_pct.iloc[b]) else 0.5,
        'atr_vs_med': float(atr.iloc[b] / max(atr_median.iloc[b], 1e-9)) if pd.notna(atr_median.iloc[b]) else 1.0,
        'adx': float(adx.iloc[b]) if pd.notna(adx.iloc[b]) else 0,
        'ema9': float(features['ema_9'].iloc[b]),
        'ema20': float(features['ema_20'].iloc[b]),
        'ema_align': float(features['ema_9'].iloc[b] - features['ema_20'].iloc[b]),
        'close': float(features['close'].iloc[b]),
        'is_buy': d,
        'london': int(is_london.iloc[b]),
        'ny': int(is_ny.iloc[b]),
        'mon': int(is_mon.iloc[b]),
        'fri': int(is_fri.iloc[b]),
    }
    X_rows.append(feats)
    y_labels.append(t['won'])

X = pd.DataFrame(X_rows)
y = np.array(y_labels)

print(f'  Feature matrix: {X.shape[0]} samples, {X.shape[1]} features')
print(f'  Class balance: {y.mean()*100:.1f}% winners')

# Step 3: Train
print('\n[3/4] Training LightGBM meta-labeler...')

# Temporal split (first 70% train, last 30% test)
n = len(X)
split = int(n * 0.7)
X_train, X_test = X.iloc[:split], X.iloc[split:]
y_train, y_test = y[:split], y[split:]

print(f'  Train: {len(X_train)} samples  |  Test: {len(X_test)} samples')
print(f'  Test WR: {y_test.mean()*100:.1f}%')

try:
    import lightgbm as lgb
    model = lgb.LGBMClassifier(
        n_estimators=150, max_depth=3, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        class_weight='balanced', random_state=42, verbosity=-1,
    )
    model.fit(X_train, y_train, feature_name=list(X.columns))

    train_acc = (model.predict(X_train) == y_train).mean()
    test_preds = model.predict_proba(X_test)[:, 1]
    test_acc = (model.predict(X_test) == y_test).mean()

    # Feature importance
    imp = pd.DataFrame({'feature': X.columns, 'imp': model.feature_importances_}).sort_values('imp', ascending=False)

    print(f'\n  Train accuracy: {train_acc*100:.1f}%')
    print(f'  Test accuracy:  {test_acc*100:.1f}%')
    print(f'  Baseline (all-in): {y_test.mean()*100:.1f}%')
    print(f'\n  Feature importance:')
    for _, r in imp.iterrows():
        print(f'    {r["feature"]:<15} {r["imp"]}')

    # Step 4: Simulate as trade filter
    print('\n[4/4] Evaluating as trade filter on test set...')

    # Baseline on test period
    base_pf = sum(r for t in trades[split:] if (r:=t['r'])>0) / abs(sum(r for t in trades[split:] if (r:=t['r'])<0)) if any(t['r']<0 for t in trades[split:]) else float('inf')
    base_wr = y_test.mean()

    for thresh in [0.4, 0.45, 0.5, 0.55, 0.6, 0.65]:
        keep = test_preds >= thresh
        if keep.sum() < 5: continue
        kept_wr = y_test[keep].mean()
        kept_pct = keep.sum() / len(keep) * 100

        # Simulate PF: assume 2R avg win, 1R avg loss
        n_w = int(kept_wr * keep.sum())
        n_l = keep.sum() - n_w
        sim_pf = (n_w * 2.0) / (n_l * 1.0) if n_l > 0 else float('inf')

        n_w_b = int(base_wr * len(y_test))
        n_l_b = len(y_test) - n_w_b
        base_sim_pf = (n_w_b * 2.0) / (n_l_b * 1.0) if n_l_b > 0 else float('inf')

        delta = (sim_pf - base_sim_pf) / base_sim_pf * 100 if base_sim_pf > 0 else 0
        mark = '✅' if sim_pf > base_sim_pf else '❌'
        print(f'  {mark} thresh={thresh:.2f}: keep {keep.sum()}/{len(keep)} ({kept_pct:.0f}%)  '
              f'WR {kept_wr*100:.1f}%  simPF {sim_pf:.3f}  '
              f'(baseline: WR {base_wr*100:.1f}%  simPF {base_sim_pf:.3f})  {delta:+.0f}%')

    # Save model
    import cloudpickle
    with open(MODEL_PATH, 'wb') as f:
        cloudpickle.dump({
            'model': model, 'features': list(X.columns), 'threshold': 0.50,
            'train_date': datetime.now(UTC).isoformat(),
            'train_samples': len(X_train), 'test_accuracy': test_acc,
        }, f)
    print(f'\n  Model saved to {MODEL_PATH}')

except ImportError:
    print('  lightgbm not installed. Install with: pip install lightgbm')
    print('  Skipping training.')
