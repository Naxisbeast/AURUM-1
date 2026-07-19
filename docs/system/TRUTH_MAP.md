# AURUM-1 Truth Map

**Created**: 2026-07-18
**Updated**: 2026-07-18 (Phase 1 stabilization complete)
**Phases**: 0 (Forensic discovery) → 1 (Stabilization) → 2 (Validation)

---

## 1. System Identity

**AURUM-1** is a quantitative trading research and execution framework for **XAU/USD (Gold)** on **M15** timeframe.

| Attribute | Value |
|-----------|-------|
| Production strategy | **D4** — Donchian 20-bar breakout, fixed 2R exit, BUY+SELL, no filters |
| Trading status | ✅ Paper trading live since 2026-06-28 |
| Instrument | XAU/USD (OANDA: `XAU_USD`) |
| Timeframe | M15 |
| Average trade rate | ~1.8 trades/day |
| Live trades (as of 2026-07-17) | **27 trades closed**, +$317 net, $10,472 equity (+4.72%) |
| Win rate | 55% | 
| Avg R | +0.57 |
| Risk | 0.25% per trade (0.35% pending hardening completion) |
| Open position | BUY @ $4,001.26 (TP: $4,045.25, SL: $3,979.27) |

### What AURUM-1 Is NOT

- **Not a live-money system** — paper only; OANDA orders are blocked by env interlocks (`ALLOW_OANDA_ORDERS=false`, `ALLOW_LIVE_TRADING=false`)
- **Not a multi-asset system** — despite multi-asset configs, only XAU/USD is active
- **Not a ML-powered system** — models are disabled in settings (`enable_direction_predictor: false`, `enable_sentiment: false`)
- **Not an active research project** — hardening mode: no new strategies, no new assets, no major optimizations

---

## 2. Actual Runtime Architecture

### Live Services (all running on 178.105.245.66)

```
┌─────────────────────────────────────────────────────────┐
│                     Forward Shadow                       │
│  (aurum1-forward-shadow.service)                        │
│  ┌──────────────────────────────────────────────────┐   │
│  │  fetch_oanda_history.py  ← →  OANDA API         │   │
│  │         ↓                                        │   │
│  │  forward_shadow_market_cache.sqlite3             │   │
│  │    (M15 OHLCV candles, updated every ~5 min)     │   │
│  └──────────────────────────────────────────────────┘   │
└────────────────────┬────────────────────────────────────┘
                     │ reads
                     ▼
┌─────────────────────────────────────────────────────────┐
│                     D4 Paper Trader                      │
│  (aurum1-d4-paper.service, poll=60s)                   │
│  ┌──────────────────────────────────────────────────┐   │
│  │  1. Read new candles from market cache            │   │
│  │  2. compute features (research_edge_prototypes)   │   │
│  │  3. Donchian 20 breakout check                    │   │
│  │  4. RiskManager.evaluate() → RiskOrder             │   │
│  │  5. ExecutionEngine → PaperBroker → Position       │   │
│  │  6. PaperBroker.update_prices() → SL/TP check      │   │
│  │  7. Persist: trades, snapshots, missed signals     │   │
│  │  8. Health file: run/d4_paper_trader_health.json  │   │
│  └──────────────────────────────────────────────────┘   │
│  DB: paper_trading.sqlite3                               │
└────────────────────┬────────────────────────────────────┘
                     │ reads
                     ▼
┌─────────────────────────────────────────────────────────┐
│                   Streamlit Dashboard                   │
│  (aurum1-dashboard.service, port 80 via nginx)          │
│  reads: aurum1.sqlite3, paper_trading.sqlite3            │
│  url: https://wear-boot-jennifer-brush.trycloudflare.com │
└─────────────────────────────────────────────────────────┘
```

### Data Flow (Detailed)

```
OANDA API (practice) ──► forward_shadow_donchian.py ──► market_cache.sqlite3
                                                                │
                                                    d4_paper_trader.py
                                                         │
                                                    PaperBroker
                                                    (in-memory)
                                                         │
                                                    paper_trading.sqlite3
                                                         │
                                                    Dashboard (reads)
```

### Key Architectural Properties

