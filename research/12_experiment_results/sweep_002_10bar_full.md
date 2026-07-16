# Experiment Sweep 002: 10-Bar Donchian Full Improvement Scan

**Date:** 2026-07-16
**Data:** 237k M15 candles (2016-06 → 2026-07) + GC=F 26yr daily + GLD 22yr daily
**Purpose:** Find the best 10-bar Donchian configuration with exit and filter improvements

---

## Summary Rankings

| Rank | Config | PF | Trades | WR | PnL | Avg R |
|------|--------|-----|--------|-----|------|-------|
| 🥇 | **ADX > 20 + fixed 2R** | **1.129** | 7,259 | 36.4% | **+$28,513** | 0.083 |
| 🥈 | ADX > 25 + fixed 2R | 1.097 | 5,591 | 35.7% | +$11,481 | 0.063 |
| 🥉 | 10-bar + vol scale + 2R | 1.058 | 16,637 | 42.9% | -$584 | 0.019 |
| 4 | ADX > 30 + fixed 2R | 1.058 | 3,985 | 34.9% | +$3,804 | 0.038 |
| 5 | ADX 20-30 + fixed 2R | 1.057 | 5,351 | 34.9% | +$5,596 | 0.038 |
| 6 | 10-bar + vol scale + Ch 6x | 1.037 | 14,371 | 44.1% | -$2,510 | 0.012 |
| 7 | 10-bar + fixed 2R | 1.001 | 19,918 | 43.8% | -$503 | 0.000 |
| 8 | 20-bar + fixed 2R | 0.987 | 15,445 | 44.0% | -$2,630 | -0.004 |
| 9 | Chandelier 6.0x | 0.987 | 18,229 | 44.4% | -$2,050 | -0.003 |
| 10 | Chandelier 5.5x | 0.975 | 18,393 | 44.1% | -$2,440 | -0.007 |

## Key Findings

### 1. ADX > 20 Is the Best Single Improvement
**PF 1.129, +$28.5k — 12.8% better than baseline.** Filtering out low-ADX periods (ranging markets) removes the worst trades while keeping breakout signals in trending conditions.

### 2. 10-bar vs 20-bar: Not Clearly Better
The 10-bar baseline barely breaks even (PF 1.001) vs the earlier batch_test PF 1.204. Need to investigate the data discrepancy. The ADX filter works well on both.

### 3. Volatility Scaling Helps
PF 1.058 with 16.6k trades is +5.7% vs baseline. The constant-risk approach smooths the equity curve even if total PnL is flat.

### 4. Partial TP Loses Money
WR jumps to 45.6% but PF drops to 0.883 — winners are too small, losses still full size.

### 5. Chandelier Never Beats Fixed 2R
Consistently lower PF across all multipliers (4.0x-6.5x). The fixed 2R exit remains optimal.

## Next Steps
- Fix GC=F/GLD multi-asset validation and re-run
- Investigate the 10-bar baseline discrepancy (1.204 in batch_test vs 1.001 in run_comprehensive)
- Test ADX > 20 + volatility scaling combined
- Run walk-forward on ADX > 20 configuration
- Meta-labeler training on ADX > 20 filtered signals
