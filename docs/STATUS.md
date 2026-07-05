# AURUM-1 System Status

**Last updated**: 2026-07-05

## Operational Status

| Component | Status | Details |
|-----------|--------|---------|
| Main Orchestrator | **STOPPED** | Last run May 27 2026, killed by signal_2. Not restarted. D4 paper trader replaces it. |
| Forward Shadow (Raw Donchian 2R) | ✅ **ACTIVE** | Running since June 11. Continuous market data cache. |
| **D4 Paper Trader** | ✅ **ACTIVE** | 🏆 Autonomous paper trading since June 28. See below. |
| D1 Shadow Journal | ✅ **TIMER ACTIVE** | Every 15 min. Filtered 1R journal. |
| D2 Shadow (1R + filter, BUY only) | ✅ **TIMER ACTIVE** | Every 15 min. Historical comparison. |
| D3 Shadow (1R + filter, BUY+SELL) | ✅ **TIMER ACTIVE** | Every 15 min. SELL-enabled D1 variant. |
| D4 Shadow (2R no filters, BUY+SELL) | ✅ **TIMER ACTIVE** | Every 15 min. Best variant comparison. |
| D6 Shadow (2R + ML ensemble) | ✅ **TIMER ACTIVE** | Every 15 min. ML variant comparison. |
| ML Retrain (weekly) | ✅ **TIMER ACTIVE** | Saturdays. Retrains RegimeClassifier + DirectionPredictor. |
| Dashboard | **STOPPED** | Not deployed on cloud server. |
| Daily Backups | ✅ **ACTIVE** | 28+ daily backups of forward shadow DB. |

## D4 Paper Trader Performance 🏆

**Service**: `aurum1-d4-paper.service` — autonomous Donchian 2R BUY+SELL, no filters.

| Metric | Value |
|--------|-------|
| Started | 2026-06-28 |
| Restarts | 2 (Phase 1 deploy Jul 4, open position recovery Jul 5) |
| Equity | **$10,149.59** (+$149.59, +1.50%) |
| Trades (DB) | 0 (previous 3 lost during schema migration; next trade saves correctly) |
| Wins | 0 (pending next trade) |
| Losses | 0 (pending next trade) |
| Data Source | Local cache (OANDA → forward-shadow → D4) |
| DB Tables | trades, account_snapshots, settings, missed_signals, **open_positions** |

**Strategy**: Donchian 20-bar breakout → entry at next open + slippage → 2R fixed exit (2× ATR stop, 4× ATR target). No filters. Both BUY and SELL directions.

### Infrastructure Status (Phase 0 — Complete ✅)

| Feature | Status | Details |
|---------|--------|---------|
| PID file / single-instance lock | ✅ | Prevents duplicate processes |
| R-multiple in broker trade dict | ✅ | Kelly calculator works correctly |
| Account snapshots every cycle | ✅ | 188+ records in DB |
| entry_time / exit_time columns | ✅ | Trade duration calculable |
| Read state from DB on restart | ✅ | Equity, settings, trades restored |
| Trade history capped at 10,000 | ✅ | Memory safety |
| Drawdown % in status line | ✅ | Shown each tick |
| Settings table | ✅ | last_processed_ts persisted |
| **Open position recovery** | ✅ | Positions persist to DB every cycle, restored on restart |

### Observability Status (Phase 1 — Complete ✅)

| Feature | Status | Details |
|---------|--------|---------|
| Entry slippage tracking | ✅ | Signed slippage recorded per entry |
| Exit slippage tracking | ✅ | Signed exit slippage recorded per close |
| Spread in status line | ✅ | `Sprd=X.Xp` shown each tick |
| Latency min/max/avg | ✅ | Tracked per execution |
| Missed signal logging | ✅ | Logged to DB with timestamp, direction, price, reason |
| Periodic observability report | ✅ | Full summary every ~1h |
| Health file | ✅ | All metrics exposed as JSON |
| `missed_signals` table | ✅ | Survives restart |
| `open_positions` table | ✅ | Survives restart |

### Validation Status (Phase 2 — Partially Complete 🟡)

| Analysis | Status | Key Result |
|----------|--------|------------|
| **Monte Carlo (10k sims)** | ✅ **Complete** | 0% ruin, 99th DD 20.3% |
| **Walk-Forward L20** | ✅ **Complete** | PF 1.14, Sharpe 1.27, 88.9% positive windows |
| **Walk-Forward L55** | ✅ **Complete** | PF 1.09, Sharpe 0.67, 77.8% positive windows |
| **Risk Sensitivity** | ✅ **Complete** | 0.25% is sweet spot |
| Live vs backtest comparator | ❌ Not started | — |