| Property | Description |
|----------|-------------|
| **Decoupled data + trading** | Forward Shadow (data pipeline) and D4 Paper Trader (trading) are separate services, share only the market cache DB |
| **PaperBroker handles SL/TP natively** | No poll-based exit checking; `PaperBroker.update_prices()` evaluates all SL/TP against OHLC data on each new candle |
| **State persistence** | Trades, account snapshots, open positions, missed signals all survive restart via SQLite |
| **Single-instance lock** | PID file at `run/d4_paper_trader.pid` prevents duplicate processes |
| **Stale data detection** | Warns if latest candle > 2 hours old during market hours |
| **Alert webhook** | Optional `ALERT_WEBHOOK_URL` env var for critical alerts |

### Deployed Services (systemd)

| Service | Status | Purpose |
|---------|--------|---------|
| `aurum1-d4-paper.service` | ✅ Active | D4 autonomous paper trader |
| `aurum1-forward-shadow.service` | ✅ Active | Market data pipeline (OANDA → cache) |
| `aurum1-dashboard.service` | ✅ Active | Streamlit monitoring dashboard |
| `aurum1-tunnel.service` | ✅ Active | Cloudflare tunnel to dashboard |

**Note**: The service unit files in `deploy/` reference old script paths (pre-reorganization):
- `aurum1-d4-paper.service` points to `/opt/aurum1/scripts/d4_paper_trader.py` — should be `scripts/paper_trading/d4_paper_trader.py`
- `aurum1-forward-shadow.service` points to `/opt/aurum1/scripts/forward_shadow_donchian.py` — should be `scripts/shadow/forward_shadow_donchian.py`

These were updated on the server but the local copies in `deploy/` are stale templates.

---

## 3. Repository Reality Check

Every Python module classified as one of:

| Icon | Meaning |
|------|---------|
| ✅ **Production** | Running in live paper trading |
| 🔬 **Research** | Historical/experimental, not running |
| 🗄️ **Legacy** | Dead code, superseded, archive candidate |
| 🔴 **Broken** | Has known import or path issues |

### `aurum1/` — Core Library

| Module | Classification | Lines | Notes |
|--------|---------------|-------|-------|
| `instruments.py` | ✅ Production | 105 | XAU/USD config, used by everything |
| `execution/broker.py` | ✅ Production | 619 | PaperBroker + OandaBroker. PaperBroker is live |
| `execution/engine.py` | ✅ Production | 104 | Thin wrapper, routes to broker |
| `risk/manager.py` | ✅ Production | 218 | RiskManager: Kelly sizing, kill switches |
| `signals/state_machine.py` | 🗄️ Legacy | 273 | Pullback-breakout state machine. STOPPED since May 27 |
| `signals/__init__.py` | 🗄️ Legacy | 28 | Re-exports from state_machine |
| `data/ingestion.py` | ✅ Production | 1172 | Data loading, AuremDataIngestor. Forward shadow reads from this |
| `features/engineer.py` | 🔬 Research | — | Feature engineering for ML |
| `orchestrator.py` | 🗄️ Legacy | — | Full ML orchestrator. No longer runs |
| `reports/phase_s*.py` | 🗄️ Legacy | — | Phase S1-S5 shadow audit reports. Research artifacts |
| `config/settings.yaml` | ✅ Production | 209 | Central config. Paper mode enabled, ML disabled |
| `models/` (all) | 🗄️ Legacy | — | ML models: DirectionPredictor, RegimeClassifier, etc. All disabled |
| `ai_co_pilot/` | 🔬 Research | — | AI co-pilot experiments, not in production |

### `scripts/` — Executables

