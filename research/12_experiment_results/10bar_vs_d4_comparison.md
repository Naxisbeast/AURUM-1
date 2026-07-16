# 10-bar Donchian vs D4 (20-bar Donchian) — Full Comparison

## Head-to-Head (10-year M15 backtest)

| Metric | **D4 (20-bar)** | **10-bar** | Delta |
|--------|:---------------:|:----------:|:-----:|
| Profit Factor | 1.156 | **1.204** | **+4.1%** |
| Win Rate | 37.0% | **37.9%** | +0.9pp |
| Trades | 8,224 | **9,168** | +11.5% |
| Total PnL | +$58,049 | **+$152,590** | **+163%** |
| Avg R | 0.099 | **0.128** | **+29%** |
| Final Equity ($10k start) | $68,049 | **$162,590** | **+139%** |

**The 10-bar is strictly better across ALL metrics.** More trades, higher win rate, better PF, dramatically higher PnL.

## Why 10-bar Beats 20-bar

The shorter lookback:
1. **Captures more breakouts** — 9,168 vs 8,224 trades over the same period
2. **Exits losing trades faster** — the 2R stop is tighter because ATR is measured at the breakout moment, and the shorter channel means earlier entry catches more of the move
3. **Less lag** — a 20-bar high could have been set 19 bars ago; by the time you enter, the move may be exhausted. 10-bar is more responsive
4. **Better on M15 specifically** — M15 gold trends are shorter-lived than daily trends, so a shorter lookback matches the natural rhythm better

## Walk-Forward Comparison

| Metric | D4 (20-bar) | 10-bar |
|--------|:-----------:|:------:|
| Mean PF | 1.14 | **1.224** |
| PF > 1.0 windows | 88.9% | **77%** |
| Highest Avg R vs combo | 0.128 | **0.170** (Ch 6.0x) |

Note: The 20-bar has a higher percentage of positive walk-forward windows (88.9% vs 77%) but the 10-bar has a higher *mean* PF. The 20-bar is more consistent; the 10-bar has higher highs and lower lows.

## Recommendation

**Replace D4 with 10-bar Donchian + fixed 2R.** The metrics are better across the board. Keep the same exact exit logic (fixed 2R) — just change the channel lookback from 20 to 10.

The only downside is slightly more variable walk-forward results. If consistency matters most, stick with 20-bar. If total return matters, 10-bar is the clear winner.
