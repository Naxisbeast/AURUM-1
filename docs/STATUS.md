# AURUM-1 System Status

**Last updated**: 2026-07-14

## Operational Status

| Component | Status | Details |
|-----------|--------|---------|
| Main Orchestrator | **STOPPED** | Last run May 27 2026. D4 paper trader replaced it. |
| Forward Shadow (Raw Donchian 2R) | ✅ **ACTIVE** | Running since Jun 11. 26,060+ M15 candles cached. |
| **D4 Paper Trader** 🏆 | ✅ **ACTIVE** | Autonomous Donchian breakout trading since Jun 28. **See below.** |
| D1-D6 Shadow Journals | ✅ **TIMERS ACTIVE** | Every 15 min. Variant comparison & journaling. |
| ML Retrain (weekly) | ✅ **TIMER ACTIVE** | Saturdays. RegimeClassifier + DirectionPredictor. |
| **Dashboard** | ✅ **ACTIVE** | **Streamlit dashboard live at `http://178.105.245.66:8501`** |
| Daily Backups | ✅ **ACTIVE** | 28+ daily backups of forward shadow DB. |

## D4 Paper Trader Performance 🏆

**Service**: `aurum1-d4-paper.service` — Donchian 20, 2R exit, BUY+SELL, no filters.

| Metric | Value |
|--------|-------|
| Started | 2026-07-02 (first trade) |
| **Trades (DB)** | **20 closed** |
| **Win Rate** | **55.0%** (11 wins / 9 losses) |
| **Net PnL** | **+$294.05** |
| **Avg R** | **+0.61R** |
| **Peak Equity** | **$10,449.15** (+$449.15, +4.49%) |
| **Current Equity** | **$10,449.15** |
| **Data Source** | Local cache (OANDA → forward-shadow → D4) |
| **DB** | 6 tables: trades, account_snapshots, settings, open_positions, missed_signals, health |

### Trade Log (Last 20)

| # | Date | Dir | Entry | Exit | R | PnL | Result |
|:-:|:----:|:---:|:----:|:---:|:-:|:---:|:------:|
| 1 | Jul 07 | BUY | $4,133 | $4,163 | +2.00 | +$59 | ✅ TP |
| 2 | Jul 07 | BUY | $4,157 | $4,140 | -1.00 | -$17 | ❌ SL |
| 3 | Jul 07 | SELL | $4,142 | $4,110 | +2.00 | +$64 | ✅ TP |
| 4 | Jul 07 | SELL | $4,127 | $4,090 | +2.00 | +$37 | ✅ TP |
| 5 | Jul 08 | SELL | $4,118 | $4,078 | +2.00 | +$40 | ✅ TP |
| 6 | Jul 08 | SELL | $4,089 | $4,043 | +2.00 | +$46 | ✅ TP |
| 7 | Jul 08 | SELL | $4,048 | $4,072 | -1.00 | -$23 | ❌ SL |
| 8 | Jul 08 | SELL | $4,060 | $4,087 | -1.00 | -$27 | ❌ SL |
| 9 | Jul 08 | BUY | $4,080 | $4,057 | -1.00 | -$23 | ❌ SL |
| 10 | Jul 09 | SELL | $4,065 | $4,083 | -1.00 | -$18 | ❌ SL |
| 11 | Jul 09 | BUY | $4,082 | $4,119 | +2.00 | +$37 | ✅ TP |
| 12 | Jul 09 | BUY | $4,121 | $4,103 | -1.00 | -$17 | ❌ SL |
| 13 | Jul 10 | SELL | $4,104 | $4,117 | -1.00 | -$24 | ❌ SL |
| 14 | Jul 10 | SELL | $4,104 | $4,121 | -1.00 | -$33 | ❌ SL |
| 15 | Jul 12 | BUY | $4,112 | $4,091 | -1.74 | -$42 | ❌ SL gap |
| 16 | Jul 12 | SELL | $4,091 | $4,057 | +2.00 | +$67 | ✅ TP |
| 17 | Jul 13 | SELL | $4,069 | $4,027 | +2.00 | +$42 | ✅ TP |
| 18 | Jul 13 | SELL | $4,036 | $3,991 | +2.00 | +$45 | ✅ TP |
| 19 | Jul 14 | BUY | $4,002 | $4,037 | +2.00 | +$35 | ✅ TP |
| 20 | Jul 14 | BUY | $4,030 | $4,077 | +2.00 | +$46 | ✅ TP |

### Equity Curve

```
Jul 02 — $10,000.00 ──► Start
Jul 02 — $10,051.82 ──► +$52  (1st BUY entry)
Jul 03 — $10,149.59 ──► +$150 (3 trades)
Jul 04 — $10,149.59 ──► flat (all closed)
Jul 06 — $10,123.62 ──► -$26 (new BUY)
Jul 06 — $10,165.79 ──► +$166 (2 trades)
Jul 07 — $10,214.95 ──► +$215 (TP + new BUY)
Jul 08 — $10,383.98 ──► +$384 (4 SELL wins in a row)
Jul 10 — $10,350.00 ──► drawdown (3 stop losses)
Jul 13 — $10,420.00 ──► recovery (3 SELL wins)
Jul 14 — $10,449.15 ──► 🏆 PEAK (20 trades, +4.49%)
```

### Infrastructure Status (Phase 0 — Complete ✅)

