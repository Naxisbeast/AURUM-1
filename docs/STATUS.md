# AURUM-1 System Status

**Last updated**: 2026-07-18

## Operational Status

| Component | Status | Details |
|-----------|--------|---------|
| D4 Paper Trader 🏆 | ✅ **ACTIVE** | Donchian breakout, 2R exit, BUY+SELL. **Risk: 0.25% (0.35% pending deploy)** |
| Forward Shadow (Raw Donchian 2R) | ✅ **ACTIVE** | Data pipeline — 236K+ M15 candles cached. |
| Dashboard | ✅ **ACTIVE** | Streamlit via Cloudflare tunnel |
| D1-D7 Shadow Journals | 🟡 **VARIOUS** | D4 shadow runs on timer; D2-D7 are research-only |
| ML Retrain | ❌ **DISABLED** | Timer exists but models are unused in production |
| Main Orchestrator | ❌ **STOPPED** | Last run May 27 2026. D4 replaced it. |

## Hardening Status (Phases 0-2 Complete ✅)

| Phase | Status | Summary |
|-------|--------|---------|
| **0: Truth Map** | ✅ Complete | Forensic scan: dead code, broken imports, test gaps, risk decisions. See `docs/system/TRUTH_MAP.md` |
| **1: Stabilization** | ✅ Complete | 6 import fixes, 8 `__init__.py` added, dead code archived, silent `except:pass` fixed, 9 deploy templates updated, 8 backtesting ROOT paths fixed, 5 new tests |
| **2: Validation** | ✅ Complete | Walk-forward (88.9% positive), Monte Carlo (0% ruin), TC stress (survives 6p+2p), risk sensitivity, ICIR decay — all confirmed no regression |
| **Risk Bump (0.25% → 0.35%)** | ⏳ **PENDING** | Code ready. Deploy + restart scheduled Sunday 20:00 UTC |
| **3: Analytics** | ⬜ Not started | Trade quality scoring, prop firm sim, system health dashboard |
| **4: Evidence Collection** | ⬜ Not started | D4 runs untouched at 0.35% for 100 trades |

## D4 Paper Trader Performance 🏆

**Service**: `aurum1-d4-paper.service` — Donchian 20, 2R exit, BUY+SELL, no filters.

| Metric | Value |
|--------|-------|
| Started | 2026-07-02 (first trade) |
| **Trades (DB)** | **27 closed** |
| **Win Rate** | **55%** |
| **Net PnL** | **+$317** |
| **Avg R** | **+0.57R** |
| **Equity** | **$10,472** (+4.72%) |
| **Open Position** | BUY @ $4,001.26 (TP: $4,045.25, SL: $3,979.27) |
| **Data Source** | Local cache (OANDA → forward-shadow → D4) |

### Validation Results (Post-Cleanup, 2026-07-18)

| Analysis | Result |
|----------|--------|
| **Walk-Forward L20** | 16/18 positive (88.9%), mean PF 1.14, mean Sharpe 1.27 |
| **Monte Carlo (10K sims)** | Ruin: 0%, P(DD>20%): 1.2%, median return: +551% |
| **TC Stress (baseline)** | PF 1.14, Sharpe 1.27, WR 37%, MaxDD 5.4% |
| **TC Stress (max: 6p+2p)** | PF 1.09, WR 37%, MaxDD 6.2% — survives |
| **ICIR Decay** | Peak IC at 15min, decays gracefully by 12.5h |
| **Risk Sensitivity (0.35%)** | MedDD 16.4%, 95thDD 23.5%, P(DD>20%): 24.9%, ruin: 0% |

### Test Suite (265 passing)

```
# Full CI — .github/workflows/test.yml
Core unit tests:    73 tests (paper_broker, risk_manager, donchian_signals, instruments)
D4 regression:       6 tests (d4_regression, backtest_sanity)
Trade quality:      18 tests
Prop firm sim:      20 tests
Evidence:           12 tests
Execution/Oanda:    41 tests
Dashboard metrics:  27 tests
Forward shadow CI:  42 tests
Watchdog:           13 tests
Render smoke:        3 tests
Research edge:       3 tests
Donchian research:   1 test
Engine/modules:      6 tests
Total:             265 tests, all passing
```

## Infrastructure

| Feature | Status |
|---------|--------|
| Data → Trading decoupled | ✅ Forward shadow fills cache; D4 reads from it |
| State persistence | ✅ Equity, trades, positions survive restart |
| Single-instance lock | ✅ PID file at `run/d4_paper_trader.pid` |
| Stale data detection | ✅ Warns if candle > 2h old during market hours |
| Alert webhook | ✅ Optional `ALERT_WEBHOOK_URL` |
| Session-aware spread | ✅ 1.0x overlap, 1.3x single, 2.0x Asian |
| Folded-normal slippage | ✅ (No favorable slippage on market orders) |
| Service units in repo | ✅ Paths corrected for script reorganization |

## Key Decisions

- **May 27**: Main orchestrator stopped. D4 becomes primary.
- **Jun 11**: Forward shadow deployed.
- **Jun 28**: D4 paper trader deployed.
- **Jul 14**: Dashboard deployed. Phase 0-2 research complete.
- **Jul 18**: Hardening v1.0 Phases 0-2 complete. Risk bump prepared.

## Pending Actions

1. ⏳ **Sunday 20:00 UTC** — Deploy hardening updates + restart D4 at 0.35% risk
2. 🔲 24h post-deploy monitoring
3. 🔲 Accumulate 100+ trades at 0.35%
4. 🔲 Phase 3: Analytics (trade scoring, prop firm sim, health dashboard)
5. 🔲 Phase 4: Evidence collection for strategy review