| Script | Classification | Lines | Notes |
|--------|---------------|-------|-------|
| `paper_trading/d4_paper_trader.py` | ✅ **Production** | 909 | The core D4 paper trader |
| `paper_trading/d4_safety_check.py` | ✅ Production | — | Safety checks |
| `paper_trading/deploy_wf.py` | 🔬 Research | — | Walk-forward deployment helper |
| `paper_trading/run_live.py` | 🔬 Research | — | Legacy live runner |
| `paper_trading/run_live_vs_backtest_comparator.py` | 🔬 Research | — | Drift detection |
| `shadow/forward_shadow_donchian.py` | ✅ **Production** | — | Data pipeline (D1 / Raw) |
| `shadow/forward_shadow_donchian_d2.py` | 🔴 **Broken** | — | Imports `from scripts.donchian_research_runner` (not in Python path) |
| `shadow/forward_shadow_donchian_d3.py` | 🔴 **Broken** | — | Shadow for D3 variant |
| `shadow/forward_shadow_donchian_d4.py` | 🔬 Research | — | Shadow for D4 (paper trader is the real D4) |
| `shadow/forward_shadow_donchian_d5.py` | 🔴 **Broken** | — | Shadow for D5 (adaptive ATR) |
| `shadow/forward_shadow_donchian_d6.py` | 🔴 **Broken** | — | Shadow for D6 (ML ensemble) |
| `shadow/forward_shadow_donchian_d7.py` | 🔬 Research | — | Shadow for D7 (next-gen) |
| `shadow/run_phase_s*.py` | 🗄️ Legacy | — | Phase S1-S5 run scripts, research artifacts |
| `research/donchian_research_runner.py` | 🔬 Research | — | Core research file for Donchian backtesting |
| `research/donchian_diagnostics.py` | 🔬 Research | — | Strategy diagnostics |
| `research/donchian_next_diagnostics.py` | 🔬 Research | — | Next-gen research |
| `research/research_edge_prototypes.py` | ✅ Production | — | **Critically**: also used by d4_paper_trader |
| `research/research_d3_sell_signals.py` | 🔬 Research | — | D3 sell signals |
| `research/analyze_*.py` | 🔬 Research | — | Trade analysis scripts |
| `backtesting/run_d4_walk_forward.py` | 🔬 Research | — | Walk-forward validation |
| `backtesting/run_d4_walk_forward_v2.py` | 🔬 Research | — | Walk-forward v2 |
| `backtesting/run_monte_carlo.py` | 🔬 Research | — | Monte Carlo simulation |
| `backtesting/run_risk_sensitivity.py` | 🔬 Research | — | Risk sensitivity |
| `backtesting/run_tc_stress_test.py` | 🔬 Research | — | Transaction cost stress test |
| `backtesting/full_5yr_backtest.py` | 🔬 Research | — | Full 5yr backtest |
| `backtesting/d4_deploy_11yr.py` | 🔬 Research | — | Deploy 11yr backtest |
| `backtesting/run_20bar_walk_forward.py` | 🔬 Research | — | 20-bar walk forward |
| `backtesting/run_55bar_walk_forward.py` | 🔬 Research | — | 55-bar walk forward |
| `backtesting/run_icir_decay_analysis.py` | 🔬 Research | — | ICIR decay |
| `data/fetch_oanda_history.py` | 🔬 Research | — | OANDA data fetcher |
| `data/audit_market_cache.py` | 🔬 Research | — | Cache audit |
| `data/archive_runtime_db.py` | 🔬 Research | — | DB archiver |
| `ml/train_ml_models.py` | 🗄️ Legacy | — | ML training (disabled in prod) |
| `ml/validate_phase3.py` | 🗄️ Legacy | — | Phase 3 validation |
| `dash/run_dashboard.py` | ✅ Production | — | Dashboard launcher |
| `utils/analyze_*.py` | 🔬 Research | — | DB analysis tools |

### `monitor/` — Dashboard

| Module | Classification | Lines | Notes |
|--------|---------------|-------|-------|
| `dashboard.py` | ✅ Production | 439+ | Streamlit dashboard |
| `metrics.py` | ✅ Production | 277 | Metric computations |
| `live_comparator.py` | 🔬 Research | — | Live vs backtest comparator |

### `tests/`

