# GC=F Daily 15-bar Donchian — Standalone Strategy Spec

**For use as a separate trading system in its own folder.**

## Performance (26 years, 2000-2026)

| Metric | 10-bar | **15-bar (best)** | 20-bar |
|--------|:-----:|:-----------------:|:------:|
| Profit Factor | 1.373 | **1.522** | 1.374 |
| Win Rate | 40.7% | **43.2%** | 40.7% |
| Trades | 452 | **398** (15/yr avg) | 361 |
| Avg R | 0.221 | **0.296** | 0.222 |
| PnL ($10k) | +$2,596 | **+$3,658** | +$2,066 |

## Strategy Rules

```python
# Entry Signal (at daily close)
LOOKBACK = 15
buy_signal  = close > high.rolling(LOOKBACK).max().shift(1)
sell_signal = close < low.rolling(LOOKBACK).min().shift(1)

# Entry (next day open)
entry = next_open
stop_loss = entry - 2 * atr(14)  if BUY
stop_loss = entry + 2 * atr(14)  if SELL
take_profit = entry + 4 * atr(14) if BUY
take_profit = entry - 4 * atr(14) if SELL

# Risk
position_size = (equity * 0.0025) / (|entry - stop| * contract_value)
```

## Key Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Instrument | GC=F | Gold Futures (CME) |
| Timeframe | Daily | Close-to-close |
| Lookback | 15 bars | ~3 trading weeks |
| Exit | Fixed 2R | Same as D4 |
| Risk per trade | 0.25% | Standard |
| Data source | yfinance | `yf.download("GC=F", period="max")` |

## Files to Copy for New System

To set this up independently in another folder:
1. Copy the backtest logic from `experiments/run_multasset.py`
2. Run against GC=F data from yfinance
3. Same infrastructure as AURUM-1 but with daily timeframe and futures instrument

## Why It Works on Daily

- **Fewer trades, higher quality** — 15/year vs ~740/year on M15. Each trade has more room to run
- **Less noise** — daily candles filter out intraday randomness
- **Futures structure** — gold futures have different participants (institutions, hedgers) creating cleaner trends
- **26 years of validation** — survived multiple macro regimes (dot-com, GFC, COVID, inflation)
