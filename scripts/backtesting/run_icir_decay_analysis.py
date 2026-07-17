"""ICIR & Decay Analysis for D4 Donchian strategy.

ICIR (Information Coefficient Information Ratio):
  - IC = rank correlation (spearman) between signal direction and forward return
  - ICIR = mean(IC) / std(IC) over rolling windows — measures signal consistency

Decay Analysis:
  - Tracks signal predictive power across forward horizons (1, 2, 5, 10, 20, 50 bars)
  - A healthy signal peaks at horizon 1 and decays gracefully
  - A decaying signal that reverses (flips sign) suggests overfitting

D4 Signal: Donchian 20 breakout — BUY when close > 20-bar high, SELL when close < 20-bar low
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

LOOKBACK = 20

settings = load_settings(LOCAL / 'aurum1' / 'config' / 'settings.yaml')
spec = InstrumentSpec.from_settings(settings)

ohlcv = load_ohlcv('M15', LOCAL / 'aurum1' / 'data' / 'backtest_market_cache.sqlite3')
print(f'Data: {len(ohlcv)} M15 candles ({ohlcv.index[0].date()} to {ohlcv.index[-1].date()})')

features = build_research_features(ohlcv)

# Forward horizons in M15 bars
HORIZONS = [1, 2, 5, 10, 20, 50, 100]

# Generate D4 signals
high_20 = ohlcv['high'].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
low_20 = ohlcv['low'].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)

signals = pd.Series(0, index=ohlcv.index)
signals[ohlcv['close'] > high_20] = 1    # BUY
signals[ohlcv['close'] < low_20] = -1    # SELL

# Remove periods where signal is 0 (no signal)
signal_mask = signals != 0
print(f'Total signals: {signal_mask.sum()} ({signal_mask.sum()/len(ohlcv)*100:.2f}% of bars)')

# Compute forward returns and IC at each horizon
results = []
for h in HORIZONS:
    # Forward return over h bars
    fwd_ret = ohlcv['close'].shift(-h) / ohlcv['close'] - 1.0

    # Compute IC at each valid signal bar
    valid = signal_mask & fwd_ret.notna()
    if valid.sum() < 10:
        continue

    # Information Coefficient: Spearman rank correlation between signal and forward return
    from scipy.stats import spearmanr
    ic, ic_p = spearmanr(signals[valid], fwd_ret[valid])

    # ICIR: rolling IC mean / rolling IC std across monthly windows
    # First get time-series of (signal, return) pairs
    ic_series = []
    signal_vals = signals[valid]
    ret_vals = fwd_ret[valid]

    # Monthly IC: compute IC per calendar month
    monthly_ics = []
    monthly_idx = valid[valid].index
    for year_month in monthly_idx.to_period('M').unique():
        mask = monthly_idx.year == year_month.year
        mask &= monthly_idx.month == year_month.month
        chunk_signals = signal_vals.loc[mask]
        chunk_returns = ret_vals.loc[mask]
        if len(chunk_signals) >= 5:
            m_ic, _ = spearmanr(chunk_signals, chunk_returns)
            monthly_ics.append(m_ic)

    monthly_ics = np.array(monthly_ics)
    icir = monthly_ics.mean() / monthly_ics.std() if len(monthly_ics) > 1 and monthly_ics.std() > 0 else 0.0

    # Signal decay: does the signal still work at longer horizons?
    # We also compute hit rate: % of signals where direction matches forward return sign
    hit_rate = ((signals[valid] * fwd_ret[valid]) > 0).mean()

    # Mean forward return conditional on signal
    buy_returns = fwd_ret[valid & (signals == 1)]
    sell_returns = fwd_ret[valid & (signals == -1)]
    mean_buy_ret = buy_returns.mean() if len(buy_returns) > 0 else 0.0
    mean_sell_ret = sell_returns.mean() if len(sell_returns) > 0 else 0.0
    spread = mean_buy_ret - mean_sell_ret  # BUY - SELL return spread

    results.append({
        'horizon_bars': h,
        'horizon_minutes': h * 15,
        'horizon_label': f'{h*15}min' if h*15 < 60 else f'{h*15//60}h{h*15%60:02d}min' if h*15 < 1440 else f'{h*15//1440}d',
        'n_signals': int(valid.sum()),
        'ic': round(float(ic), 6),
        'ic_p_value': round(float(ic_p), 6),
        'icir': round(float(icir), 4),
        'n_monthly_windows': len(monthly_ics),
        'hit_rate': round(float(hit_rate), 4),
        'mean_buy_return_pct': round(float(mean_buy_ret * 100), 4),
        'mean_sell_return_pct': round(float(mean_sell_ret * 100), 4),
        'buy_sell_spread_pct': round(float(spread * 100), 4),
    })

    sig = '***' if ic_p < 0.001 else '**' if ic_p < 0.01 else '*' if ic_p < 0.05 else ''
    print(f'  Horizon {h:3d} bars ({h*15:3d}min): IC={ic:.4f}{sig} ICIR={icir:.2f} HitRate={hit_rate:.1%} Spread={spread*100:+.3f}%')

# Summary
print(f'\n{"="*80}')
print(f'{"D4 DONCHIAN SIGNAL: ICIR & DECAY ANALYSIS":^80}')
print(f'{"="*80}')
print(f'{"Horizon":>12s} {"IC":>10s} {"ICIR":>8s} {"HitRate":>10s} {"Spread%":>10s} {"Signals":>8s}')
print(f'{"-"*80}')
for r in results:
    print(f'{r["horizon_label"]:>12s} {r["ic"]:>10.4f} {r["icir"]:>8.2f} {r["hit_rate"]:>9.1%} {r["buy_sell_spread_pct"]:>+9.3f} {r["n_signals"]:>8d}')
print(f'{"="*80}')

# Decay analysis: what % of peak IC is retained at each horizon?
peak_ic = max(abs(r['ic']) for r in results)
peak_horizon = min(r['horizon_bars'] for r in results if abs(r['ic']) == peak_ic)
print(f'\nDecay: Peak IC at {peak_horizon} bars ({peak_ic:.4f})')
print(f'{"Horizon":>12s} {"IC":>10s} {"% of Peak":>10s} {"Status":>12s}')
for r in results:
    pct = abs(r['ic']) / peak_ic * 100
    status = 'Healthy' if pct >= 50 else 'Weakening' if pct >= 25 else 'Decayed'
    print(f'{r["horizon_label"]:>12s} {r["ic"]:>+10.4f} {pct:>9.1f}% {status:>12s}')

# Save results
out = LOCAL / 'reports' / 'forward_shadow' / 'd4_icir_decay_analysis.json'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({
    'analysis': 'D4 Donchian Signal ICIR & Decay Analysis',
    'data_range': f'{ohlcv.index[0].date()} to {ohlcv.index[-1].date()}',
    'total_candles': len(ohlcv),
    'total_signals': int(signal_mask.sum()),
    'signal_frequency_pct': round(float(signal_mask.sum() / len(ohlcv) * 100), 2),
    'horizons': results,
    'decay': {
        'peak_ic': round(float(peak_ic), 6),
        'peak_horizon_bars': peak_horizon,
        'half_life_bars': next((r['horizon_bars'] for r in results if abs(r['ic']) < peak_ic * 0.5), None),
    },
    'generated_at': datetime.now(UTC).isoformat(),
}, indent=2))
print(f'\nSaved: {out}')
