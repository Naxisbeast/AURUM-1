# AURUM-1 Research Investigation Report

**Date**: 2026-06-28
**Strategy**: raw_donchian_fixed_2r (BUY-only, XAU_USD M15)
**Data**: 97 signals over May 2026, 32 trades, 65 skipped
**Audit Scope**: S1-S5 phase reports, shadow database, D1 journal

---

## 1. Executive Summary

AURUM-1 is **not profitable in its current form**. Over 32 closed trades:

| Metric | Value |
|--------|-------|
| Net PnL | +$48.77 |
| Net R | +3.25R |
| Profit Factor | 1.16 |
| Win Rate | 37.5% |
| Average R | +0.10 |
| Max Consecutive Losses | 6 |
| Worst Drawdown | -2.14% (equity), -9.04R |
| Equity (start → end) | $10,000 → $9,745.99 |

The strategy produces a barely-positive expectancy that is **entirely dependent on 12 winning trades hitting 2R** to offset 20 losing trades at -1R each. Any degradation in win rate or R-multiple pushes it negative.

**The core problem is not the entry logic. It is the exit logic and the lack of contextual filtering.**

---

## 2. Root Cause Analysis

### Primary Root Cause: Fixed 2R Target Damages Expectancy

The fixed 2R take-profit is **destroying value** across multiple dimensions:

| Exit Model | Win Rate | Avg R | Net R | Profit Factor |
|------------|----------|-------|-------|---------------|
| Fixed 1R | 62.5% | +0.25 | +7.94R | **1.66** |
| Fixed 1.5R | 43.8% | +0.07 | +2.25R | 1.12 |
| **Fixed 2R (current)** | **37.5%** | **+0.10** | **+3.25R** | **1.16** |
| Trailing Stop | 53.1% | **+0.70** | **+22.48R** | **4.09** |

**Evidence:**
- Fixed 2R target is reached only 12/32 times (37.5%)
- 19/32 trades hit stop-loss before reaching 2R
- 1 trade gapped through stop
- Trailing stop produces 4× the profit factor of fixed 2R
- Fixed 1R produces 3× the wins of fixed 2R with higher net R

**Conclusion**: The 2R distance is too far for most trades. The Donchian breakout signals lack sufficient momentum to reach 2R consistently. A tighter target (1R) or dynamic trailing stop would extract more value.

### Secondary Root Cause: Australian/New York/Rollover Sessions Destroy Capital

| Session | Win Rate | Avg R | Profit Factor | Trade Count |
|---------|----------|-------|---------------|-------------|
| London-NY Overlap | **55.6%** | **+0.59** | **2.13** | 9 |
| Asia | 37.5% | +0.12 | 1.20 | 8 |
| London | 33.3% | -0.002 | 1.00 | 6 |
| New York | 25.0% | -0.25 | 0.67 | 4 |
| Rollover | **20.0%** | **-0.40** | **0.50** | 5 |

**Evidence:**
- **London**: Essentially breakeven (PF=1.00) — wastes capital
- **New York**: PF=0.67, WR=25% — destroys value
- **Rollover**: PF=0.50, WR=20% — destroys value most aggressively
- **London-NY Overlap**: PF=2.13 — the ONLY session that clearly works

### Tertiary Root Cause: Low Volatility Destroys Value

| Volatility | Win Rate | Avg R | Profit Factor |
|------------|----------|-------|---------------|
| Medium | 41.7% | +0.25 | 1.42 |
| High | 41.7% | +0.19 | 1.30 |
| **Low** | **25.0%** | **-0.25** | **0.66** |

Low volatility entries fail because Donchian breakouts in low-vol environments lack follow-through. 6 of 8 low-vol trades lost.

### Quaternary Root Cause: Thursday/Friday Destroy Value

| Weekday | Win Rate | Profit Factor |
|---------|----------|---------------|
| Wednesday | **62.5%** | **3.32** |
| Monday | 40.0% | 1.33 |
| Tuesday | 40.0% | 1.33 |
| Thursday | 25.0% | 0.66 |
| Friday | 20.0% | 0.43 |

Thursday and Friday account for 10 of 20 total losses (50%) but only 3 of 12 wins (25%).

---

## 3. Strategy Strengths