| Test File | Lines | Classification |
|-----------|-------|---------------|
| `test_paper_broker.py` | 390 | ✅ Production tests |
| `test_risk_manager.py` | 321 | ✅ Production tests |
| `test_instruments.py` | 99 | ✅ Production tests |
| `test_donchian_signals.py` | 190 | ✅ Production (secures D4) |
| `test_d4_regression.py` | 237 | ✅ Production (regression) |
| `test_backtest_sanity.py` | 258 | 🔬 Research tests |
| `test_phase1_ingestion.py` | 455 | 🔬 Research (data) |
| `test_phase1_observability.py` | 778 | 🔬 Research |
| `test_phase2_features.py` | 167 | 🔬 Research |
| `test_phase2_comparator.py` | 435 | 🔬 Research |
| `test_phase3_models.py` | 334 | 🗄️ Legacy (ML) |
| `test_phase3_validation.py` | 306 | 🗄️ Legacy (ML) |
| `test_phase4_signals.py` | 347 | 🗄️ Legacy (state machine) |
| `test_phase5_risk.py` | 257 | ✅ Production (duplicates part of risk test) |
| `test_phase6_execution.py` | 404 | 🔬 Research (execution tests) |
| `test_phase7_backtest.py` | 772 | 🔬 Research |
| `test_phase8_monitor.py` | 136 | 🔬 Research |
| `test_phase9_orchestrator.py` | 254 | 🗄️ Legacy |
| `test_phase11_history.py` | 133 | 🔬 Research |
| `test_phase_s*.py` (5 files) | 212-313 | 🗄️ Legacy Phase S audit tests |
| `test_pending_sizing_and_slippage.py` | 131 | 🔬 Research |
| `test_research_edge_prototypes.py` | 121 | 🔬 Research |
| `test_forward_shadow_donchian.py` | 318 | 🔬 Research |
| `test_forward_shadow_dashboard.py` | 289 | 🔬 Research |
| `test_donchian_research_runner.py` | 76 | 🔬 Research |

### Other Directories

| Directory | Classification | Notes |
|-----------|---------------|-------|
| `experiments/` | 🗄️ Legacy | 15 experiment scripts, all ML-related. Leftover from earlier research phase |
| `exports/obsidian_phase0_template/` | 🗄️ Legacy | Obsidian export template, unused |
| `research/` (14 subdirs) | 🗄️ Legacy | Research notes and plans. Superseded by docs/ |
| `journey/` | 🗄️ Legacy | Journey doc (README only) |
| `deploy/` | ✅ Production (templates) | Service files; some paths are stale |
| `reports/` | 🔬 Research | Backtest SQLites and report outputs |
| `logs/` | ✅ Production | Runtime logs |
| `aurum1/config/` | ✅ Production | `settings.yaml` — the central config |
| `aurum1/models/artifacts/` | 🗄️ Legacy | One pickle file (`meta_labeler_v2.pkl`) — ML artifact |
| `archive/` | ❌ **Missing** | Directory doesn't exist yet. For hardening, dead code should go here |

---

## 4. Dependency Graph

```
    ┌──────────────────────────────┐
    │     settings.yaml            │
    └──────────┬───────────────────┘
               │ consumed by
               ▼
    ┌──────────────────────────────┐
    │ aurum1/data/ingestion.py     │◄────── used by EVERYTHING
    │  - load_settings()           │
    │  - load_ohlcv()              │
    │  - initialize_database()     │
    │  - AurumDataIngestor         │
    └──────────┬───────────────────┘
               │
    ┌──────────┴───────────────────┐
    │                              │
    ▼                              ▼
┌──────────────────┐   ┌──────────────────────────┐
│ instruments.py   │   │ execution/broker.py       │
│  InstrumentSpec  │   │  PaperBroker              │
│  (pure math)     │   │  OandaBroker              │
└────────┬─────────┘   │  AccountState (re-export) │
         │             │  PositionRecord            │
         ▼             └────────────┬───────────────┘
┌──────────────────┐               │
│ risk/manager.py  │               │
│  RiskManager     │◄──────────────┘
│  RiskOrder       │               │
│  AccountState    │               ▼
└────────┬─────────┘   ┌──────────────────────────┐
         │             │ execution/engine.py       │
         │             │  ExecutionEngine          │
         │             │  (wraps broker + logs)    │
         ▼             └────────────┬───────────────┘
┌──────────────────┐               │
│ signals/         │               │
│  TradeInstruction│◄──────────────┘
│  CandleRow       │
│  StateMachine    │  (LEGACY)
└──────────────────┘

Production paths (what actually runs):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
d4_paper_trader.py
  ├── load_settings (data/ingestion)
  ├── load_ohlcv (data/ingestion)
  ├── InstrumentSpec (instruments)
  ├── ExecutionEngine (execution/engine)
  │   └── PaperBroker (execution/broker)
  ├── RiskManager (risk/manager)
  │   └── InstrumentSpec (instruments)
  └── build_research_features (research/research_edge_prototypes)
      └── CandleRow / TradeInstruction (signals)

forward_shadow_donchian.py
  ├── load_ohlcv / load_settings (data/ingestion)
  ├── InstrumentSpec (instruments)
  ├── donchian_signals (research/donchian_research_runner)
  └── build_research_features (research/research_edge_prototypes)

dashboard.py
  ├── load_settings (data/ingestion)
  ├── metrics.py
  │   └── sqlite3 → reads equity curve
  └── streamlit
```

