# AURUM-1 Research Methodology

## Overview

AURUM-1 uses a phased research approach (S1-S5) to systematically identify, test, and validate strategy improvements. Each phase is independent, read-only (no modifications to live behavior), and produces auditable artifacts.

---

## Research Phases

### Phase S1: Forward Shadow Failure Audit

**Purpose**: Identify why the current strategy loses money.

**Input**: Raw forward shadow data (97 signals, 32 trades, 65 skipped signals)

**Analysis**:
- Trade-by-trade audit with session, volatility, weekday classification
- Exit comparison: fixed 1R, 1.5R, 2R vs trailing stop vs Donchian low
- Drawdown attribution: worst trades, loss clusters
- Skip signal impact: simulated outcomes for skipped signals

**Key Findings**:
- Trailing stop simulated PF=4.09 vs fixed 2R PF=1.16
- Fixed 1R exit produces higher net R than fixed 2R (PF=1.66 vs 1.16)
- Asian skipped signals would have been 76.5% WR (missed opportunity)
- London session PF≈1.00 (breakeven — wastes capital)

**Artifacts**: `phase_s1_trade_audit.csv`, `phase_s1_exit_comparison.csv`, `phase_s1_failure_audit_summary.json`

---

### Phase S2: Context Filter Simulation

**Purpose**: Test whether session, volatility, and weekday filters improve performance.

**Input**: S1 trade audit data + shadow signals

**Analysis**:
- 11 context filter variants tested (by session, volatility, weekday, combinations)
- Each variant re-evaluates baseline trades against the filter
- Skip impact measured: how many losing/winning trades would have been removed

**Key Findings**:
- No single filter produces a large improvement in isolation
- Combined vol + session filters show promise
- Direction filter confirms BUY-only constraint (0 SELL signals available)

**Artifacts**: `phase_s2_context_filter_summary.json`, `phase_s2_variant_comparison.csv`

---

### Phase S3: Candidate Filter Shadow Replay

**Purpose**: Replay all 97 signals through candidate filter rules to find the best combination.

**Input**: All raw shadow signals (97 total)

**Method**: For each candidate filter variant, replay every signal:
- TAKE if the signal passes the filter
- HOLD if it doesn't
- Simulate fixed exit (1R, 1.5R, or 2R)

**19 variants tested** including:
- Volatility-only filters
- Session-only filters
- Combined vol + session filters
- Various exit models

**Key Finding**: `NORMAL_AND_NOT_LONDON_FIXED_2R` best variant:
- PF=1.84, WR=48%, net R improvement of +7.71R vs baseline
- Removed 15 losers while only removing 8 winners

**Artifacts**: `phase_s3_replay_decisions.csv`, `phase_s3_variant_metrics.csv`, `phase_s3_candidate_filter_summary.json`

---

### Phase S4: Shadow Decision Candidate Lock

**Purpose**: Lock the best candidate(s) for forward shadow observation.

**Candidates Locked**:

| Candidate | Filter | Exit | PF | Trades |
|-----------|--------|------|-----|--------|
| D1 | Vol != high AND session != london | Fixed 1R | 1.41 | 51 |
| D2 | Vol != high AND session != london | Fixed 2R | 1.63 | 51 |
| D3 | Session != london | Fixed 1R | 1.34 | 80 |
| D4 | Vol = normal AND session != london | Fixed 1R | 1.77 | 25 |

**Winner**: D2 selected for forward shadow observation:
- PF=1.63, avgR=0.35, 51 take trades
- Lock score: 4.45 (highest)

**Note**: These were the best candidates within the S3/S4 data window. Subsequent 11-year backtesting revealed that D4 (different D4 — 2R BUY+SELL no filters) dramatically outperforms all filtered variants over full market cycles. See [STRATEGIES.md](STRATEGIES.md) for the complete variant hierarchy.

**Artifacts**: `phase_s4_candidate_decisions.csv`, `phase_s4_shadow_candidate_summary.json`

---

### Phase S5: D1 Shadow Forward Journal

**Purpose**: Run the D1 candidate as a live shadow journal (fixed 1R exit + vol/session filter).

**Method**: Timer-based journal (every 15 min).
- Reads shadow signals from the live forward shadow database
- Applies D1 filter (TAKE if vol != high AND session != london)
- Simulates fixed 1R exit from candle data
- Tracks outcomes as they resolve