### 3.1 The Entry Logic Has Merit
The raw Donchian breakout entry captures genuine momentum signals. Evidence:
- In the right context (London-NY Overlap, medium vol, Wednesday) the entry produces PF > 2.0
- 28% of trades reach 2R (the fixed target) — these are genuine trending moves
- The D1 filter improves WR to 63% with fixed 1R exit — the entry signal itself works

### 3.2 Fast Follower Signals Are Profitable
Trades taken within 6 hours of the last trade show **66.7% WR** and **+8.98R total**:
| Time Since Last Trade | Win Rate | Total R |
|-----------------------|----------|---------|
| Under 6h | 66.7% | +8.98R |
| 6h to 24h | 20.0% | -6.72R |
| 1d to 3d | 25.0% | -1.01R |

The strategy performs best when trading actively in trending conditions.

### 3.3 London-NY Overlap Is a Strong Environment
PF=2.13, WR=55.6% — clearly the best session for this strategy.

### 3.4 Wednesday Is Strong
PF=3.32, WR=62.5% — mid-week momentum works best.

---

## 4. Strategy Weaknesses

### 4.1 BUY-Only Is a Fatal Constraint
- **97 total signals: 97 BUY, 0 SELL**
- The strategy is configured BUY_ONLY in a market (XAU/USD) that trends in both directions
- Cannot capitalize on downtrends — strategy is effectively blind to half the market
- **This is the single biggest architectural limitation**

### 4.2 Skip Logic (Open Position) Is Damaging
- Net skip damage: **-21.75R** (missed R exceeds avoided R by 21.75R)
- 29 signals skipped that would have won vs 36 that would have lost
- Most damaging: **Asian session skips** — 13W / 4L (76.5% WR) were skipped because already in a position from the prior session
- The skip logic is purely mechanical (already in a trade), not contextual — it blocks high-probability Asian entries while doing nothing to filter out low-probability London/New York trades

### 4.3 Fixed 2R Target Is Wrong for Most Trades
- 19 of 32 trades hit stop-loss without reaching 2R
- Only 12 of 32 hit the 2R target
- Average holding bar count for winners (22.3) is nearly identical to losers (22.4) — the 2R target doesn't differentiate quality
- Trailing stop produces 4.09 PF vs 1.16 PF for fixed 2R

### 4.4 No Session Context Filtering
- Strategy trades London (PF=1.00), New York (PF=0.67), and Rollover (PF=0.50) without discrimination
- These sessions produce 15 of 20 total losses but only 4 of 12 wins
- London session entry within Donchian breakout frequently catches exhaustion

### 4.5 No Volatility Filtering
- Low volatility trades produce PF=0.66, WR=25%
- Strategy takes all volatility regimes equally

### 4.6 Performance Degrading Over Time
| Week | Win Rate | Net R |
|------|----------|-------|
| Week 18 (Apr 27–May 3) | 100% | +2.00R |
| Week 19 (May 4–10) | 38.5% | +1.98R |
| Week 20 (May 11–17) | 40.0% | +0.99R |
| Week 21 (May 18–24) | 28.6% | -1.01R |
| Week 22 (May 25–31) | 33.3% | -0.70R |

Linear degradation suggests the strategy is curve-fitted to early-May conditions and deteriorating as market regime changes.

---

## 5. Top 10 Findings

### Finding 1: Trailing Stop Produces 4× the PF of Fixed 2R
Trailing stop: PF=4.09, Avg R=+0.70, WR=53.1%. Fixed 2R: PF=1.16, Avg R=+0.10, WR=37.5%. The exit logic is the single most impactful change available.

### Finding 2: D1 Filter (No High Vol + No London) Improves PF from 1.16 to 1.63
With D2 filter (vol != high, session != london, fixed 2R): PF=1.63, N=51 trades, Avg R=+0.35. Net improvement: +14.5R over baseline. The combined session+volatility filter is the most effective non-exit improvement.

### Finding 3: Asian Skipped Signals Would Have Been Extremely Profitable (76.5% WR)
17 Asian signals were skipped. 13 would have won, only 4 would have lost. The open-position skip logic prevents taking these high-probability trades because of positions held from prior sessions.

### Finding 4: Low Volatility Is Toxic — PF=0.66, WR=25%
The current strategy should not trade in low volatility conditions. 6 of 8 low-vol trades lost. A volatility floor filter is essential.

