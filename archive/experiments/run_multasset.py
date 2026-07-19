"""Multi-asset validation on GC=F (26yr) and GLD (22yr)."""
import sys; sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
import pandas as pd, numpy as np
from pathlib import Path
import csv, json
from datetime import datetime, UTC

LOG = Path('experiments/results/sweep_003_multasset.csv')
LOG.parent.mkdir(parents=True, exist_ok=True)

def run_gcf(lookback, filepath, price_col='Close'):
    gc = pd.read_csv(filepath, index_col=0, parse_dates=True)
    for c in ['Open','High','Low','Close','Volume']:
        gc[c] = pd.to_numeric(gc[c], errors='coerce')
    gc = gc.dropna(subset=[price_col])

    ub = gc['High'].rolling(lookback).max().shift(1)
    lb = gc['Low'].rolling(lookback).min().shift(1)
    tr = pd.concat([
        gc['High']-gc['Low'],
        (gc['High']-gc['Close'].shift(1)).abs(),
        (gc['Low']-gc['Close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, min_periods=14).mean()

    entries = {}
    for d, mask in [('BUY', gc[price_col] > ub), ('SELL', gc[price_col] < lb)]:
        idx_list = list(gc.index)
        for t in gc.index[mask.fillna(False)]:
            idx = idx_list.index(t); ei = idx + 1
            if ei >= len(gc): continue
            a = float(atr.iloc[idx])
            if pd.isna(a) or a <= 0: continue
            c = float(gc[price_col].iloc[idx])
            stop = c - 2*a if d == 'BUY' else c + 2*a
            if (d == 'BUY' and stop >= c) or (d == 'SELL' and stop <= c): continue
            entries.setdefault(ei, []).append({'d':d,'e':c,'stop':stop,'a':a})

    eq = 10000.0; pos = None; trades = []
    for i in range(len(gc)):
        if pos:
            o,h,l = float(gc['Open'].iloc[i]), float(gc['High'].iloc[i]), float(gc['Low'].iloc[i])
            d = pos['d']
            if (d == 'BUY' and l <= pos['stop']) or (d == 'SELL' and h >= pos['stop']):
                ex = pos['stop']; net = (ex-pos['entry']) if d=='BUY' else (pos['entry']-ex)
                rv = net/pos['risk'] if pos['risk']>0 else 0; trades.append(rv); eq += net; pos = None
            elif (d == 'BUY' and h >= pos['tgt']) or (d == 'SELL' and l <= pos['tgt']):
                ex = pos['tgt']; net = (ex-pos['entry']) if d=='BUY' else (pos['entry']-ex)
                rv = net/pos['risk']; trades.append(rv); eq += net; pos = None
        for sig in entries.get(i, []):
            if pos: continue
            orig_r = abs(sig['e'] - sig['stop'])
            off_sl = orig_r if sig['d']=='BUY' else -orig_r
            off_tp = 2*orig_r if sig['d']=='BUY' else -2*orig_r
            pos = {'d':sig['d'],'entry':sig['e'],'stop':sig['e']-off_sl,'tgt':sig['e']+off_tp,'risk':orig_r}

    rvs = np.array(trades) if trades else np.array([0])
    w=sum(1 for r in rvs if r>0); g=sum(abs(r) for r in rvs if r>0); ls=sum(abs(r) for r in rvs if r<0)
    return {'trades':len(trades),'wr':w/len(trades)*100 if trades else 0,'pf':g/ls if ls>0 else 0,'pnl':eq-10000,'avg_r':float(np.mean(rvs)) if len(rvs)>0 else 0}

def log_result(asset, lb, r):
    is_new = not LOG.exists()
    with open(LOG, 'a', newline='') as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(['asset','lookback','trades','wr_pct','pf','pnl','avg_r','timestamp'])
        w.writerow([asset,lb,r['trades'],f"{r['wr']:.1f}",f"{r['pf']:.3f}",f"{r['pnl']:+.2f}",f"{r['avg_r']:.4f}",datetime.now(UTC).isoformat()])

print('========== MULTI-ASSET DONCHIAN VALIDATION ==========')
print()

# GC=F Gold Futures (26 years)
print('GC=F Gold Futures Daily (2000-2026, 26 years)')
for lb in [10, 15, 20]:
    r = run_gcf(lb, 'aurum1/data/gc_futures_daily_2000_2026.csv')
    log_result('GC=F', lb, r)
    print(f'  {lb}-bar:  {r["trades"]:>4}t  WR:{r["wr"]:>5.1f}%  PF:{r["pf"]:.3f}  PnL:${r["pnl"]:>+.0f}  AvgR:{r["avg_r"]:.3f}')

# GLD ETF (22 years)
print('\\nGLD ETF Daily (2004-2026, 22 years)')
for lb in [10, 15, 20]:
    r = run_gcf(lb, 'aurum1/data/gld_etf_daily_2004_2026.csv')
    log_result('GLD', lb, r)
    print(f'  {lb}-bar:  {r["trades"]:>4}t  WR:{r["wr"]:>5.1f}%  PF:{r["pf"]:.3f}  PnL:${r["pnl"]:>+.0f}  AvgR:{r["avg_r"]:.3f}')

print(f'\\nLog saved to {LOG}')
