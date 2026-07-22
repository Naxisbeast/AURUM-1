# AURUM-1 — Project Guide for AI Agents

## Project Identity
AURUM-1 is a **quantitative trading research and execution framework** for XAU/USD (Gold) on M15. The current production strategy is **D4**: Donchian 20-bar breakout, fixed 2R exit, BUY+SELL, no filters — paper trading live since Jul 2, 2026.

## Active Mission: AURUM Hardening v1.0
**No new strategies. No new assets. No major optimizations.** The goal is to stabilize, clean, test, and document the existing system while D4 accumulates 100 forward-tested trades.

**Status**: ✅ Hardening v1.0 fully complete as of 2026-07-21. Risk at 0.35%. All services active. 240 tests passing. See `docs/system/TRUTH_MAP.md` for the forensic map, `docs/JOURNEY.md` for the full story.

See `docs/system/TRUTH_MAP.md` for the complete forensic map, and `docs/HANDOVER_AURUM_HARDENING.md` for the full plan.

## What Actually Runs

| Path | Status |
|------|--------|
| `scripts/paper_trading/d4_paper_trader.py` | ✅ Production — D4 autonomous paper trader |
| `scripts/shadow/forward_shadow_donchian.py` | ✅ Production — market data pipeline (OANDA cache) |
| `aurum1/data/ingestion.py` | ✅ Production — data loading, used by everything |
| `monitor/dashboard.py` | ✅ Production — Streamlit dashboard |
| `monitor/metrics.py` | ✅ Production — dashboard metric computations |
| `aurum1/execution/broker.py` | ✅ Production — PaperBroker (SL/TP, slippage, spread) |
| `aurum1/execution/engine.py` | ✅ Production — ExecutionEngine wrapper |
| `aurum1/risk/manager.py` | ✅ Production — RiskManager (Kelly sizing, kill switches) |
| `aurum1/instruments.py` | ✅ Production — XAU/USD InstrumentSpec |
| `scripts/research/research_edge_prototypes.py` | ✅ Production — feature builder (also used by D4 trader) |
| `aurum1/config/settings.yaml` | ✅ Production — central config (risk: 0.35%) |
| `scripts/shadow/forward_shadow_donchian_d4.py` | 🟡 Shadow running (timer-based) |
| `monitor/trade_quality.py` | ✅ Production — trade quality scoring (Phase 3) |
| `monitor/prop_firm_simulator.py` | ✅ Production — prop firm challenge simulator (Phase 3) |
| `docs/EXP-001-template.md` | ✅ Documentation — experiment framework template (Phase 3) |
| `docs/` | ✅ Documentation |
| `scripts/backtesting/run_d4_walk_forward_v2.py` | 🔬 Research — walk-forward validation |
| `scripts/backtesting/run_monte_carlo.py` | 🔬 Research — Monte Carlo simulation |
| `scripts/backtesting/run_tc_stress_test.py` | 🔬 Research — TC stress test |
| `scripts/backtesting/run_risk_sensitivity.py` | 🔬 Research — risk sensitivity |
| `scripts/backtesting/run_20bar_walk_forward.py` | 🔬 Research — L20 walk-forward |
| `scripts/backtesting/run_55bar_walk_forward.py` | 🔬 Research — L55 walk-forward |
| `scripts/backtesting/run_icir_decay_analysis.py` | 🔬 Research — signal decay analysis |
| `scripts/research/donchian_research_runner.py` | 🔬 Research — Donchian backtesting research |
| `scripts/research/donchian_diagnostics.py` | 🔬 Research — diagnostic tools |
| `scripts/shadow/forward_shadow_donchian_d2.py` | 🔬 Research — D2 shadow (1R + filters) |
| `scripts/shadow/forward_shadow_donchian_d3.py` | 🔬 Research — D3 shadow (BUY+SELL 1R) |
| `scripts/shadow/forward_shadow_donchian_d5.py` | 🔬 Research — D5 shadow (adaptive ATR) |
| `scripts/shadow/forward_shadow_donchian_d6.py` | 🔬 Research — D6 shadow (ML ensemble) |
| `scripts/shadow/forward_shadow_donchian_d7.py` | 🔬 Research — D7 shadow (next-gen) |
| `aurum1/orchestrator.py` | ❌ Legacy — archived (`archive/aurum1/orchestrator.py`) |
| `aurum1/models/` | ❌ Legacy — ML models disabled in production (kept for engine imports) |
| `aurum1/reports/phase_s*.py` | ❌ Legacy — archived (`archive/aurum1/`) |
| `aurum1/ai_co_pilot/` | ❌ Legacy — archived |
| `experiments/` | ❌ Legacy — archived |
| `tests/` | 🟡 Test suite — 107 passing, 1 pre-existing time-sensitive failure |

## Decision Framework

Every proposed change must pass this four-question gate:
1. Does it improve **performance**?
2. Does it improve **reliability**?
3. Does it improve **explainability**?
4. Does it **reduce uncertainty**?