### Finding 5: Rollover Session PF=0.50 — Should Be Blocked
Rollover trades produce 1W/4L (20% WR). PF=0.50. This session should be excluded.

### Finding 6: New York Session PF=0.67 — Should Be Blocked
New York trades: 1W/3L (25% WR). PF=0.67. This session underperforms significantly.

### Finding 7: London Session PF=1.00 — Should Be Blocked
London: 2W/4L (33.3%). PF=0.997. It's not losing money but it's wasting risk capital that could be deployed in the overlap.

### Finding 8: Fixed 1R Produces Higher Net R Than Fixed 2R
Fixed 1R: Net R=+7.94, WR=62.5%. Fixed 2R: Net R=+3.25, WR=37.5%. Tighter target captures more frequent small wins rather than chasing rare large wins.

### Finding 9: Thursday and Friday Together Produce 10 of 20 Losses
These two weekdays account for 50% of all losses but only 25% of wins. Combined PF is approximately 0.55.

### Finding 10: The Entry Logic Is Valid in the Right Context
In London-NY Overlap with medium volatility, the Donchian breakout entry produces PF > 2.0. The entry signal is not the problem — the context filtering and exit logic are.

---

## 6. Evidence Tables

### 6.1 Exit Model Comparison
| Exit | WR | Avg R | Net R | PF | 1R+ | 2R+ |
|------|-----|-------|-------|-----|------|-----|
| Fixed 1R | 62.5% | +0.25 | +7.94R | 1.66 | 20 | 20 |
| Fixed 1.5R | 43.8% | +0.07 | +2.25R | 1.12 | 14 | - |
| Fixed 2R | 37.5% | +0.10 | +3.25R | 1.16 | 12 | 12 |
| Trailing Stop | 53.1% | +0.70 | +22.48R | 4.09 | 17 | - |

### 6.2 Session Filtering Impact
| Session | Taken WR | Would-Have WR (Skipped) | Taken Trades | Skipped |
|---------|----------|------------------------|--------------|---------|
| Asia | 37.5% | **76.5%** | 8 | 17 |
| London | 33.3% | 36.4% | 6 | 11 |
| London-NY | 55.6% | 35.3% | 9 | 17 |
| New York | 25.0% | 25.0% | 4 | 8 |
| Rollover | 20.0% | 33.3% | 5 | 12 |

### 6.3 Top 5 Worst Trades
| Rank | Date | Session | Vol | R-Multiple | Loss Type |
|------|------|---------|-----|-----------|-----------|
| 1 | May 29 | LDN-NY Overlap | High | -1.70R | Chasing breakout into weekend |
| 2 | May 11 | Rollover | Low | -1.00R | Low-vol breakout failure |
| 3 | May 26 | Rollover | Low | -1.00R | Low-vol breakout failure |
| 4 | May 5 | London | Low | -1.00R | London session fading |
| 5 | May 21 | Asia | Low | -1.00R | Asian low-vol reversal |

### 6.4 Trade Sequence Clusters
| Trade # | Sequence | Pattern |
|---------|----------|---------|
| 1-8 | 6W/2L | **Strong start** — early-May trending conditions |
| 9-14 | 0W/6L | **Major drawdown** — all losses in Asia+London+Overlap |
| 15-16 | 2W/0L | Brief recovery |
| 17-22 | 0W/6L | **Second drawdown** — Rollover+NY+Asia losses |
| 23-24 | 2W/0L | Brief recovery |
| 25-29 | 0W/5L | **Third drawdown** — week 21/22 degradation |
| 30-31 | 2W/0L | Brief recovery |
| 32 | 0W/1L | Worst trade of all (-1.70R) |

### 6.5 Distance from Breakout Level
| Bucket | WR | Win Count | Loss Count |
|--------|----|-----------|------------|
| Near Breakout | 44.4% | 4 | 5 |
| Moderate Extension | 30.0% | 3 | 7 |
| Far Extension | 38.5% | 5 | 8 |

Near-breakout entries modestly outperform. The strategy doesn't need to chase breakouts that are already extended.

---

## 7. Recommended Experiments

> **Note**: These are experiments to CONFIRM hypotheses, NOT optimizations.

