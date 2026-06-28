# AURUM-1 Data Flow

## End-to-End Pipeline

```
                    ┌──────────────┐
                    │  OANDA API   │
                    │  (practice)  │
                    └──────┬───────┘
                           │
                           ▼
                ┌──────────────────────┐
                │  AurumDataIngestor   │
                │  fetch_ohlcv()       │
                └──────┬───────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
  ┌─────────────┐ ┌──────────┐ ┌──────────┐
  │ Main DB     │ │ Shadow  │ │ Backtest │
  │ aurum1.sql  │ │ Cache   │ │ Cache    │
  │ ite3        │ │ SQLite  │ │ SQLite   │
  │ (trades     │ │         │ │          │
  │  history)   │ │         │ │          │
  └─────────────┘ └────┬─────┘ └──────────┘
                        │
              ┌─────────┴─────────┐
              │                   │
              ▼                   ▼
  ┌──────────────────┐  ┌──────────────────┐
  │ forward_shadow   │  │ donchian         │
  │ _donchian.py     │  │ _research_runner │
  │ (continuous      │  │ .py              │
  │  service)        │  │ (run-once)       │
  │                  │  │                  │
  │ Reads M15        │  │ Reads M15        │
  │ candles from     │  │ candles from     │
  │ shadow cache     │  │ backtest cache   │
  └────────┬─────────┘  └────────┬─────────┘
           │                     │
           ▼                     ▼
  ┌──────────────────┐  ┌──────────────────┐
  │ donchian_shadow  │  │ Backtest Result  │
  │ .sqlite3         │  │ (SQLite + JSON)  │
  │                  │  │                  │
  │ 97 signals       │  │ Isolated per-run │
  │ 34 trades        │  │ database         │
  │ equity curve     │  │                  │
  └────────┬─────────┘  └──────────────────┘
           │
           ▼
  ┌──────────────────────────────────────┐
  │  Phase Reports (S1-S5)              │
  │                                      │
  │  S1: Failure audit (32 trades)      │
  │  S2: Context filter simulation      │
  │  S3: Candidate replay (97 signals)  │
  │  S4: Candidate lock decision        │
  │  S5: D1 forward journal (ongoing)   │
  └──────────────────────────────────────┘
           │
           ▼
  ┌──────────────────────────────────────┐
  │  Research Findings                   │
  │  → Strategy improvements            │
  │  → Rule changes                    │
  │  → New variant deployment (D2)      │
  └──────────────────────────────────────┘
```

---

## Main Pipeline (Orchestrator)

```
Every M15 candle close:

1. fetch_ohlcv("M15", count=3)       → OANDA API
2. Append candle to OHLCV buffer      → In-memory DataFrame
3. Build features                     → FeatureEngineer
4. Predict regime                     → RegimeClassifier (ML/fallback)
5. Predict direction                  → DirectionPredictor (ML/fallback)
6. Score sentiment                    → SentimentScorer
7. Ensemble signals                  → EnsembleSignal.combine()
8. State machine                     → StateMachine.on_candle()
9. If instruction emitted:
   a. Evaluate risk                  → RiskManager.evaluate()
   b. Execute order                  → ExecutionEngine.execute()
   c. Log trade                      → trades_log table
10. Log equity snapshot              → performance_log table
11. Check weekly retraining          → optional
```

---

## Shadow Pipeline

```
Every 60 seconds (forward-shadow service):

1. fetch_ohlcv_range("M15", ...)     → OANDA API → market cache
2. Load candles from cache            → safe_load_ohlcv()
3. Build research features            → build_research_features()
4. Generate Donchian signals          → donchian_signals()
5. Simulate trades:
   a. Check open position for exit    → maybe_close_position()
   b. Process new signals             → enter if no position open
   c. Compute P&L with slippage/spread
6. Write to SQLite:
   - shadow_signals
   - shadow_trades
   - shadow_equity_curve
   - shadow_candles
   - shadow_audit_snapshots
```

---

## D1/D2 Shadow Pipeline

```
Every 15 minutes (timer):

D1: 1. Read shadow signals from donchian_shadow.sqlite3
    2. Apply D1 decision filter:
       - HOLD if volatility == high
       - HOLD if session == london
       - TAKE otherwise
    3. Simulate fixed 1R exit from candle data
    4. Write to phase_s5_d1_shadow_journal.csv

D2: 1. Read M15 candles from market cache
    2. Generate Donchian signals
    3. Apply same D1 filter (vol/session)
    4. Simulate 1R exit with full P&L
    5. Print JSON summary (for journalctl)
```

---

## Data Files Reference

| File | Type | Contents |
|------|------|----------|
| `aurum1/data/aurum1.sqlite3` | SQLite | Trade history, performance log (gitignored) |
| `aurum1/data/forward_shadow_market_cache.sqlite3` | SQLite | M15, H1, H4, macro data for shadow |
| `aurum1/data/backtest_market_cache.sqlite3` | SQLite | M15, H1, H4, macro data for backtest |
| `reports/forward_shadow/donchian_shadow.sqlite3` | SQLite | Shadow signals, trades, equity curve |
| `reports/forward_shadow/phase_s5_d1_shadow_journal.csv` | CSV | D1 filtered journal entries |
| `reports/forward_shadow/donchian_shadow_weekly_*.json` | JSON | Weekly performance reports |
| `reports/backtest_execution_*.sqlite3` | SQLite | Isolated backtest results |
| `backups/forward_shadow/donchian_shadow_*.sqlite3` | SQLite | Daily database backups |
