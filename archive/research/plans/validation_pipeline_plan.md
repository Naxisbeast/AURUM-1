# Plan: Validation Pipeline + Folder Restructure + ML Improvements

## Goals
1. **Experiment validation pipeline** — systematically test every strategy change against baseline with statistical rigor
2. **Harsher test environments** — stress tests, adverse conditions, more data
3. **ML model improvements** — better data, avoid overfitting, meta-labeling
4. **Improved backtesting** — faster, more thorough, more robust
5. **Clean folder structure** — organized, documented, maintainable
6. **Everything in research folder** — single source of truth for experimentation

---

## Phase 1: Folder Restructure (half day)

### 1.1 Clean project root
```
BEFORE:                          AFTER:
aurum1/                          aurum1/           (unchanged core)
scripts/                         scripts/          (production only)
tests/                           tests/            (reorganized)
reports/                         reports/          (unchanged)
research/                        research/         (expanded)
experiments/                     ← NEW
docs/                            docs/             (unchanged)
main.py                          main.py
README.md                        README.md
pytest.ini                       pytest.ini
requirements.txt                 requirements.txt
analyze_main_db.py               → scripts/analyze_main_db.py
analyze_paper_db.py              → scripts/analyze_paper_db.py
analyze_trades_log.py            → scripts/analyze_trades_log.py
2005Thapelo$#@!*                  → .ssh/ (gitignored)
^                                → delete
.pytest_* caches                 → .gitignore adds these
```

### 1.2 Organize scripts into subdirectories
```
scripts/
  ├── paper_trading/             # Live paper trader scripts
  │   ├── d4_paper_trader.py
  │   ├── d4_safety_check.py
  │   └── live_comparator.py
  ├── backtesting/                # Backtest scripts
  │   ├── run_backtest.py
  │   ├── run_monte_carlo.py
  │   ├── run_20bar_walk_forward.py
  │   ├── run_55bar_walk_forward.py
  │   ├── run_risk_sensitivity.py
  │   └── run_icir_decay_analysis.py
  ├── shadow/                     # Shadow mode / forward testing
  │   ├── forward_shadow_donchian*.py  (D1-D6)
  │   └── phase_s*_run.py
  ├── research/                   # One-off research scripts
  │   ├── research_edge_prototypes.py
  │   ├── research_d3_sell_signals.py
  │   ├── donchian_diagnostics.py
  │   └── analyze_mfe_mae.py
  ├── data/                       # Data management
  │   ├── fetch_oanda_history.py
  │   ├── audit_market_cache.py
  │   └── archive_runtime_db.py
  ├── ml/                         # ML training
  │   └── train_ml_models.py
  ├── dash/                       # Dashboard
  └── utils/                      # Analysis scripts
      ├── analyze_main_db.py
      ├── analyze_paper_db.py
      ├── analyze_trades_log.py
      └── d4_deploy_11yr.py
```

### 1.3 Reorganize tests
```
tests/
  ├── __init__.py
  ├── unit/                       # Existing unit tests
  │   ├── test_phase1_ingestion.py
  │   ├── test_phase2_features.py
  │   ├── test_phase3_models.py
  │   ├── test_phase4_signals.py
  │   ├── test_phase5_risk.py
  │   ├── test_phase6_execution.py
  │   ├── test_phase7_backtest.py
  │   ├── test_phase8_monitor.py
  │   ├── test_phase9_orchestrator.py
  │   └── test_phase11_history.py
  ├── integration/                # NEW: integration tests
  │   ├── test_validation_pipeline.py
  │   ├── test_stress_tests.py
  │   └── test_multi_strategy.py
  └── conftest.py                 # Shared fixtures
```

---

## Phase 2: Validation Pipeline (3-4 days)

### 2.1 Core Architecture

```
experiments/
  ├── __init__.py
  ├── runner.py                   # Run a full experiment
  ├── tracker.py                  # Track results in SQLite database
  ├── compare.py                  # Compare vs baseline with statistics
  ├── stress_test.py              # Run under harsh conditions
  ├── models.py                   # Data models (dataclasses)
  ├── baseline.py                 # Baseline D4 result (loaded once)
  └── results/                    # DB storage location (gitignored)
```

### 2.2 ExperimentRunner class

```python
class ExperimentRunner:
    """Run a strategy change and validate it against baseline."""
    
    def run(change_config: ExperimentConfig) -> ExperimentResult:
        """Full pipeline:
        1. Run 11-year backtest with change
        2. Run walk-forward validation
        3. Run Monte Carlo simulation
        4. Run stress tests
        5. Compare vs baseline
        6. Compute statistical significance
        7. Save to experiment database
        8. Return comprehensive result
        """
```

### 2.3 Experiment Tracking Database

SQLite database at `experiments/results/experiments.db` with tables:

