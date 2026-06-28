# AURUM-1

**AURUM-1** is a phased algorithmic trading system for XAU/USD (Gold) on the M15 timeframe. It provides live-capable data ingestion, feature engineering, ML model validation, signal generation, risk management, multi-broker execution, backtesting, forward shadow testing, and real-time monitoring.

> **Current status**: Research & forward-shadow testing. No live capital deployed.
> See [docs/STATUS.md](docs/STATUS.md) for the latest operational state.

---

## Quick Start

```bash
# Clone and enter
git clone git@github.com:Naxisbeast/AURUM-1.git
cd AURUM-1

# Set up Python 3.12 environment
python3.12 -m venv .venv
source .venv/bin/activate  # Linux/Mac
# or: .venv\Scripts\Activate.ps1  # Windows

# Install dependencies
pip install -r requirements.txt

# Configure secrets (never commit .env)
cp .env.example .env
# Edit .env with your API keys

# Run tests
python -m pytest -q --basetemp .pytest_tmp -p no:cacheprovider

# Run the dashboard
python scripts/run_dashboard.py

# Run a backtest
python scripts/run_backtest.py
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         AURUM-1 System                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                   Data Ingestion Layer                       │   │
│  │  OANDA API ──→ Market Cache (SQLite)                        │   │
│  │  FRED API  ──→ Macro Data (SQLite)                          │   │
│  │  Alpha Vant.──→ News/Sentiment                              │   │
│  │  CFTC       ──→ COT Data                                    │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│                              ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │               Feature Engineering Layer                      │   │
│  │  OHLCV ──→ ATR, ADX, EMA, BB, MACD, RSI                    │   │
│  │  Macro  ──→ DXY, VIX, Real Yield, CPI                       │   │
│  │  COT     ──→ Net Long/Short Positioning                     │   │
│  │  Time    ──→ Session labels, cyclical encoding              │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│                              ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │               Signal Generation Layer                        │   │
│  │  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐      │   │
│  │  │ Regime   │  │ Direction    │  │ Sentiment        │      │   │
│  │  │ Classifier│  │ Predictor   │  │ Scorer           │      │   │
│  │  └──────────┘  └──────────────┘  └──────────────────┘      │   │
│  │         │              │                  │                │   │
│  │         ▼              ▼                  ▼                │   │
│  │  ┌───────────────────────────────────────────────────┐    │   │
│  │  │              Ensemble Signal                       │    │   │
│  │  └───────────────────────────────────────────────────┘    │   │
│  │         │                                                   │   │
│  │         ▼                                                   │   │
│  │  ┌───────────────────────────────────────────────────┐    │   │
│  │  │  State Machine (SCANNING → ARMED → WINDOW_OPEN)  │    │   │
│  │  └───────────────────────────────────────────────────┘    │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│                              ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              Risk Management Layer                          │   │
│  │  Kelly sizing, portfolio risk limits, kill switches,       │   │
│  │  drawdown protection, spread filters                        │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│                              ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                Execution Layer                               │   │
│  │  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐      │   │
│  │  │ Paper    │  │ OANDA        │  │ Trade Log        │      │   │
│  │  │ Broker   │  │ Broker       │  │ (SQLite)         │      │   │
│  │  └──────────┘  └──────────────┘  └──────────────────┘      │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │           Testing & Research Layer                          │   │
│  │  ┌──────────────┐  ┌────────────┐  ┌──────────────────┐    │   │
│  │  │ Backtesting  │  │ Forward   │  │ Phase Research   │    │   │
│  │  │ Engine       │  │ Shadow    │  │ (S1-S5 reports)  │    │   │
│  │  └──────────────┘  └────────────┘  └──────────────────┘    │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/STATUS.md](docs/STATUS.md) | Current operational state and live metrics |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Detailed system architecture and component design |
| [docs/STRATEGIES.md](docs/STRATEGIES.md) | All strategy variants (Raw, D1, D2) with performance |
| [docs/RESEARCH.md](docs/RESEARCH.md) | Research methodology, phases S1-S5, findings |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Server deployment guide and systemd services |
| [docs/DATA_FLOW.md](docs/DATA_FLOW.md) | End-to-end data pipeline documentation |
| [docs/forward_shadow_donchian.md](docs/forward_shadow_donchian.md) | Forward shadow runner reference |
| [docs/forward_shadow_dashboard.md](docs/forward_shadow_dashboard.md) | Dashboard configuration guide |
| [docs/DEPLOYMENT_CHECKLIST.md](docs/DEPLOYMENT_CHECKLIST.md) | Pre-deployment verification checklist |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution guidelines |

---

## Running the System

### Live/Paper Trading (Main Orchestrator)

```bash
# Start the orchestrator (paper mode by default)
python scripts/run_live.py

