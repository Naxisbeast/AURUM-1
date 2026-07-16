# A/B Testing Framework for Strategy Improvements

## 1. Core Principle

Every strategy change must be validated against the **existing D4 baseline** using the 11-year backtest before any live deployment. No change is accepted on theory alone.

## 2. Testing Protocol

### 2.1 Step-by-Step

1. **Isolate the variable** — change exactly one thing at a time
2. **Run the backtest** — same 11-year data, same settings, same market conditions
3. **Compare metrics** — against the D4 baseline
4. **Significance test** — is the difference statistically significant?
5. **Walk-forward test** — does the improvement hold in all windows?
6. **Sensitivity analysis** — does the improvement depend on a specific parameter value?

### 2.2 Minimum Improvement Requirements

| Metric | Minimum Improvement | Notes |
|--------|-------------------|-------|
| Profit Factor | +0.05 | From 1.14 to 1.19+ |
| Sharpe | +0.08 | From 0.85 to 0.93+ |
| Return/DD Ratio | +0.05 | From current value +5% |
| Win Rate | +2 pp | From 40% to 42%+ |
| Net PnL | +5% | From $42,678 to $44,800+ |

**If a change doesn't meet these thresholds, it's not worth deploying.** Theory isn't enough — it must be validated.

## 3. Statistical Significance Testing

### 3.1 Trade-Level Comparison

Use a **one-sided permutation test** on the difference in mean R-multiple:

```python
def permutation_test_improvement(baseline_rs, new_rs, n_permutations=10000):
    """Test if new strategy has significantly higher mean R."""
    observed_diff = np.mean(new_rs) - np.mean(baseline_rs)
    all_rs = np.concatenate([baseline_rs, new_rs])
    n = len(baseline_rs)
    
    perm_diffs = []
    for _ in range(n_permutations):
        np.random.shuffle(all_rs)
        perm_diff = np.mean(all_rs[:n]) - np.mean(all_rs[n:])
        perm_diffs.append(perm_diff)
    
    p_value = np.mean(np.array(perm_diffs) >= observed_diff)
    return observed_diff, p_value
```

**Accept if p < 0.05** (95% confidence the improvement is real).

### 3.2 Walk-Forward Significance

A change must improve PF in ≥ 60% of walk-forward windows to be considered robust.

### 3.3 Multiple Testing Correction

When testing multiple changes simultaneously, apply Bonferroni correction:

$$\alpha_{adjusted} = \frac{0.05}{k}$$

For $k=10$ tests: accept if $p < 0.005$.

## 4. Sensitivity Analysis

For each parameterized change, test the parameter grid:

```python
sensitivity_results = {}

# Example: Chandelier multiplier sensitivity
for multiplier in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
    result = run_backtest(multiplier=multiplier)
    sensitivity_results[multiplier] = {
        'pf': result.profit_factor,
        'sharpe': result.sharpe_ratio,
        'wr': result.win_rate,
        'pnl': result.total_net_pnl,
        'max_dd': result.max_drawdown_pct,
    }
```

**A good parameter is one where performance is stable across a range (the "flat region").** If performance spikes at one specific value, it's likely overfitted.

## 5. A/B Testing Each Recommendation

| Change | Test Design | Success Criteria | Min Trades |
|--------|-------------|------------------|------------|
| Volatility compression filter | Compare D4 +/- compression filter | PF +0.05, WR +2pp | 5,000+ |
| Chandelier exit | Replace 2R TP/SL with Chandelier | PF +0.08, Sharpe +0.10 | 5,000+ |
| Partial TP + trail | Add partial close at 1R | PF +0.06, WR +3pp | 5,000+ |
| H1 trend hard gate | Block trades against H1 trend | PF +0.05, even with fewer trades | 5,000+ |
| Volatility scaling | Swap fixed sizing for vol-scaled | Sharpe +0.05, DD -2pp | 5,000+ |
| ADX-Kelly | Use ADX regime for Kelly multiplier | Sharpe +0.05, DD -2pp | 5,000+ |
| Meta-labeling | ML filter on entry quality | PF +0.10, WR +5pp | 5,000+ |
| Regime-switching | Switch between trend/ranging modes | PF +0.10, Sharpe +0.12 | 5,000+ |

## 6. Combining Changes

Once individual changes are validated, test combinations:

```
Phase 1: Individual testing (2-4 weeks)
Phase 2: Best 2-3 combined (1-2 weeks)
Phase 3: Full system (1-2 weeks)
```

**Changes that pass individually may interact negatively.** Always test combinations before deployment.