```sql
CREATE TABLE experiments (
    id TEXT PRIMARY KEY,              -- UUID
    name TEXT NOT NULL,               -- Human-readable name
    description TEXT,
    category TEXT,                    -- 'entry', 'exit', 'risk', 'ml', 'hybrid'
    config_json TEXT,                 -- Full configuration as JSON
    created_at TEXT NOT NULL,
    parent_experiment_id TEXT,        -- For variant tracking
    status TEXT DEFAULT 'completed'   -- 'running', 'completed', 'failed'
);

CREATE TABLE metrics (
    experiment_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,        -- 'profit_factor', 'sharpe', etc.
    baseline_value REAL,              -- D4 baseline
    experiment_value REAL,            -- New value
    absolute_change REAL,             -- experiment - baseline
    relative_change REAL,             -- (experiment - baseline) / baseline
    p_value REAL,                     -- Statistical significance
    is_significant INTEGER,           -- p < 0.05
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);

CREATE TABLE walk_forward_windows (
    experiment_id TEXT NOT NULL,
    window_index INTEGER,
    profit_factor REAL,
    sharpe REAL,
    win_rate REAL,
    max_drawdown REAL,
    net_pnl REAL,
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);

CREATE TABLE stress_tests (
    experiment_id TEXT NOT NULL,
    test_name TEXT NOT NULL,          -- '2x_spread', '3x_slippage', 'high_vol', etc.
    profit_factor REAL,
    sharpe REAL,
    max_drawdown REAL,
    net_pnl REAL,
    passed INTEGER,                   -- 1 = survived, 0 = failed
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);
```

### 2.4 Baseline D4 Result (Run Once)

Run the full 11-year D4 backtest once and store as baseline:

| Metric | Value |
|--------|-------|
| Profit Factor | 1.14 |
| Sharpe | 0.85 |
| Total PnL | +$42,678 |
| Max DD | 15.3% |
| Win Rate | 40% |
| Trades | 8,175 |
| Avg R | +0.20 |

### 2.5 Experiment Report Output

For each experiment, generate:

```
experiment: chandelier_exit_m2.5
category: exit
status: PASSED

METRICS                        BASELINE    NEW     CHANGE    p-value   SIGNIFICANT
Profit Factor                  1.14        1.38    +21.1%    0.003     ✅ YES
Sharpe                         0.85        1.12    +31.8%    0.001     ✅ YES
Win Rate                       40.0%       48.5%   +8.5pp    0.002     ✅ YES
Max Drawdown                   15.3%       11.2%   -26.8%    0.008     ✅ YES
Total PnL                      $42,678     $56,210 +31.7%     0.004     ✅ YES

WALK-FORWARD (20-bar windows)
  Positive windows: 88.9% → 92.3%
  Mean PF: 1.14 → 1.32
  Mean Sharpe: 1.27 → 1.45

STRESS TESTS
  2x Spread:      PF=1.18 ✅  (baseline: PF=0.98)
  3x Slippage:    PF=1.12 ✅  (baseline: PF=0.95)
  High Vol 2011:  PF=1.30 ✅  (baseline: PF=1.10)
  Bad Stretch:    PF=1.05 ✅  (baseline: PF=0.92)

MONTE CARLO (10,000 sims)
  Ruin prob: 0.0% → 0.0%
  Median DD: 8.2% → 6.1%
  95th %ile DD: 18.1% → 13.4%

VERDICT: ✅ Passes all gates — recommended for deployment
```

### 2.6 Stress Test Conditions

| Test | Condition | Why |
|------|-----------|-----|
| 2x Spread | 3.0 pips instead of 1.5 | High-volatility periods |
| 3x Slippage | 1.5 pips std instead of 0.5 | Fast markets |
| 2x Costs | Both spread and slippage doubled | Worst-case scenario |
| High Vol Regime | Only 2008, 2011, 2020, 2022 periods | Crisis periods |
| Low Vol Regime | Only 2017, 2023 periods | Dull market performance |
| Bad Stretch | Only worst 20% of market conditions | Stress test |
| Random Entry | Replace signals with random (same count) | Does the change add edge? |

### 2.7 Decision Gates

For every experiment:
```
GATE 1: PF improvement > 0.05         [  ]
GATE 2: Sharpe improvement > 0.08     [  ]
GATE 3: No DD increase > 2pp          [  ]
GATE 4: Walk-forward PF up in ≥60%    [  ]
GATE 5: Survives 2x cost stress       [  ]
GATE 6: p < 0.05 on permutation test  [  ]
GATE 7: MC ruin probability < 1%      [  ]

PASSED: 6/7 gates required
```

---

## Phase 3: ML Improvement Pipeline (3-5 days)

### 3.1 More Training Data

Current ML:
- RegimeClassifier: trained on rolling 252 days
- DirectionPredictor: trained on last 2 years