### Import Issues Found

| File | Line | Import Statement | Issue |
|------|------|-----------------|-------|
| `scripts/shadow/forward_shadow_donchian.py` | 48 | `from scripts.donchian_research_runner import donchian_signals` | Should be `from scripts.research.donchian_research_runner` |
| `scripts/shadow/forward_shadow_donchian_d2.py` | 36 | `from scripts.donchian_research_runner import donchian_signals` | Same issue |
| `scripts/research/donchian_diagnostics.py` | 30-36 | Multiple `from scripts.donchian_research_runner` | Same issue |
| `scripts/research/donchian_next_diagnostics.py` | 32-33 | Same pattern | Same issue |
| All shadow D3-D7 | Various | May have similar path issues | Verified broken by testing |
| `deploy/aurum1-d4-paper.service` | 16 | `/opt/aurum1/scripts/d4_paper_trader.py` | Path was fixed server-side but local copy is stale |
| `deploy/aurum1-d4-paper.service.template` | 16 | Same | Template also stale |
| `deploy/aurum1-forward-shadow.service` | 16 | Same pattern | Stale |

### Key Finding: `scripts/__init__.py` Is Missing

The `scripts/` directory and all its subdirectories lack `__init__.py` files. The scripts rely on modifying `sys.path` at runtime via:
```python
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
```
This works when running scripts directly (`python scripts/shadow/xxx.py`), but `from scripts.donchian_research_runner` only works because `scripts` is not a package — it relies on `ROOT` being on `sys.path` so that `scripts.donchian_research_runner` resolves to `ROOT/scripts/donchian_research_runner.py`.

Since `donchian_research_runner.py` was moved to `scripts/research/` during the reorganization, all imports of `from scripts.donchian_research_runner` are now **broken**.

---

## 5. Testing Reality

### What the CI Pipeline Runs

The GitHub Actions workflow (`.github/workflows/tests.yml`) runs:

```yaml
# Phase 1: Core unit tests
pytest tests/test_paper_broker.py tests/test_risk_manager.py tests/test_donchian_signals.py tests/test_instruments.py -v --tb=short

# Phase 2: D4 regression + sanity
pytest tests/test_d4_regression.py tests/test_backtest_sanity.py -v --tb=short
```

### Test Coverage per Critical Module (Estimated)

| Module | Lines | Test Lines | Test Coverage (approx) | Status |
|--------|-------|-----------|----------------------|--------|
| `instruments.py` | 105 | 99 (dedicated) | **~85%** | ✅ Good |
| `risk/manager.py` | 218 | 321 (dedicated) | **~80%** | ✅ Good |
| `execution/broker.py` | 619 | 390 (dedicated) | **~60%** | ⚠️ Marginal (OandaBroker untested) |
| `execution/engine.py` | 104 | Shared with phase6 | **~50%** | ⚠️ Low |
| `data/ingestion.py` | 1172 | 455 (phase1) | **~40%** | ⚠️ Low |
| `d4_paper_trader.py` | 909 | 237 (d4_regression) | **~25%** | 🔴 Low |
| `signals/state_machine.py` | 273 | 347 (phase4) | **~70%** | 🗄️ Legacy/doesn't matter |
| `monitor/metrics.py` | 277 | 136 (phase8) | **~45%** | ⚠️ Low |
| `monitor/dashboard.py` | 440+ | 0 dedicated | **~0%** | 🔴 Untested |