# The orchestrator runs the full pipeline:
# Data → Features → Models → Signals → Risk → Execution
```

### Dashboard

```bash
# Start the Streamlit dashboard
python scripts/run_dashboard.py
# Default: http://127.0.0.1:8501
```

### Backtesting

```bash
# Run standard backtest
python scripts/run_backtest.py

# Run Donchian research backtest
python scripts/donchian_research_runner.py --market-db aurum1/data/backtest_market_cache.sqlite3

# Run Donchian diagnostics
python scripts/donchian_diagnostics.py
python scripts/donchian_next_diagnostics.py
```

### Forward Shadow Testing (Research)

The forward shadow runs a locked strategy alongside live market data without executing trades.

```bash
# Initialize shadow ledger
python scripts/forward_shadow_donchian.py init

# Run one-time update
python scripts/forward_shadow_donchian.py run-once --start-date 2026-05-01T00:00:00Z

# Run continuous service
python scripts/forward_shadow_donchian.py service --start-date 2026-05-01T00:00:00Z

# Check status
python scripts/forward_shadow_donchian.py status

# Generate weekly report
python scripts/forward_shadow_donchian.py weekly-report
```

### D1/D2 Shadow Variants

```bash
# Run D1 filtered shadow journal
python scripts/run_phase_s5_d1_shadow_forward_journal.py

# Run D2 simulation (1R exit + session/vol filter)
python scripts/forward_shadow_donchian_d2.py

# With JSON output for monitoring
python scripts/forward_shadow_donchian_d2.py --json
```

### Phase Research Reports

```bash
python scripts/run_phase_s1_forward_shadow_failure_audit.py
python scripts/run_phase_s2_shadow_context_filter_simulation.py
python scripts/run_phase_s3_candidate_filter_shadow_replay.py
python scripts/run_phase_s4_shadow_decision_candidate_lock.py
python scripts/run_phase_s5_d1_shadow_forward_journal.py
```

---

## Strategy Variants

| Variant | Entry | Exit | Filters | Trades | WR | PF |
|---------|-------|------|---------|--------|----|----|
| **Raw Donchian 2R** | Price > 20-bar high | Fixed 2R | None | 34 | 23.5% | 0.61 |
| **D1 (1R + filter)** | Price > 20-bar high | Fixed 1R | No high vol, no London | 36 closed | 52.8% | 1.24 |
| **D2 (1R + filter)** | Price > 20-bar high | Fixed 1R | No high vol, no London | 543 sim | **57.6%** | **1.33** |

See [docs/STRATEGIES.md](docs/STRATEGIES.md) for full details.

---

## Project Structure

```
aurum1/                           # Main application package
├── __init__.py
├── orchestrator.py               # Main trading loop coordinator
├── instruments.py                # Instrument specifications (XAU/USD)
├── backtesting/                  # Backtest engine
│   ├── engine.py                 # Event-driven backtest core
│   ├── monte_carlo.py            # Monte Carlo simulation
│   ├── walk_forward.py           # Walk-forward analysis
│   ├── report.py                 # Report generation
│   └── ablation.py               # Ablation testing
├── data/
│   ├── ingestion.py              # OANDA, FRED, Alpha Vantage, CFTC fetchers
│   ├── aurum1.sqlite3            # Main trade log (gitignored)
│   ├── forward_shadow_market_cache.sqlite3  # Shadow market cache (gitignored)
│   └── backtest_market_cache.sqlite3        # Backtest cache (gitignored)
├── execution/
│   ├── engine.py                 # Execution engine wrapper
│   └── broker.py                 # PaperBroker + OandaBroker
├── features/
│   └── engineer.py               # Feature engineering pipeline
├── models/
│   ├── direction_predictor.py    # ML direction prediction
│   ├── regime_classifier.py      # Market regime classification
│   ├── ensemble.py               # Signal ensemble combiner
│   ├── sentiment_model.py        # News sentiment scoring
│   ├── retrainer.py              # Weekly model retraining
│   ├── utils.py                  # Model serialization
│   └── ablation.py               # Model ablation testing
├── reports/                      # Phase research report generators
│   ├── phase_s1_forward_shadow_failure_audit.py
│   ├── phase_s2_shadow_context_filter_simulation.py
│   ├── phase_s3_candidate_filter_shadow_replay.py
│   ├── phase_s4_shadow_decision_candidate_lock.py
│   └── phase_s5_d1_shadow_forward_journal.py
├── risk/
│   └── manager.py                # Kelly sizing, kill switches, spread filters
└── signals/
    ├── state_machine.py          # Scanning → Armed → Window Open states
    └── __init__.py               # MachineMode, MachineState enums

