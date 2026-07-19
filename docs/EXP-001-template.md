# EXP-001 — Experiment Framework Template

Every strategy change in AURUM-1 must pass the **Four-Question Gate**:
1. Does it improve **performance**?
2. Does it improve **reliability**?
3. Does it improve **explainability**?
4. Does it **reduce uncertainty**?

If none of the above → reject.

For changes that pass the gate, use this template to formalize the proposal.

---

## Experiment Proposal

```yaml
exp_id: EXP-001            # auto-incrementing
title: "<One-line title>"
author: "<name>"
created: "<YYYY-MM-DD>"
status: "proposed"          # proposed → running → passed | rejected
```

### Hypothesis
> In one sentence, what do you expect to happen and why?

### Motivation
> Which of the four gates does this satisfy? What problem does it solve?

### Change Description
> Exactly what code/config change is being made? Link to relevant files and line numbers.

### Test Protocol
| Parameter | Value |
|-----------|-------|
| Data period | <e.g. 2016-2026, 236K M15 candles> |
| Lookback | <e.g. 20 bars> |
| Exit mode | <e.g. FIXED 2R> |
| Directions | <BUY / SELL / BUY+SELL> |
| Transaction costs | <spread, slippage model> |
| Risk per trade | <% equity> |
| Benchmark | <e.g. D4 baseline at same period> |

### Success Criteria
> Quantitative criteria that determine pass/fail. Be specific:
> - Profit factor improvement: ≥ X%
> - Sharpe ratio: ≥ X
> - Walk-forward positive windows: ≥ X%
> - Monte Carlo ruin: 0%
> - Drawdown not significantly worse than baseline
> - TC stress test survives baseline + 1 stress level

### Results

```json
{
  "benchmark_pf": null,
  "experiment_pf": null,
  "benchmark_sharpe": null,
  "experiment_sharpe": null,
  "benchmark_win_rate": null,
  "experiment_win_rate": null,
  "benchmark_max_dd": null,
  "experiment_max_dd": null,
  "walk_forward_positive": null,
  "monte_carlo_ruin": null,
  "verdict": ""
}
```

### Verdict
> **PASSED** or **REJECTED**. If rejected, state which success criterion failed.
> If passed, specify promotion gate (e.g. "shadow only for N weeks" or "production after 24h monitoring").

### Notes
> Any additional observations, edge cases, or follow-up experiments suggested.

---

## Execution Steps

1. [ ] Code change implemented (branch: `exp/EXP-XXX-short-name`)
2. [ ] Unit tests pass
3. [ ] Backtest run against full 11-year dataset
4. [ ] Walk-forward validation (18 windows)
5. [ ] Monte Carlo simulation (10,000 runs)
6. [ ] TC stress test
7. [ ] Risk sensitivity check
8. [ ] Results documented in EXP file
9. [ ] Peer review (if applicable)
10. [ ] Decision recorded

---

## Experiment Log

| EXP | Title | Author | Created | Status | Verdict |
|-----|-------|--------|---------|--------|---------|
| EXP-000 | Risk configuration (0.25% → 0.35%) | AURUM Team | 2026-07-18 | ✅ Production | Passed hardening v1.0 Phases 0-2 |
| EXP-001 | (template — replace with your title) | | | 🔲 Proposed | |