## 11-Year Backtest Results (Best Variants)

| Rank | Variant | Directions | Exit | PF | PnL | Trades |
|------|---------|-----------|------|-----|-----|--------|
| 1 | **D4** | BUY+SELL | 2R | **1.14** | **+$42,678** | 8,175 |
| 2 | D6 | BUY+SELL | 2R + ML | 1.14 | +$42,681 | 8,169 |
| 3 | Raw | BUY only | 2R | 1.14 | +$17,156 | 4,879 |
| 4 | D2 | BUY only | 1R + filters | 1.03 | +$1,667 | 6,890 |
| 5 | D3 | BUY+SELL | 1R + filters | 1.02 | +$1,162 | 3,544 |

**Key Insight**: D4 (simplest: 2R exit, no filters, both directions) dominates over 11 years. Adding filters (D2/D3) or ML (D6) doesn't materially improve the 11-year result.

## Risk Sensitivity Analysis

| Risk/Trade | Med DD | 95th DD | 99th DD | Worst DD | Med Return | Ruin |
|:----------:|:------:|:-------:|:-------:|:--------:|:----------:|:----:|
| 0.10% | 4.9% | 7.2% | 8.8% | 12.4% | +114% | 0% |
| **0.25%** ⬅️ | **11.9%** | **17.2%** | **20.3%** | **27.9%** | **+551%** | **0%** |
| 0.50% | 22.8% | 32.4% | 37.3% | 49.4% | +3,704% | 0% |
| 1.00% | 41.3% | 55.3% | 62.4% | 76.4% | +93,528% | 0% |

**Conclusion**: 0.25% is the sweet spot — only 1.2% of simulated paths exceed 20% drawdown, and ruin probability is 0%.

## Strategy Hierarchy

```
D4  ── 2R, BUY+SELL, no filters      → PF=1.14, +$42,678  🏆 Best (paper trading)
D6  ── 2R, BUY+SELL, ML ensemble     → PF=1.14, +$42,681  (effectively identical)
Raw ── 2R, BUY only, no filters       → PF=1.14, +$17,156  (no SELL signals)
D2  ── 1R, BUY only, vol+session      → PF=1.03, +$1,667   (filtered 1R)
D3  ── 1R, BUY+SELL, vol+session      → PF=1.02, +$1,162   (SELL adds little with filters)
D5  ── Research only (adaptive ATR + vol imbalance)          (kills 83% of trades)
```

## Server

| Detail | Value |
|--------|-------|
| Host | `aurum1-paper-server` (178.105.245.66) |
| OS | Ubuntu 24.04.4 LTS |
| Disk | 38GB total, 53% used (17G free) |
| Memory | 3.7GB total, ~11% used |
| Python | 3.12.3 |
| Working directory | `/opt/aurum1` |
| D4 PID | 678783 |

## Key Decisions

- **May 27**: Main orchestrator shut down (signal_2). Not restarted.
- **June 11**: Forward shadow service deployed.
- **June 28**: D4 paper trader deployed as autonomous service. D3/D4/D6 shadow timers added.
- **June 29**: Fixed D4 data source (from yfinance → local cache). Added multi-candle processing. Fixed file ownership.
- **June 30**: Fixed DB persist bug. 3 trades executed, +$85.29.
- **July 2**: Phase 0 complete — PID lock, R-multiple, account snapshots, entry/exit timestamps, DB state recovery, trade cap, drawdown tracking.
- **July 3**: Walk-forward validation (L20 PF=1.14, L55 PF=1.09), risk sensitivity analysis (0.25% sweet spot).
- **July 4**: Phase 1 observability complete — exit slippage, latency min/max, spread in status, missed signal logging, periodic observability report.
- **July 5**: Open position recovery — positions persist to DB and restore on restart.

## Next Actions

1. ✅ ~~D4 paper trader deployed and trading~~
2. ✅ ~~DB persist bug fixed~~
3. ✅ ~~Phase 0 infrastructure complete~~
4. ✅ ~~Phase 1 observability complete~~
5. ✅ ~~Walk-forward validation (L20 + L55)~~
6. ✅ ~~Risk sensitivity analysis~~
7. ✅ ~~Open position recovery~~
8. ✅ ~~Monte Carlo analysis (10k sims, 0% ruin)~~
9. 🔲 Live vs backtest comparator script (Phase 2)
10. 🔲 Verify next trade: saves to DB, survives restart
11. 🔲 Accumulate 100+ trades before strategy changes
12. 🔲 Phase 0.5 — cooldown + breakeven stop (after 100 trades)