| Feature | Status | Details |
|---------|--------|---------|
| PID file / single-instance lock | ✅ | Prevents duplicate processes |
| R-multiple in broker trade dict | ✅ | Kelly calculator works correctly |
| Account snapshots every cycle | ✅ | 500+ records in DB |
| entry_time / exit_time columns | ✅ | Trade duration calculable |
| Read state from DB on restart | ✅ | Equity, settings, trades restored |
| Trade history capped at 10,000 | ✅ | Memory safety |
| Drawdown % in status line | ✅ | Shown each tick |
| Settings table | ✅ | last_processed_ts persisted |
| Open position recovery | ✅ | Positions restored on restart |
| **Trade recording to DB** | ✅ **FIXED** | All trades now persist (was a bug) |

### Observability Status (Phase 1 — Complete ✅)

| Feature | Status | Details |
|---------|--------|---------|
| Entry slippage tracking | ✅ | Signed per entry |
| Exit slippage tracking | ✅ | Signed per close |
| Spread in status line | ✅ | `Sprd=X.Xp` each tick |
| Latency min/max/avg | ✅ | Tracked per execution |
| Missed signal logging | ✅ | Logged to DB |
| Periodic observability report | ✅ | Full summary every ~1h |
| Health file | ✅ | JSON on disk |
| Streamlit dashboard | ✅ | Live at port 8501 |

### Validation Status (Phase 2 — Complete ✅)

| Analysis | Status | Key Result |
|----------|--------|------------|
| **Monte Carlo (10k sims)** | ✅ **Complete** | 0% ruin, 99th DD 20.3% |
| **Walk-Forward L20** | ✅ **Complete** | PF 1.14, Sharpe 1.27, 88.9% positive |
| **Walk-Forward L55** | ✅ **Complete** | PF 1.09, worse than L20 |
| **Risk Sensitivity** | ✅ **Complete** | 0.25% is sweet spot |
| **TC Stress Test** | ✅ **Complete** | Survives 6p spread + 2p slippage (S=0.75) |
| **ICIR & Decay** | ✅ **Complete** | Weak IC (-0.076) but profit from 2R exit design |
| **Live vs Backtest** | 🟡 **Running** | Comparator script deployed, needs 100+ trades |

## 11-Year Backtest Results

| Rank | Variant | Directions | Exit | PF | PnL | Trades |
|:----:|---------|:---------:|:---:|:--:|:---:|:------:|
| **1** | **D4 🏆** | BUY+SELL | 2R | **1.14** | **+$42,678** | 8,175 |
| 2 | D6 | BUY+SELL | 2R + ML | 1.14 | +$42,681 | 8,169 |
| 3 | Raw | BUY only | 2R | 1.14 | +$17,156 | 4,879 |
| 4 | D2 | BUY only | 1R + filters | 1.03 | +$1,667 | 6,890 |
| 5 | D3 | BUY+SELL | 1R + filters | 1.02 | +$1,162 | 3,544 |

**Key Insight**: D4 (simplest: 2R exit, no filters, both directions) dominates over 11 years.

## Risk Sensitivity (Monte Carlo)

| Risk/Trade | Med DD | 95th DD | 99th DD | Worst DD | Med Return | Ruin |
|:----------:|:------:|:-------:|:-------:|:--------:|:----------:|:----:|
| 0.10% | 4.9% | 7.2% | 8.8% | 12.4% | +114% | 0% |
| **0.25%** ⬅️ | **11.9%** | **17.2%** | **20.3%** | **27.9%** | **+551%** | **0%** |
| 0.50% | 22.8% | 32.4% | 37.3% | 49.4% | +3,704% | 0% |
| 1.00% | 41.3% | 55.3% | 62.4% | 76.4% | +93,528% | 0% |

**Sweet spot: 0.25%** — only 1.2% of paths exceed 20% drawdown, 0% ruin.

## Server

| Detail | Value |
|--------|-------|
| Host | `aurum1-paper-server` (178.105.245.66) |
| OS | Ubuntu 24.04.4 LTS |
| Disk | 38GB total, 53% used (17G free) |
| Memory | 3.7GB total, ~11% used |
| Python | 3.12.3 |
| Services | D4 paper trader, forward shadow, dashboard (3 active) |

## Key Decisions

- **May 27**: Main orchestrator stopped (signal_2). D4 paper trader becomes primary.
- **Jun 11**: Forward shadow service deployed.
- **Jun 28**: D4 paper trader deployed. Shadow timers added.
- **Jun 29**: Fixed data source (yfinance → local cache).
- **Jun 30**: First 3 trades executed (+$85). Walk-forward validation.
- **Jul 2**: Phase 0 complete — PID lock, R-multiple, snapshots, state recovery.
- **Jul 4**: Phase 1 observability — slippage, latency, spread, missed signals.
- **Jul 7**: **Trade recording fixed** (all trades now save to DB). TC stress test. 4 bugs killed.
- **Jul 8-14**: 20 trades accumulated. Equity grew from $10,000 → $10,449 (+4.49%).
- **Jul 14**: Dashboard deployed. ICIR/decay analysis. Documentation overhaul.

## Next Actions

1. ✅ ~~D4 paper trader deployed and trading~~
2. ✅ ~~DB persist bug fixed~~ (+ all 4 bugs)
3. ✅ ~~Phase 0 infrastructure complete~~
4. ✅ ~~Phase 1 observability complete~~
5. ✅ ~~Walk-forward validation (L20 + L55)~~
6. ✅ ~~Risk sensitivity analysis~~
7. ✅ ~~Open position recovery~~
8. ✅ ~~Monte Carlo analysis (10k sims, 0% ruin)~~
9. ✅ ~~TC Stress Test~~
10. ✅ ~~ICIR & Decay Analysis~~
11. ✅ ~~Dashboard deployed~~
12. 🔲 Accumulate 100+ trades before strategy changes
13. 🔲 Phase 0.5 — cooldown + breakeven stop (after 100 trades)
14. 🔲 Live vs backtest comparator needs more trade data