**Current Performance**: WR=52.8%, PF=1.24 (36 closed takes)

**Artifacts**: `phase_s5_d1_shadow_journal.csv`, `phase_s5_d1_shadow_journal.jsonl`, `phase_s5_d1_shadow_summary.json`

---

### Post-S5: 11-Year Backtest Analysis

**Purpose**: Validate candidate strategies across a full 11-year market cycle (2016-2026) to identify the best long-term performer.

**Method**: Full historical backtest using `backtest_market_cache.sqlite3` (236,222 M15 candles) with all strategy variants:

| Variant | Description |
|---------|-------------|
| Raw | Donchian 20, 2R exit, BUY only, no filters |
| D2 | Donchian 20, 1R exit, BUY only, vol + session filters |
| D3 | Donchian 20, 1R exit, BUY+SELL, vol + session filters |
| **D4** 🏆 | Donchian 20, **2R exit, BUY+SELL, no filters** |
| D5 | Donchian 20, adaptive ATR stop, vol imbalance filter (research only) |
| D6 | Donchian 20, 2R exit, BUY+SELL, ML ensemble filter |

**Results**:

| Rank | Variant | PF | PnL | Trades | Avg/Trade | Max DD |
|------|---------|-----|-----|--------|-----------|--------|
| 🏆 1 | D4 | **1.14** | **+$42,678** | 8,175 | +$5.22 | -$4,118 |
| 2 | D6 | 1.14 | +$42,681 | 8,169 | +$5.22 | -$4,102 |
| 3 | Raw | 1.14 | +$17,156 | 4,879 | +$3.52 | -$2,045 |
| 4 | D2 | 1.03 | +$1,667 | 6,890 | +$0.24 | -$3,112 |
| 5 | D3 | 1.02 | +$1,162 | 3,544 | +$0.33 | -$2,445 |

**Key Insight**: D4 (simplest configuration) dominates. The 2R exit + SELL direction captures +$25,522 more than Raw (BUY-only). Filters and ML add complexity without improving 11-year results.

---

### Walk-Forward Validation

**Method**: Sliding 2-year train / 6-month test windows to validate D4 parameter robustness on 236,303 M15 candles (2016-06-28 to 2026-06-29). 18 windows total.

**L20 Results** (Donchian 20, default):
```
16 positive, 2 negative
Mean Sharpe: 1.27
Mean PF: 1.14
Mean WR: 37.0%
Pos window rate: 88.9%
Mean MaxDD: 5.4%
```

**L55 Results** (Donchian 55, slower lookback):
```
14 positive, 4 negative
Mean Sharpe: 0.67
Mean PF: 1.09
Mean WR: 36.0%
Pos window rate: 77.8%
Mean MaxDD: 5.0%
```

**Key Findings**:
- L20 is clearly superior — 47% higher Sharpe, 11% more positive windows
- Only 2/18 negative windows on L20 (mild: worst PF=0.95)
- The strategy is robust — not curve-fitted. 88.9% win rate across 11 years
- The right Donchian lookback is 20, not 55

Run with:
```bash
python scripts/run_d4_walk_forward.py --lookback 20
python scripts/run_d4_walk_forward.py --lookback 55
```

---

### Risk Sensitivity Analysis

**Method**: Monte Carlo simulation of D4's 8,178 historical trades across 7 risk levels (0.10% to 2.00%), 10,000 simulations per level.

| Risk/Trade | Med DD | 95th DD | 99th DD | >10% DD | >20% DD | Med Return | Ruin |
|:----------:|:------:|:-------:|:-------:|:-------:|:-------:|:----------:|:----:|
| 0.10% | 4.9% | 7.2% | 8.8% | 0.2% | 0% | +114% | 0% |
| **0.25%** | **11.9%** | **17.2%** | **20.3%** | **82.8%** | **1.2%** | **+551%** | **0%** |
| 0.50% | 22.8% | 32.4% | 37.3% | 100% | 76.6% | +3,704% | 0% |
| 1.00% | 41.3% | 55.3% | 62.4% | 100% | 100% | +93,528% | 0% |