**Improvements:**
```yaml
# New expanded training:
regime:
  train_window: 504 days     # 2× more data (was 252)
  retrain_days: 30           # More frequent (was weekly)
  ensemble: True             # 3 models with different random seeds
  
direction_predictor:
  train_window: 3 years      # More history (was 2 years)
  features_extended: True    # Include new features
```

### 3.2 Meta-Labeling (New Feature)

Train a LightGBM classifier that predicts whether a breakout signal will succeed:

```
FEATURES FOR META-LABELER:
  ├── ATR percentile (100-bar)
  ├── ADX value
  ├── Breakout distance (as % of ATR)
  ├── EMA alignment score
  ├── Session (London/NY/Asia)
  ├── Day of week
  ├── DXY regime (rising/falling/sideways)
  ├── Real yield trend
  ├── Recent 10-trade win rate
  └── Consecutive signals in last 20 bars

LABEL: 1 if trade had positive R-multiple, else 0

USAGE: Only take signals where P(success) > 40%
```

### 3.3 Feature Expansion

New features to add to FeatureEngineer:
```python
# New features to implement:
features['atr_percentile'] = atr.rolling(100).apply(last_rank_percentile)
features['yang_zhang_vol'] = yz_estimator(open, high, low, close)
features['breakout_distance'] = (close - donchian_upper) / atr
features['dxy_regime'] = bin_dxy_trend(dxy_returns)
features['turn_of_month'] = is_turn_of_month(index)
features['efficiency_ratio'] = kaufman_efficiency(close, 10)
```

Each feature added with proper lookahead checks and causality verification.

### 3.4 Overfitting Prevention

```python
class OverfittingPrevention:
    """Multiple mechanisms to prevent ML overfitting."""
    
    # 1. Deflated Sharpe Ratio (DSR)
    # Adjusts Sharpe for number of trials conducted
    dsr = adjusted_sharpe(num_trials=len(experiment_db))
    
    # 2. Combinatorially Symmetric Cross-Validation (CSCV)
    # Tests model on all possible train/test splits
    cscv_score = combinatorial_cross_validation(features, labels, n_splits=10)
    
    # 3. Feature importance stability
    # Track feature importances across retraining — stable = real
    feature_stability = correlation_matrix(retrained_importances)
    
    # 4. Purged walk-forward
    # No data leakage between train/test windows
    purged_wf = walk_forward(purge_gap=10)
```

---

## Phase 4: Backtesting Infrastructure Improvements (2-3 days)

### 4.1 Faster Backtests

```python
# Current: ~30 seconds for 11 years
# Target: ~10 seconds using vectorization + caching

# 1. Feature caching: build features once, cache to disk
# 2. Parallel walk-forward: run windows in parallel
# 3. Result caching: same config → same result (memoization)
```

### 4.2 Richer Walk-Forward

Current walk-forward checks basic metrics. New version adds:

```python
@dataclass
class EnhancedWalkForwardResult:
    window_results: list[WindowResult]
    
    # Stability metrics
    pf_stability: float           # 1 - std(pf) / mean(pf)
    sharpe_stability: float
    rank_correlation: float       # Kendall tau of window order
    
    # Degradation detection
    pf_trend_slope: float         # Is PF degrading over time?
    sharpe_trend_slope: float
    
    # Purged metrics (no data leakage)
    purged_sharpe: float
    
    # Minimum requirements
    passes_minimum: bool          # PF > 1.0 in all windows
    passes_robustness: bool       # PF > 1.10 in 60%+ windows
```

### 4.3 Multi-Asset Testing (Optional)

Extend backtesting to test on additional instruments:
- XAG/USD (Silver) — correlated but different behavior
- EUR/USD — different dynamics
- BTC/USD — high volatility, different structure

This validates that improvements are not gold-specific overfitting.

---

## Deliverables

| # | What | Files |
|---|------|-------|
| 1 | Clean folder structure | `.gitignore`, `scripts/` reorg, `tests/` reorg |
| 2 | Validation pipeline | `experiments/runner.py`, `tracker.py`, `compare.py`, `stress_test.py` |
| 3 | Experiment DB | `experiments/results/experiments.db` |
| 4 | ML improvements | `aurum1/models/meta_labeler.py`, feature expansion |
| 5 | Enhanced backtesting | `aurum1/backtesting/validation.py`, walk-forward improvements |
| 6 | Research docs | `research/` updated with pipeline docs |
| 7 | First experiment: Chandelier Exit | Validated result with full report |

## Estimated Timeline

```
Day 1:    Folder restructure + .gitignore cleanup
Day 2-3:  Validation pipeline core (runner + tracker + compare)
Day 4:    Baseline D4 run + experiment DB
Day 5:    Stress tests + decision gates
Day 6-7:  ML improvements (meta-labeler + features)
Day 8:    Backtesting improvements
Day 9-10: Run first batch of experiments
Day 11:   Documentation + research folder update
```
