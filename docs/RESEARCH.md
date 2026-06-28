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

---

## Key Research Findings Summary

| Finding | Evidence | Confidence |
|---------|----------|------------|
| Fixed 2R exit is suboptimal | S1 exit comparison (all 32 trades) | HIGH |
| Fixed 1R outperforms fixed 2R | S1 exit comparison (PF=1.66 vs 1.16) | HIGH |
| Trailing stop simulated PF=4.09 | S1 exit simulation | MEDIUM (unverified live) |
| London session wastes capital | S1-S3 independent analyses | HIGH |
| Low volatility entry is damaging | S1 failure breakdown (PF=0.66) | MEDIUM (small sample) |
| D1 filter improves PF to 1.63 | S3 replay + S4 lock | MODERATE-HIGH |
| D2 (1R + filter) PF=1.33 | 543-trade simulation | HIGH (large sample) |
| Open-position skip logic damages | S1 skip analysis (-21.75R net) | HIGH |
| BUY-only limits opportunity | Confirmed across all phases | MODERATE (unproven) |

## Data Sources

- **Market data**: OANDA API → local SQLite cache
- **Shadow trades**: Forward shadow ledger (`donchian_shadow.sqlite3`)
- **Candles for simulation**: Shadow market cache (`forward_shadow_market_cache.sqlite3`)
- **Backtest data**: Backtest market cache (`backtest_market_cache.sqlite3`)