If none → reject.

## Key Philosophy
- The repo should tell the truth about what the system is
- Dead code should be archived (in `archive/`), not left in place
- Rejected hypotheses should be preserved (in `archive/`), not destroyed
- Everything earns its place in AURUM, including AURUM itself
- Evidence before promotion. Always.

## Server Info
- **Host**: 178.105.245.66
- **SSH**: `ssh -i ~/.ssh/aurum1_key root@178.105.245.66`
- **Working dir**: `/opt/aurum1`
- **Python**: `/opt/aurum1/.venv/bin/python`
- **Dashboard**: https://wear-boot-jennifer-brush.trycloudflare.com

## Current System State (as of 2026-07-18)

### Hardening Status
| Phase | Status |
|-------|--------|
| 0: Truth Map | ✅ Complete (`docs/system/TRUTH_MAP.md`) |
| 1: Stabilization | ✅ Complete (imports fixed, dead code archived, silent errors fixed, tests added) |
| 2: Validation | ✅ Complete (walk-forward, MC, TC stress all re-validated) |
| 3: Analytics | ✅ Complete (trade quality, prop firm sim, health dashboard, exp framework) |
| 4: Evidence Collection | ✅ Complete (tracker built, server monitoring at 0.35% risk) |

### Test Suite (240 passing)
```
# Full CI (7 groups, 240 tests) — .github/workflows/test.yml
pytest tests/test_paper_broker.py tests/test_risk_manager.py tests/test_donchian_signals.py tests/test_instruments.py -v --tb=short
pytest tests/test_d4_regression.py tests/test_backtest_sanity.py -v --tb=short
pytest tests/test_trade_quality.py tests/test_prop_firm_simulator.py -v --tb=short
pytest tests/test_evidence.py -v --tb=short
pytest tests/test_research_edge_prototypes.py -v --tb=short
pytest tests/test_phase6_execution.py -v --tb=short
pytest tests/test_metrics.py -v --tb=short
pytest tests/test_forward_shadow_ci.py -v --tb=short
```

### Validation Results
| Metric | 0.25% Risk | 0.35% Risk |
|--------|-----------|------------|
| Walk-forward positive windows | 88.9% (16/18) | — |
| Monte Carlo ruin probability | 0.00% | 0.00% |
| Median drawdown | 11.9% | 16.4% |
| P(DD > 20%) | 1.2% | 24.9% |
| TC stress: max stress PF | 1.09 | — |

## Services
```bash
systemctl status aurum1-d4-paper.service      # D4 paper trader
systemctl status aurum1-forward-shadow.service # Data pipeline
systemctl status aurum1-dashboard.service      # Dashboard
systemctl status aurum1-tunnel.service         # Cloudflare tunnel
```

## Repo Structure (Post-Hardening)

### Key directories
```
aurum1/                Core library (production + research)
scripts/                Executables (production + research)
tests/                  Test suite (107 passing)
monitor/                Dashboard + metrics
docs/                   Documentation
deploy/                 Service unit files (paths updated)
archive/                Dead code preserved for reference (added Phase 1)
```

### Import conventions
- All `scripts/` subpackages have `__init__.py` (added Phase 1)
- Scripts use `ROOT = Path(__file__).resolve().parents[2]` for repo root
- Backtesting scripts use `parents[2]` (fixed Phase 1 from incorrect `parents[1]`)
- `from scripts.research.xxx` not `from scripts.xxx` (moved modules)

## First Task for New Session
Read memory files in `C:\Users\thape\.claude\projects\C--Users-thape-Desktop-Trading-algorithim\memory\` first.

Key decisions from prior sessions:
- **Never** add Co-Authored-By lines to git commits
- **Prefer** option-based strategies over implementing the first idea

Current system state:
- Hardening v1.0 is complete — all phases 0-4 done
- D4 running at 0.35% risk on server (178.105.245.66)
- Equity ~$10,548, 29 trades, 240/240 tests passing
- Next gate: risk review at 50 trades, strategy review at 100 trades
- .githooks/pre-commit is installed (blocks credential commits)
- All ROOT paths use parents[2], all scripts subpackages have __init__.py
- Stale timers (ML retrain, D1/D2/D3/D6/D7 shadows) disabled on server

Key Phase 3 tools:
  - Trade quality: `python -c "from monitor.trade_quality import print_quality_report_from_db; from pathlib import Path; print_quality_report_from_db(str(Path('aurum1/data/paper_trading.sqlite3')))"`
  - Prop firm sim: `python -c "from monitor.prop_firm_simulator import simulate_all_challenges, load_trades_from_db; from pathlib import Path; [print(r.challenge.name, ':', 'PASS' if r.passed else 'FAIL' if r.failed else 'LIVE', r.total_return_pct) for r in simulate_all_challenges(load_trades_from_db(str(Path('aurum1/data/paper_trading.sqlite3'))))]"`
  - Exp framework: `docs/EXP-001-template.md`