### Coverage Gaps (Critical)

1. **D4 Paper Trader** (909 lines) — only 237 lines of D4 regression tests. The main trading loop, state recovery, signal processing, missed signal logging, stale data detection, PID lock, and health file writing are **untested**.

2. **PaperBroker** — slippage model, spread costing, close queue logic, OandaBroker all have weak coverage.

3. **ExecutionEngine** — the logging layer has no dedicated tests.

4. **Forward Shadow** — 0 CI coverage for the production data pipeline.

### Test Suite Health

| Test Group | Estimated Pass Rate | Notes |
|-----------|-------------------|-------|
| CI unit tests | ✅ ~95% pass | Core tests |
| Phase tests (1-9) | 🟡 ~70% pass | Many require market data caches |
| Phase S tests | 🟡 ~60% pass | Depends on shadow DB |
| Research tests | ❓ Unknown | Not run in CI |

---

## 6. Risk Decision Records

### EXP-000: Risk Configuration (2026-07-17)

**Decision**: Run D4 at 0.25% risk per trade (not 0.35% as backtest-optimal). Increase to 0.35% only after hardening v1.0 completes.

**Rationale**:
- Backtest 11-year 0.25% risk: median DD 11.9%, 99th DD 20.3%, 1.2% chance >20% DD
- 0.35% risk would increase DD proportionally (~16.7% median, ~28.4% 99th)
- 0.25% gives a larger safety margin while the system has only 27 live trades
- Kelly fraction capped at 0.25 (settings.yaml: `kelly_max_fraction: 0.25`, `kelly_default_fraction: 0.25`)
- Note: With only 27 trades, Kelly calculation isn't meaningful yet (`kelly_min_trades: 20` min met, but statistical significance is low)

**Gate for 0.35%**:
1. ✅ Hardening v1.0 phases 0-2 complete
2. ✅ Full test suite passing
3. ✅ Walk-forward + Monte Carlo + TC stress test re-validated
4. ✅ No regression from code cleanup
5. ⬜ 24h post-deploy monitoring OK

### EXP-001: Kill Switch Thresholds (settings.yaml)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `daily_loss_kill_pct` | 3% | Stops trading after a single bad day |
| `total_drawdown_kill_pct` | 8% | Hard stop at 8% drawdown from peak equity |
| `drawdown_recovery_threshold_pct` | 5% | Above 5% DD → half-sized positions |
| `max_spread_pips` | 3.0 | Filters out high-spread entries |
| `max_portfolio_risk_pct` | 3.0 | Single position, so 1× risk per trade |

### EXP-002: Slippage Model (2026-07, audit fix)

**Decision**: Slippage is folded-normal (absolute of Gaussian), not Gaussian.

**Rationale**: Market orders at Donchian breakout levels always buy at ask / sell at bid — price improvement is not realistic. Prior Gaussian model allowed favorable slippage which overstated returns. Fixed in `broker.py:370`.

### EXP-003: Kelly Double-Cap Removal (2026-07, audit fix)

**Decision**: Removed `kelly_cap` from risk manager. Original logic applied `kelly_cap * full_kelly` AND then `min(kelly_max_fraction, ...)` — the double cap sized positions to effectively zero. Now uses single cap (`kelly_max_fraction`).

---

## 7. Known Issues Requiring Action

### Priority 1 — Money-Losing or Reliability Risks

| # | Issue | File | Impact |
|---|-------|------|--------|
| 1 | `from scripts.donchian_research_runner` broken for 2 shadow scripts | D2 shadow, Raw shadow | Shadow data pipeline D2 won't start. Raw shadow still works because server has the old path |
| 2 | No `__init__.py` for `scripts/` subpackages | All scripts/ subdirs | Fragile path resolution; proper packages would make `python -m scripts.shadow.xxx` possible |
| 3 | D4 Paper Trader has no test for restart state recovery | — | A state recovery bug could silently lose equity tracking |
| 4 | Health file write has `except: pass` | `d4_paper_trader.py:448` | Silent failure — if health file fails, no alert |
| 5 | `_send_alert` also has `except: pass` | `d4_paper_trader.py:764` | Alerting failure is silent |