scripts/                          # Run scripts and research tools
├── forward_shadow_donchian.py    # Raw Donchian 2R forward shadow runner
├── forward_shadow_donchian_d2.py # D2 filtered 1R variant
├── donchian_research_runner.py   # Donchian signal generation
├── research_edge_prototypes.py   # Alternative entry prototypes
├── run_live.py                   # Start the main orchestrator
├── run_dashboard.py              # Start Streamlit dashboard
├── run_backtest.py               # Run backtest engine
├── run_phase_*.py               # Phase research launchers
├── audit_market_cache.py         # Market cache data audit
├── analyze_mfe_mae.py           # MFE/MAE analysis
├── analyze_trade_lifecycle.py   # Trade lifecycle analysis
├── analyze_signal_forward_returns.py  # Forward return analysis

deploy/                           # Systemd service templates
├── aurum1.service.template
├── dashboard.service.template
├── forward-shadow.service.template
├── forward-shadow-backup.service.template
├── forward-shadow-weekly-report.service.template
└── logrotate/

docs/                             # Documentation
├── ARCHITECTURE.md
├── STATUS.md
├── STRATEGIES.md
├── RESEARCH.md
├── DEPLOYMENT.md
├── DATA_FLOW.md
├── forward_shadow_donchian.md
├── forward_shadow_dashboard.md
└── DEPLOYMENT_CHECKLIST.md

tests/                            # Test suite
├── test_forward_shadow_donchian.py
├── test_forward_shadow_*.py
└── ...
```

---

## Safety & Interlocks

The system has multiple safety layers to prevent accidental live trading:

1. **ALLOW_OANDA_ORDERS** — Must be explicitly set to `true` to enable broker orders
2. **ALLOW_LIVE_TRADING** — Must be `true` for live capital deployment
3. **Forward shadow** — Fails closed if either env var is enabled
4. **OANDA_ENV** — Must be `practice` even for shadow (never `live`)
5. **Risk manager** — Daily loss kill, total drawdown kill, spread filters, position size limits

```bash
# Safe defaults (never change unless deliberately testing live execution)
ALLOW_OANDA_ORDERS=false
ALLOW_LIVE_TRADING=false
OANDA_ENV=practice
```

---

## Prerequisites

- **Python 3.12** (strictly required for the forward shadow runner)
- **OANDA API key** (practice account) — market data and broker
- **FRED API key** (optional) — macro-economic data
- **Alpha Vantage API key** (optional) — news sentiment

---

## License

Private — AURUM-1 Trading System. All rights reserved.