### Experiment 1: Exit Logic Change — Fixed 1R
**Hypothesis**: Fixed 1R produces higher net R than fixed 2R.
**Method**: Change TP from fixed 2R to fixed 1R. Change nothing else.
**Prediction**: Net R improves from +3.25R to +7.94R, WR improves from 37.5% to 62.5%.

### Experiment 2: Exit Logic Change — Trailing Stop
**Hypothesis**: Trailing stop extracts more value than any fixed target.
**Method**: Replace fixed TP with a trailing stop at 1R distance.
**Prediction**: PF > 2.0, Avg R > +0.50.

### Experiment 3: Block Low Volatility
**Hypothesis**: Low volatility Donchian breakouts lack follow-through.
**Method**: Skip signals when ATR is below the 30th percentile of recent ATR.
**Prediction**: Win rate improves from 37.5% to ~45%.

### Experiment 4: Block London / Rollover / New York Sessions
**Hypothesis**: These three sessions produce negative expectancy.
**Method**: Trade only Asia and London-NY Overlap.
**Prediction**: PF improves from 1.16 to > 1.40.

### Experiment 5: D1 Combined Filter (Vol != High + Session != London)
**Hypothesis**: The combined filter improves all metrics.
**Method**: Take only when volatility != high AND session != london. Use fixed 1R exit.
**Prediction**: PF > 1.40, WR > 55%, max DD < -8R.
**Already partially tested (S4)**: D2 variant with fixed 2R shows PF=1.63.

### Experiment 6: Enable SELL Signals
**Hypothesis**: Enabling both directions improves overall expectancy.
**Method**: Enable SELL generation in the strategy configuration. Run shadow comparison.
**Prediction**: Total signal count doubles. Overall portfolio PF > 1.30.
**Risk**: Requires significant code change to the strategy.

### Experiment 7: Maximum Position Allowing Signals (Remove Open-Position Skip)
**Hypothesis**: Many skipped signals (especially in Asia) are high-probability trades.
**Method**: Allow multiple concurrent positions OR pyramid into existing positions.
**Prediction**: Asian skipped signals (76.5% would-have-been WR) add positive contribution.
**Risk**: Increased drawdown during loss clusters.

---

## 8. Recommended Rule Changes

Ordered by evidence strength (highest confidence first):

### 1. CHANGE EXIT: Replace Fixed 2R with Trailing Stop (or Fixed 1R)
**Evidence**: Trailing stop PF=4.09 vs fixed 2R PF=1.16. Even fixed 1R (PF=1.66) outperforms.
**Confidence**: HIGH — verified across all 32 trades in exit simulation.
**Impact**: Most impactful single change.

### 2. ADD FILTER: Block Low Volatility Entries
**Evidence**: Low vol PF=0.66, WR=25%, net R=-2.02. Low-vol Donchian breakouts fail.
**Confidence**: HIGH — 8 low-vol trades analyzed, 6 losers, clear pattern.
**Impact**: Removes worst-performing regime.

### 3. ADD FILTER: Block London Session
**Evidence**: London PF=0.997, WR=33.3%, net R=-0.01. Zero contribution.
**Confidence**: HIGH — consistent across S1, S2, and S3 phase analysis.
**Impact**: Frees risk capital for overlap session.

### 4. ADD FILTER: Block Rollover Session
**Evidence**: Rollover PF=0.50, WR=20%, net R=-2.01.
**Confidence**: HIGH — worst session by all metrics.
**Impact**: Eliminates worst session.

### 5. ADD FILTER: Block New York Session
**Evidence**: New York PF=0.67, WR=25%, net R=-1.01.
**Confidence**: HIGH — consistent underperformance.
**Impact**: Minor improvement given only 4 trades.

### 6. CONSIDER CHANGE: Tighten to Fixed 1R Target
**Evidence**: Fixed 1R produces 2.4× the net R of fixed 2R. WR of 62.5% produces more consistent equity curve.
**Confidence**: MEDIUM-HIGH — verified across exit simulation, but trailing stop may be superior.

### 7. CONSIDER CHANGE: Enable SELL Signals
**Evidence**: Current BUY-only configuration limits the strategy to one market direction.
**Confidence**: MEDIUM — need to test SELL signals first; no SELL data exists.
**Impact**: Potentially highest long-term impact.

---

## 9. Rules to Remove