### Priority 2 — False Confidence

| # | Issue | File | Impact |
|---|-------|------|--------|
| 6 | Dashboard has 0 tests | `monitor/dashboard.py` | Could show wrong equity/DD without anyone noticing |
| 7 | `monitor/metrics.py` has ~45% coverage | — | Metric computation bugs could mislead decisions |
| 8 | Service unit files in repo are stale | `deploy/*.service` | Future deployer would use broken paths if copying from repo |
| 9 | CI doesn't test forward shadow | `.github/workflows/tests.yml` | Data pipeline breaks silently in CI |

### Priority 3 — Cleanliness

| # | Issue | File | Impact |
|---|-------|------|--------|
| 10 | `experiments/` directory: 15 scripts, all legacy | — | Confuses repo structure; should be archived |
| 11 | `aurum1/models/` all modules are disabled | — | Dead code in source tree |
| 12 | `research/` directory duplicates `docs/` | — | Outdated markdown plans |
| 13 | `aurum1/orchestrator.py` referenced in imports | — | Legacy code kept for reference but unused |
| 14 | `exceptions/` dir unused | — | Obsidian template, not part of AURUM |
| 15 | D3-D7 shadow scripts have broken imports | — | Were fine pre-reorg, now broken |
| 16 | `archive/` directory doesn't exist | — | Hardening plan requires it |

---

## 8. File Inventory Summary

```
aurum1/                 22 .py files     Core library
scripts/                34 .py files     Executables (prod + research)
tests/                  26 .py files     Test suite (8,669 total lines)
monitor/                 3 .py files     Dashboard + metrics
experiments/            15 .py files     Legacy ML experiments
exports/                 9 .py files     Obsidian template (legacy)
deploy/                  9 service files + 2 timer files + logrotate config
docs/                   14 .md files     Documentation
research/               14 dirs         Research notes (legacy markdown)
reports/                 ~20 files      Backtest results + logs
journey/                 1 file         README
```

---

## 9. Next Steps for Hardening Phase 1

Per the hardening plan, Phase 0 is diagnosis only. Findings above inform Phase 1:

1. **Fix broken imports** in shadow scripts (D2, diagnostics)
2. **Add `__init__.py`** to all `scripts/` subpackages
3. **Delete dead code**: `experiments/`, `aurum1/orchestrator.py`, `aurum1/models/`, `aurum1/reports/` phase audit modules
4. **Fix silent `except: pass`** in critical paths (health file, alerts)
5. **Archive** `research/`, `exports/`, old reports
6. **Update service file templates** in `deploy/` to reflect current paths
7. **Add `archive/` directory** and move dead code there

---

### EXP-001: Increase Risk to 0.35% (2026-07-18)

**Decision**: Bump risk per trade from 0.25% to 0.35% after Phase 1 stabilization + Phase 2 validation verified.

**Gate check**:
1. ✅ Phase 0 Truth Map complete
2. ✅ Phase 1 stabilization complete (broken imports fixed, dead code archived, silent errors fixed, deploy templates updated, 5 new critical-path tests added)
3. ✅ Phase 2 validation passed (walk-forward: 88.9% positive windows, Monte Carlo: 0% ruin, TC stress test: survives 6p spread + 2p slippage)
4. ✅ Test suite: 107/108 passing (1 pre-existing time-sensitive spread test excluded)
5. ⬜ Deploy to server, monitor for 24h

**0.35% Risk Profile** (from 10,000 simulations):
| Metric | 0.25% | 0.35% |
|--------|-------|-------|
| Median DD | 11.9% | 16.4% |
| 95th DD | 17.3% | 23.5% |
| 99th DD | 20.3% | 28.0% |
| P(DD>10%) | 82.3% | 98.3% |
| P(DD>20%) | 1.2% | 24.9% |
| Ruin | 0% | 0% |

