# AURUM-1 System Architecture

## Overview

AURUM-1 follows a layered architecture with strict dependency rules: each layer only calls the layer below it. The orchestrator (`aurum1/orchestrator.py`) wires all layers together.

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Orchestrator Layer                              │
│  Coordinates data → features → models → signals → risk → execution │
│  Manages threads, health endpoint, retraining scheduler             │
└───────────────────────┬─────────────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────────────┐
│                     Execution Layer                                 │
│  ┌──────────────────────┐  ┌───────────────────────────────────┐    │
│  │    ExecutionEngine   │  │         BrokerBase                │    │
│  │  Routes orders to    │  │  ┌──────────┐  ┌──────────────┐  │    │
│  │  broker, logs trades │  │  │Paper     │  │OandaBroker   │  │    │
│  └──────────────────────┘  │  │Broker    │  │(OANDA v20)   │  │    │
│                            │  └──────────┘  └──────────────┘  │    │
│                            └───────────────────────────────────┘    │
└───────────────────────┬─────────────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────────────┐
│                     Risk Layer                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                     RiskManager                              │    │
│  │  • Kelly optimal sizing                                      │    │
│  │  • Portfolio risk limits (max 3%)                            │    │
│  │  • Daily loss kill switch (-3%)                              │    │
│  │  • Total drawdown kill switch (-8%)                          │    │
│  │  • Spread filters (>3 pips reject)                           │    │
│  │  • Recovery mode (halve risk during drawdown)                │    │
│  │  • Regime-conflict detection                                 │    │
│  └─────────────────────────────────────────────────────────────┘    │
└───────────────────────┬─────────────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────────────┐
│                   Signal Layer                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    StateMachine                              │    │
│  │                                                              │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐   │    │
│  │  │SCANNING  │→ │  ARMED   │→ │WINDOW_OPE│→ │   TRADE    │   │    │
│  │  │          │  │          │  │N         │  │            │   │    │
│  │  └──────────┘  └──────────┘  └──────────┘  └────────────┘   │    │
│  │                                                              │    │
│  │  SCANNING: Direction signal + ADX > 25 + EMA alignment      │    │
│  │  ARMED:    Wait for pullback (1-4 candles)   │              │    │
│  │  WINDOW:   Wait for breakout past armed high                │    │
│  └─────────────────────────────────────────────────────────────┘    │
└───────────────────────┬─────────────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────────────┐
│                   Model / Signal Layer                              │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐     │    │
│  │  │   Regime     │  │  Direction   │  │   Sentiment    │     │    │
│  │  │  Classifier  │  │  Predictor   │  │   Scorer       │     │    │
│  │  │  (TRENDING)  │  │  (RNN/linear)│  │  (NLP)         │     │    │
│  │  └──────────────┘  └──────────────┘  └────────────────┘     │    │
│  │         │                 │                  │               │    │
│  │         ▼                 ▼                  ▼               │    │
│  │  ┌──────────────────────────────────────────────────────┐    │    │
│  │  │                 Ensemble Signal                       │    │    │
│  │  │  Combines: direction × regime × sentiment            │    │    │
│  │  └──────────────────────────────────────────────────────┘    │    │
│  └─────────────────────────────────────────────────────────────┘    │
└───────────────────────┬─────────────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────────────┐
│                   Feature Layer                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                  FeatureEngineer                             │    │
│  │  OHLCV → ATR, ADX, EMA, BB, MACD, RSI, Volume              │    │
│  │  Macro → DXY, VIX, Real Yield, CPI                          │    │
│  │  COT   → Net Long/Short, Open Interest                      │    │
│  │  Time  → Session labels, Cyclical encoding (sin/cos)        │    │
│  └─────────────────────────────────────────────────────────────┘    │
└───────────────────────┬─────────────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────────────┐
│                   Data Ingestion Layer                              │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                 AurumDataIngestor                            │    │
│  │                                                              │    │
│  │  OANDA API ──→ OHLCV data (M5, M15, H1, H4, D1)            │    │
│  │  FRED API  ──→ Macro (DGS10, CPI)                           │    │
│  │  Yahoo Fin. ──→ DXY, VIX                                     │    │
│  │  Alpha Vant.──→ News headlines                               │    │
│  │  CFTC site ──→ COT data                                      │    │
│  │  Investing.c──→ Economic calendar / blackout events          │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Research Layer (Parallel System)

The research system is independent of the main trading pipeline:

```
┌──────────────────────────────────────────────────────────────────┐
│                  Forward Shadow Testing                          │
│                                                                  │
│  OANDA API ──→ Market Cache ──→ forward_shadow_donchian.py      │
│                                      │                          │
│                                      ▼                          │
│                              donchian_shadow.sqlite3             │
│                              (signals, trades, equity curve)     │
│                                      │                          │
│                                      ▼                          │
│         Phase Reports (S1-S5) ──→ Research Findings             │
│                                      │                          │
│         ┌────────────────────────────┼──────────────────────┐   │
│         ▼                            ▼                      ▼   │
│   Raw Donchian 2R              D1 (1R+filter)        D2 (1R+filter)
│   (continuous service)         (15-min timer)        (15-min timer)
└──────────────────────────────────────────────────────────────────┘
```

---

## Storage Architecture

```
aurum1/data/
├── aurum1.sqlite3                     Main trade log & performance
├── forward_shadow_market_cache.sqlite3  OANDA market data for shadow
└── backtest_market_cache.sqlite3        OANDA market data for backtest

reports/
├── forward_shadow/
│   ├── donchian_shadow.sqlite3         Shadow ledger (97 signals, 34 trades)
│   ├── donchian_d2_shadow.sqlite3      D2 shadow ledger
│   ├── phase_s*.csv                    Phase research CSVs
│   ├── phase_s*.json                   Phase research summaries
│   └── donchian_shadow_weekly_*.json   Weekly performance reports
├── backtest_execution_*.sqlite3        Isolated backtest DBs
└── research/                           Research JSON/CSV outputs

backups/forward_shadow/                 Daily SQLite backups (28 days retained)
```

---

## Safety Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                     Safety Interlocks                         │
│                                                                │
│  ALLOW_OANDA_ORDERS=false (env var)                            │
│       ↓                                                        │
│  ALLOW_LIVE_TRADING=false (env var)                            │
│       ↓                                                        │
│  OANDA_ENV=practice (never live)                               │
│       ↓                                                        │
│  Forward shadow: fails closed if either env is incorrectly set │
│       ↓                                                        │
│  Risk Manager: kill switches (daily loss, drawdown)           │
│       ↓                                                        │
│  PaperBroker: in-memory only, no external connectivity         │
└────────────────────────────────────────────────────────────────┘
```

---

## Threading Model

The orchestrator runs on the main thread with three background threads:

| Thread | Purpose | Interval |
|--------|---------|----------|
| Main | M15 candle processing loop | Every 15 min (aligned to candle close) |
| macro-refresh | Fetch macro data | Every 60 min |
| sentiment-refresh | Fetch news | Every 30 min |
| retraining | Check if weekly retraining due | Every 60 sec |
| health | Flask HTTP health endpoint | Continuous |
