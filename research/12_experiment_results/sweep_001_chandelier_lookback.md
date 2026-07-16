# Experiment Sweep 001: Chandelier Exit × Donchian Lookback

**Date:** 2026-07-16
**Data:** 237k M15 candles (2016-06 → 2026-07)
**Purpose:** Find optimal exit strategy and channel lookback

## Baseline: D4 (20-bar Donchian + fixed 2R)
| Metric | Value |
|--------|-------|
| Trades | 8,224 |
| Win Rate | 37.0% |
| Profit Factor | 1.156 |
| Net PnL | +$58,049 |
| Avg R | 0.099 |

---

## SWEEP 1: Chandelier Multiplier (20-bar, no filter)

| Mult | Trades | WR | PF | PnL | Avg R | vs Base |
|------|--------|-----|-----|------|-------|---------|
| 2.0x | 12,286 | 36.3% | 0.707 | -$13,350 | -0.117 | -38.9% |
| 2.5x | 10,013 | 36.3% | 0.787 | -$10,870 | -0.102 | -32.0% |
| 3.0x | 8,450 | 37.2% | 0.886 | -$6,654 | -0.063 | -23.4% |
| 3.5x | 7,385 | 36.9% | 0.942 | -$4,495 | -0.036 | -18.5% |
| 4.0x | 6,526 | 36.6% | 0.984 | -$2,471 | -0.011 | -14.9% |
| **4.5x** | **5,806** | **36.3%** | **1.009** | **+$623** | **0.007** | **-12.7%** |
| 5.0x | 5,160 | 35.8% | 1.025 | +$1,879 | 0.021 | -11.4% |
| 5.5x | 4,508 | 36.6% | 1.114 | +$15,494 | 0.101 | -3.7% |
| **6.0x** | **4,016** | **36.2%** | **1.133** | **+$19,592** | **0.128** | **-2.0%** |
| 6.5x | 3,663 | 36.0% | 1.090 | +$9,465 | 0.094 | -5.7% |
| 7.0x | 3,381 | 34.9% | 1.048 | +$3,298 | 0.055 | -9.3% |
| 8.0x | 2,784 | 37.4% | 1.099 | +$9,382 | 0.123 | -5.0% |

**Finding:** Chandelier peaks at 6.0x ATR (PF 1.133, Avg R 0.128) but never beats the fixed 2R baseline for 20-bar Donchian. The Chandelier performs best on M15 gold with wider multipliers (5.5-6.0x) but the fixed 2R exit is surprisingly optimal.

---

## SWEEP 2: 10-bar Donchian (shorter lookback)

| Config | Trades | WR | PF | PnL | Avg R | vs Base |
|--------|--------|-----|-----|------|-------|---------|
| **10-bar + fixed 2R** | **9,168** | **37.9%** | **1.204** | **+$152,590** | **0.128** | **+4.1%** |
| 10-bar + Ch 5.5x | 4,731 | 37.6% | 1.176 | +$39,869 | 0.152 | +1.7% |
| 10-bar + Ch 6.0x | 4,175 | 37.0% | 1.181 | +$37,770 | 0.170 | +2.1% |

**BREAKTHROUGH:** 10-bar Donchian + fixed 2R is the **best single variant ever tested** — PF 1.204, +$152,590 PnL, WR 37.9%. More trades, better WR, higher PnL. This is a significant finding: the shorter lookback captures more breakouts and generates higher total returns.

---

## SWEEP 3: 15-bar Donchian

| Config | Trades | WR | PF | PnL | Avg R | vs Base |
|--------|--------|-----|-----|------|-------|---------|
| 15-bar + fixed 2R | 8,697 | 37.3% | 1.175 | +$87,327 | 0.111 | +1.7% |
| 15-bar + Ch 6.0x | 4,103 | 36.5% | 1.154 | +$25,843 | 0.147 | -0.2% |

15-bar outperforms 20-bar but underperforms 10-bar. Confirms the trend: **shorter lookbacks capture more profitable breakouts** in gold M15.

---

## Summary Rankings (Top 10)

| Rank | Config | PF | PnL | WR | Avg R |
|------|--------|-----|------|-----|-------|
| 🥇 | **10-bar + fixed 2R** | **1.204** | **+$152,590** | 37.9% | 0.128 |
| 🥈 | 10-bar + Ch 6.0x | 1.181 | +$37,770 | 37.0% | **0.170** |
| 🥉 | 10-bar + Ch 5.5x | 1.176 | +$39,869 | 37.6% | **0.152** |
| 4 | 15-bar + fixed 2R | 1.175 | +$87,327 | 37.3% | 0.111 |
| 5 | 20-bar + fixed 2R | 1.156 | +$58,049 | 37.0% | 0.099 |
| 6 | 15-bar + Ch 6.0x | 1.154 | +$25,843 | 36.5% | 0.147 |
| 7 | 20-bar + Ch 6.0x | 1.133 | +$19,592 | 36.2% | 0.128 |
| 8 | 15-bar + Ch 5.5x | 1.137 | +$23,966 | 37.0% | 0.120 |
| 9 | 20-bar + Ch 5.5x | 1.114 | +$15,494 | 36.6% | 0.101 |
| 10 | 10-bar + Ch 5.0x | 1.113 | +$18,377 | 36.8% | 0.091 |

## Key Takeaways

1. **10-bar Donchian beats 20-bar** — more signals, higher PF, higher PnL. The shorter lookback is better for M15 gold
2. **Fixed 2R exit beats Chandelier** for this system — the simplicity of fixed targets works better than trailing stops on M15 gold
3. **Chandelier still useful** — if capital efficiency matters (50% fewer trades for similar PF), Chandelier 5.5-6.0x on 10-bar is a viable alternative
4. **Avg R peaks at 6.0x** for Chandelier across all lookbacks — the 6.0x multiplier is a consistent sweet spot

## Next Steps

- [ ] Test 10-bar Donchian + fixed 2R on **GC=F daily (26 years)** for out-of-sample validation
- [ ] Add **volatility scaling** to 10-bar configuration (constant risk per trade)
- [ ] Test 10-bar + **ADX filter** to further improve PF
- [ ] Run walk-forward validation on 10-bar + fixed 2R