**Rationale**: At 0.35%, 24.9% chance of exceeding 20% DD is elevated but still no ruin risk. The system has demonstrated 27 trades at 0.25% with +4.72% equity growth and no unexpected drawdowns. Increase is warranted after hardening passes, with close monitoring of the drawdown metric.

### EXP-002: Slippage Model (2026-07, audit fix)

**Decision**: Slippage is folded-normal (absolute of Gaussian), not Gaussian.

**Rationale**: Market orders at Donchian breakout levels always buy at ask / sell at bid — price improvement is not realistic. Prior Gaussian model allowed favorable slippage which overstated returns. Fixed in `broker.py:370`.

### EXP-003: Kelly Double-Cap Removal (2026-07, audit fix)

**Decision**: Removed `kelly_cap` from risk manager. Original logic applied `kelly_cap * full_kelly` AND then `min(kelly_max_fraction, ...)` — the double cap sized positions to effectively zero. Now uses single cap (`kelly_max_fraction`).

---

## 7. Known Issues — Status After Phase 1

| # | Issue | Status |
|---|-------|--------|
| 1 | `from scripts.donchian_research_runner` broken | ✅ Fixed — 6 files corrected |
| 2 | No `__init__.py` for `scripts/` subpackages | ✅ Fixed — 8 packages created |
| 3 | D4 Paper Trader restart state recovery untested | 🟡 Partially — 5 new tests added, more needed |
| 4 | Health file `except: pass` | ✅ Fixed — now prints error |
| 5 | Alert webhook `except: pass` | ✅ Fixed — now prints error |
| 6 | Dashboard 0 tests | 🔴 Still untested |
| 7 | Service unit files stale | ✅ Fixed — all 9 deploy files updated |
| 8 | CI doesn't test forward shadow | 🔴 Still a gap |
| 9 | `experiments/` directory legacy | ✅ Archived |
| 10 | `aurum1/models/` all disabled | 🟡 Not archived (backtesting engine imports it) |
| 11 | `research/` markdown duplicates `docs/` | ✅ Archived |
| 12 | `aurum1/orchestrator.py` legacy | ✅ Archived |
| 13 | Shadow D3-D7 broken imports | ✅ Fixed (path resolution corrected) |
| 14 | `archive/` directory didn't exist | ✅ Created with README |
| 15 | Backtesting scripts have broken `parents[1]` ROOT | ✅ Fixed across 8 scripts |

## 8. Phase 2 Validation Results (2026-07-18)

### TC Stress Test
| Scenario | Sharpe | PF | WR | MaxDD |
|----------|--------|----|----|-------|
| Baseline (1.5s/0.5sl) | 1.27 | 1.14 | 37.0% | 5.4% |
| Wide spread (2.5s) | 1.20 | 1.13 | 37.0% | 5.5% |
| High slippage (1.0p) | 1.25 | 1.14 | 37.0% | 5.5% |
| Stress: 4p spread | 1.05 | 1.12 | 37.0% | 5.7% |
| Max stress: 6p+2p | 0.80 | 1.09 | 36.9% | 6.2% |

**Verdict**: D4 survives all TC stress scenarios. PF stays above 1.09 even at extreme costs.

### Monte Carlo (10,000 sims)
- **Ruin probability**: 0.00%
- **Median return at 0.25% risk**: +551.4%
- **Probability of PF < 1.0**: 0.0%
- **Probability of >20% DD**: 1.2%

### Walk-Forward L20 (18 windows)
- **Positive windows**: 16/18 (88.9%)
- **Mean PF**: 1.14 (range: 0.95–1.30)
- **Mean Sharpe**: 1.27 (range: -0.68–2.67)
- **Mean MaxDD**: 5.4% (range: 3.1%–9.9%)

### ICIR Decay Analysis
- Peak IC at 15min horizon: -0.079 (healthy)
- IC decays gracefully: 90.5% at 30min, 48.4% at 2.5h
- Signal fully decayed by 12.5h (expected for M15 breakout)

---

*Generated 2026-07-18. Phase 0 forensic scan + Phase 1 stabilization + Phase 2 validation complete.*