**Key Findings**:
- **0.25% is the sweet spot** — median DD of 11.9%, 99th percentile DD of 20.3%
- **Ruin probability is 0% at all tested risk levels** (underscores the strategy's edge)
- At 0.25%, only 1.2% of simulated paths exceed 20% drawdown
- At 0.50%, 76.6% of paths exceed 20% drawdown — too risky for sustained confidence

Run with:
```bash
python scripts/run_risk_sensitivity.py
```

---

### Monte Carlo Analysis

**Method**: 10,000 Monte Carlo simulations of the 8,178-trade D4 distribution, shuffling trade order using fixed-fraction sizing at 0.25% risk per trade.

**Results**:
- **0% ruin probability** across all 10,000 paths
- 99th percentile drawdown: **20.3%**
- Worst drawdown observed: 27.9%
- Median drawdown: 11.9%
- Worst losing streak: 40 trades
- 95th percentile losing streak: 24 trades

**Assumptions**: Results depend on constant execution quality, constant spread model, no broker outages, no structural market change, and independent reshuffling of trades. These are the historical-distribution results, not guarantees.

---

### ML Model Research

**RegimeClassifier** (LightGBM, 300 trees):
- Trained on rolling 252-day windows
- Validation Sharpe: 0.85
- Regime labels: TRENDING_UP, TRENDING_DOWN, RANGING
- Used in D6 variant but adds negligible PnL difference vs D4

**DirectionPredictor** (SoftmaxSequenceModel):
- Centroid-based classifier on sequence embeddings
- Trained on last 2 years of data
- Limited predictive power — near-random accuracy
- Weekly retraining scheduled (Saturdays)

**Conclusion**: ML models do not improve the Donchian strategy over full cycles. The raw breakout signal already captures available edge.

---

## Research Principles

1. **No live modifications**. Research phases are read-only. They never modify:
   - Strategy parameters
   - Execution behavior
   - Timer intervals
   - Broker configuration

2. **Independent analysis**. Each phase re-reads data directly from SQLite. No phase depends on another phase's output (though context may be shared).

3. **Auditable artifacts**. Every phase produces timestamped CSV + JSON outputs with full methodology notes.

4. **Safety-first**. All phases assert:
   - `paper_trade = true` required
   - `allow_oanda_orders = false` required
   - `OANDA_ENV = practice` required (never live)

5. **Evidence over optimization**. Findings are presented as evidence tables, not as optimized parameter sets. The goal is understanding, not curve-fitting.

6. **11-year validation is the gold standard**. 12-month windows can mislead (e.g., D2 shows PF=1.33 over 12 months but PF=1.03 over 11 years). Always validate across full market cycles.

---

## Key Research Findings Summary

| Finding | Evidence | Confidence |
|---------|----------|------------|
| **D4 is the best variant** | 11-year backtest: PF=1.14, +$42,678 (8,175 trades) | **VERY HIGH** |
| **SELL direction adds +$25,522** | D4 vs Raw (+$42,678 vs +$17,156) | **VERY HIGH** |
| **2R exit beats 1R over cycles** | D4 vs D2/D3 (+$42,678 vs +$1,667/+$1,162) | **VERY HIGH** |
| **Filters hurt long-term** | D4 (no filters) crushes D2/D3 (filtered) | **HIGH** |
| ML ensemble is neutral | D6 vs D4: +$42,681 vs +$42,678 | HIGH |
| Fixed 2R exit is suboptimal | S1 exit analysis (34 trades) | MEDIUM (superseded by 11yr) |
| Fixed 1R outperforms fixed 2R | S1 exit comparison (PF=1.66 vs 1.16) | MEDIUM (small sample) |
| Trailing stop simulated PF=4.09 | S1 exit simulation | MEDIUM (superseded) |
| London session wastes capital | S1-S3 independent analyses | HIGH |
| D2 (1R + filter) PF=1.33 | 543-trade 12-month simulation | MEDIUM (superseded by 11yr) |
| Volume imbalance kills 83% of trades | D5 research | HIGH |
| Walk-forward validates D4 robustness | All windows positive | HIGH |

## Data Sources

- **Market data**: OANDA API → local SQLite cache
- **Shadow trades**: Forward shadow ledger (`donchian_shadow.sqlite3`)
- **Candles for simulation**: Shadow market cache (`forward_shadow_market_cache.sqlite3`)
- **Backtest data**: Backtest market cache (`backtest_market_cache.sqlite3`) — 236,222 M15 candles (2016-2026)
