# AURUM-1 Audit Improvement Roadmap

**Date**: 2026-07-21
**Status**: Planning phase

## Philosophy

Not every audit requirement is worth doing. The goal is improvements that:

1. **Prevent real losses** (kill switches, price collars)
2. **Reveal something about the system** (capacity modeling, stationarity)
3. **Protect future us** (documentation, deterministic execution)

Institutional overhead (SOC 2, nanosecond logging, dual-language engines) is skipped until it matters.

---

## Priority Matrix

```
Effort →        Low (<1 day)      Medium (2-3 days)     High (week+)
Value ↓
─────────────────────────────────────────────────────────────────────
HIGH            P1: Kill switch    P2: Capacity model     ———
                P1: Price collars  P2: Stationarity
                P1: Feature audit

MEDIUM          P3: Determinism     P3: Model docs        ———
                audit

LOW             ———                 ———                   Skipped
```

---

## P1 — Immediate (Week 1, ~2 days total)

### 1. Independent Kill Switch Watchdog
**What**: A separate process (separate systemd service) that monitors the D4's health file and kills the D4 if thresholds are breached. Cannot be overwritten by the trading algorithm.

**Why**: Current kill switches run *inside* the D4 process. If the D4 crashes or enters a bad state, the kill switches crash with it. A watchdog running on a 5-second poll is proper redundancy.

**Implementation**:
- `monitor/watchdog.py` — reads `d4_paper_trader_health.json` every 5s
- Hardcoded thresholds (cannot be changed by settings.yaml):
  - Max DD: 15% (above the 8% soft limit, catches runaway cases)
  - Max daily loss: 10% (above the 3% soft limit)
  - Max equity drop in 1h: 5% (new — catches rapid crashes)
  - Stale data > 6h: force restart (new — catches data pipeline failures)
- On breach: `systemctl stop aurum1-d4-paper.service`, log to syslog
- Separate systemd service: `aurum1-d4-watchdog.service`

**Risk**: None — this is a read-only monitor. It can only stop the D4, not start it.

**Value**: ⭐⭐⭐⭐⭐ — prevents the D4 from running in a bad state

### 2. Price Collar Checks
**What**: Reject orders where the entry price is more than X% away from the current market price.

**Why**: If a data pipeline feeds corrupted prices (e.g., decimal shift: $4,000 → $40,000), the D4 would enter at a ridiculous price. A 5% collar catches this.

**Implementation**:
- Add to `PaperBroker.submit_order()`: compare `instruction.entry_price` vs `current_price` from dequeue
- If deviation > 5%: reject with `price_collar_violation`
- Configurable via settings.yaml with a hard override in the code (so it can't be completely disabled)

**Risk**: None — this is a safety net that triggers only on extreme deviations.

**Value**: ⭐⭐⭐⭐ — cheap insurance against data corruption

### 3. Feature Stationarity Audit
**What**: Run ADF tests on the Donchian breakout signal to confirm it's not trading on non-stationary noise.

**Why**: We have ICIR analysis showing the signal has predictive power, but we've never formally tested whether we're trading a stationary process or a random walk with drift.

**Implementation**:
- `scripts/audit/stationarity.py` — ADF test on close prices, ATR, signal binary, and returns
- Documents results in the audit report
- If signal is non-stationary → investigate, but our ICIR already suggests it's meaningful

**Risk**: None — pure analysis, no code changes.

**Value**: ⭐⭐⭐ — tells us if our edge is real or statistical illusion

---

## P2 — Short Term (Week 2, ~3 days total)

### 4. Capacity & Decay Modeling
**What**: Model how much capital D4 can handle before market impact erodes the edge.

**Why**: D4 trades ~1.8 times/day on XAU/USD. At some account size, the 1-unit positions become 100-unit positions and slippage grows beyond 0.5 pips. Knowing this ceiling prevents us from scaling into a problem.

**Implementation**:
- `scripts/audit/capacity.py` — simulates the 11-year backtest at increasing position sizes
- For each size: compute slippage as a function of position size vs average daily volume
- Reports: max size before PF drops below 1.05, max size before Sharpe drops below 0.5
- XAU/USD daily volume is ~$30B+, so the ceiling is likely very high — but we should confirm

**Value**: ⭐⭐⭐⭐ — critical for the "should we risk more?" conversation at 50 and 100 trade gates

### 5. Signal Decay & Feature Stability
**What**: Expand the ICIR analysis to test whether the D4 signal has degraded over time (forward decay vs backtest decay).

**Why**: The ICIR analysis showed signal decay characteristics in the 11-year backtest. We should verify the live 29-trade sample matches the backtest decay profile, or if the live signal is degrading faster.

**Implementation**:
- `scripts/audit/decay_monitor.py` — compares rolling ICIR from live trades vs backtest baseline
- Alerts if live ICIR diverges significantly from expected

**Value**: ⭐⭐⭐ — early warning if the market regime has shifted against D4

---

## P3 — Medium Term (Week 3-4, ~3 days total)

### 6. Deterministic Execution Audit
**What**: Verify every source of randomness is seeded and deterministic.

**Why**: The RNG is seeded (42) in the paper broker, but there may be unseeded randomness in other paths (data loading, feature computation, pandas operations).

**Implementation**:
- `scripts/audit/determinism.py` — runs the full D4 pipeline twice with same inputs, compares outputs
- Any divergence = unseeded randomness bug
- Fix: add `np.random.seed()`, `random.seed()`, and `python -c` determinism checks

**Value**: ⭐⭐⭐ — catches subtle bugs that cause non-reproducible results

### 7. Model Documentation (SR 26-2 Lite)
**What**: One document that formally describes D4 as a "model" per SR 26-2 standards.

**Why**: Not because an auditor is coming, but because writing it forces us to think about every assumption, parameter, and failure mode.

**Contents**:
- Model purpose and scope
- Input data sources and their limitations
- Methodology (mathematical description of Donchian breakout)
- Parameter sensitivity (what if lookback is 15 vs 20 vs 25?)
- Performance under stress regimes (COVID, 2022 rate hikes, etc.)
- Known limitations and failure modes
- Change control procedure

**Value**: ⭐⭐⭐ — forces clarity about what D4 actually is and isn't

---

## Skipped (Institutional Overhead)

| Requirement | Why Skipped |
|-------------|-------------|
| Dual-model cross-verification (C++/Rust) | Single codebase, 29 paper trades. When we have $1M live, revisit. |
| Nanosecond timestamps | M15 trading doesn't need them. `datetime.now(UTC)` is sufficient. |
| SOC 2 / ISO 27001 | Paper trading system operated by one person. Overkill. |
| Formal data provider audit | XAU/USD from OANDA is a single liquid instrument. No survivorship bias risk. |
| MiFID II trade reconstruction | Not a regulated entity. If that changes, revisit. |

---

## Implementation Order (Recommended)

```
Week 1:
  ├── Day 1: Watchdog kill switch (monitor/watchdog.py + systemd service)
  ├── Day 2: Price collar checks + stationarity audit
  └── Day 3: Deploy watchdog to server, validate both

Week 2:
  ├── Day 1: Capacity modeling 
  ├── Day 2: Signal decay monitoring  
  └── Day 3: Deploy + validate

Week 3-4:
  ├── Determinism audit
  └── Model documentation (ongoing, can be written in parallel)
```

Total: ~8 days of work spread across 3-4 weeks, with the D4 running untouched.
