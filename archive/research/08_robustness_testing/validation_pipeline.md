# Validation Pipeline: How to Test Changes

## Overview

The validation pipeline (`experiments/` module) provides a systematic way to test every strategy change against the D4 baseline with statistical rigor.

## Quick Start

```bash
# Run an experiment
python scripts/run_experiment.py \
    --name "vol_compression_filter" \
    --category "entry" \
    --description "Only take breakouts when ATR is compressed" \
    --override '{"signals": {"vol_compression": true}}'

# List all experiments
python scripts/run_experiment.py --list

# See a specific result
python scripts/run_experiment.py --id abc123
```

## What the Pipeline Does

For every experiment, it automatically:

### 1. Full 11-Year Backtest
Runs the complete backtest with the settings override applied, exactly the same way the D4 baseline was computed (same data, same engine, same costs).

### 2. Walk-Forward Validation
Splits the 11-year data into 20 non-overlapping windows. Each window: train on 6 months, test on 3 months. Reports:
- How many windows were profitable (positive net PnL)
- Mean PF across windows
- PF stability (low variance = more robust)
- PF trend (if PF is degrading over time, that's bad)

### 3. Stress Tests
Tests the strategy under harsh conditions:
- **2x Spread**: Doubles spread (from 1.5 to 3.0 pips)
- **3x Slippage**: Triples slippage std (from 0.5 to 1.5 pips)
- **2x Costs**: Both doubled simultaneously
- **High Vol Regime**: Restricted to crisis years (2008, 2011, 2020, 2022)
- **Low Vol Regime**: Restricted to quiet years (2017, 2023)
- **Random Control**: Replaces signals with random entries — validates edge is real

### 4. Monte Carlo Simulation
Shuffles the realized trade outcomes 10,000 times to estimate:
- Probability of ruin (equity dropping below 50% of starting)
- Distribution of max drawdown
- Distribution of final equity

### 5. Statistical Comparison
Compares every metric against the D4 baseline:
- Permutation test for p-values (doesn't assume normality)
- Deflated Sharpe adjustment for multiple testing correction
- Bootstrap confidence intervals

### 6. Decision Gates
A change must pass 6/7 gates:
| Gate | Condition |
|------|-----------|
| G1 | PF improvement > 0.05 |
| G2 | Sharpe improvement > 0.08 |
| G3 | No DD increase > 2pp |
| G4 | Walk-forward PF up in 60%+ windows |
| G5 | Survives 2x cost stress |
| G6 | p < 0.05 on key metrics |
| G7 | Monte Carlo ruin < 1% |

## Experiment Output

Each experiment saves to `experiments/results/experiments.db` with full details:
- All metrics with baseline comparison
- Per-window walk-forward results
- Per-stress-test results
- Monte Carlo summary
- Gate pass/fail details

You can view results at any time with:
```bash
python scripts/run_experiment.py --id <experiment_id>
python scripts/run_experiment.py --list
```

## Adding a New Experiment

```python
from experiments.runner import ExperimentRunner
from experiments.models import ExperimentConfig

# 1. Define the change
config = ExperimentConfig(
    name="chandelier_exit_m2.5",
    description="Replace fixed 2R with Chandelier trail at 2.5x ATR",
    category="exit",
    settings_overrides={
        "signals": {"exit_mode": "CHANDELIER", "chandelier_multiplier": 2.5},
        "risk": {"risk_per_trade_pct": 0.0025},
    }
)

# 2. Run through pipeline
runner = ExperimentRunner()
result = runner.run(config)

# 3. See the report
print(result.report())
print(f"Passed: {result.passed}")
'''