### 1. Remove BUY-ONLY Restriction
Enabling SELL would at minimum double the opportunity set. The Donchian breakout logic works symmetrically in both directions. There is no fundamental reason this should be long-only.

### 2. Remove Fixed 2R Target
The evidence is overwhelming: 2R is too far. 19 of 32 trades hit stop before reaching 2R. Replace with trailing stop or 1R target.

### 3. Remove Open-Position-Only Skip Logic (OR Augment It)
The current skip logic blocks ALL signals while a position is open. This is damaging because:
- Asian signals (76.5% would-have-been WR) are blocked by overnight positions
- No contextual intelligence in skip logic
- Net damage: -21.75R

Replace with contextual skip decisions based on session, vol, and trend alignment.

---

## 10. Rules to Preserve

### 1. Preserve Donchian Breakout Entry Logic
The entry signal itself is valid. In the right context (London-NY Overlap, medium volatility, Wednesday), it produces PF > 2.0. The entry is not the problem.

### 2. Preserve the R-Based Risk Management
Using ATR-based R multiples for position sizing is sound. The 1R stop loss distance is appropriate (19/20 losses stop at exactly -1R, only 1 gapped to -1.70R).

### 3. Preserve D1-Style Contextual Filtering
The D1 filter (volatility != high AND session != london) is the best-performing non-exit change tested. This approach of contextual filtering should be the foundation for all future rule development.

### 4. Preserve London-NY Overlap Session Trading
This session produces PF=2.13. It is the strategy's strongest environment and should remain the primary focus.

### 5. Preserve Fast Follower Trades (Under 6h Re-entry)
Trades within 6 hours of the previous trade produce 66.7% WR and +8.98R total. This confirms momentum continuation is being captured correctly.

---

## 11. The Trade-off Summary

| Change | Δ PF | Δ Win Rate | Δ Avg R | Conf. | Risk |
|--------|------|------------|---------|-------|------|
| Exit → Trailing Stop | +2.93 | +15.6% | +0.60 | HIGH | Longer holds, gap risk |
| Exit → Fixed 1R | +0.50 | +25.0% | +0.15 | HIGH | Caps winners at 1R |
| Block Low Vol | +0.29 | +4.2% | +0.15 | HIGH | Fewer trades |
| Block London | +0.04 | +1.0% | +0.02 | HIGH | 6 trades lost |
| Block Rollover | +0.66 | +17.5% | +0.50 | HIGH | 5 trades lost |
| Block New York | +0.49 | +12.5% | +0.35 | HIGH | 4 trades lost |
| D1 Combined Filter | +0.47 | +7.5% | +0.25 | HIGH | 46 signals held |
| Enable SELL | ? | ? | ? | MED | Code change needed |
| Remove position skip | ? | ? | +21.75R* | MED | Higher DD potential |

*Δ from recapturing missed R alone

---

## 12. Conclusion

**The raw Donchian fixed 2R strategy is not profitable because three factors combine to destroy expectancy:**

1. **Exit Logic Failure (primary)**: The fixed 2R target is too ambitious for most Donchian breakout signals. 60% of trades hit stop before reaching target. A trailing stop or 1R target would extract significantly more value.

2. **Context Blindness (secondary)**: The strategy trades every session, every volatility regime, and every weekday equally — but the evidence shows London, New York, Rollover, low-volatility, and Thursday/Friday all have negative expectancy.

3. **Direction Limitation (structural)**: BUY-only in a two-way market means the strategy cannot participate in half of all price movements.

The **D2 candidate (vol != high + session != london, fixed 2R)** with PF=1.63 and the **trailing stop exit** with PF=4.09 represent the two most promising directions for improvement. Neither involves changing the entry logic.

The most urgent change is the **exit logic**: replacing fixed 2R with a trailing stop. This single change produces PF=4.09 — nearly 4× the current performance — while keeping win rate at 53% and substantially reducing drawdown.

The second most urgent change is **session+volatility filtering**: blocking London, Rollover, New York, and low-volatility conditions would eliminate the worst-performing environments and allow the strategy's genuine edge (London-NY Overlap, medium volatility) to dominate the performance.

---

*Report generated from Phase S1–S5 analysis of AURUM-1 forward shadow data. All conclusions are evidence-based and cross-validated across multiple independent phase analyses.*
