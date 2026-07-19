# Phased Implementation Roadmap

## Overview

This roadmap organizes all strategy improvements into **3 phases** based on impact/complexity ratio. Phase 1 is the highest-ROI work — simple changes with large impact.

---

## Phase 1: Quick Wins (2-3 weeks)

These are low-complexity, high-impact improvements. Implement first, validate, then deploy.

### Week 1: Entry & Filter Improvements

| Day | Task | Files Affected | Expected Impact |
|-----|------|---------------|-----------------|
| 1 | **Volatility compression filter** | `scripts/forward_shadow_donchian_d4.py` + backtest engine | PF 1.14 → ~1.25 |
| 2 | H1 trend hard gate | State machine entry logic | PF +0.03-0.05 |
| 3 | Time-based max-hold exit (96-192 bars) | PaperBroker + backtest engine | DD reduction |
| 4 | Testing & validation | Compare against D4 baseline | — |

**Commit:** `feat: add volatility compression filter, H1 trend gate, time-based exit`

### Week 2: Exit Optimization

| Day | Task | Files Affected | Expected Impact |
|-----|------|---------------|-----------------|
| 1-2 | **Chandelier Exit** class + integration | New `research/chandelier_exit.py`, modify `execution/broker.py` | PF 1.14 → ~1.35 |
| 3 | Breakeven stop timing | Add to exit manager | WR +2-3pp |
| 4 | Testing & validation | Full backtest comparison | — |

**Commit:** `feat: replace fixed 2R with Chandelier adaptive trailing stop`

### Week 3: Risk & Sizing

| Day | Task | Files Affected | Expected Impact |
|-----|------|---------------|-----------------|
| 1 | **Volatility scaling** for position sizing | `risk/manager.py` | Sharpe +0.05-0.10 |
| 2 | ADX regime-dependent Kelly | `risk/manager.py` | DD -2-3pp |
| 3 | Testing & validation | Walk-forward + Monte Carlo | — |

**Commit:** `feat: volatility scaling and ADX-regime dependent position sizing`

---

## Phase 2: Structural Improvements (4-6 weeks)

These are medium-complexity changes that require new components or significant refactoring.

### Week 4-5: Partial Exit + Meta-Labeling

| Task | Complexity | Expected Impact |
|------|------------|-----------------|
| Partial TP at 1R + trail remainder | Medium | PF 1.35 → 1.45 |
| Meta-labeling ML model | Medium | WR 40% → 48-50% |
| Feature engineering for meta-labeler | Medium | Model accuracy |

### Week 6-7: New Features

| Task | Complexity | Priority |
|------|------------|----------|
| ATR percentile (100-bar) | Low | High |
| Yang-Zhang volatility estimator | Low | High |
| Breakout distance as % of ATR | Low | High |
| DXY regime binning | Low | Medium |
| Turn-of-month seasonal flag | Low | Medium |

---

## Phase 3: Advanced (6-8 weeks)

### Week 8-10: Regime-Switching

| Task | Complexity | Expected Impact |
|------|------------|-----------------|
| ADX-optimized regime detection | Medium | Better regime accuracy |
| Ranging mode strategy | High | PF improvement in sideways markets |
| Mode transition management | High | Smooth switching |
| CUSUM change-point detection | Medium | Faster regime identification |

### Week 11-13: Full Integration

| Task | Description |
|------|-------------|
| Combined model testing | All Phase 1+2 improvements together |
| Walk-forward validation | 20-bar + 55-bar windows |
| Monte Carlo simulation | 10,000 runs for risk assessment |
| Live paper trading | Deploy to D4 paper trader |
| Monitoring & tuning | Track live vs backtest drift |

---

## Decision Gates

Before proceeding to the next phase, each phase must pass these gates:

### Phase 1 Gate ✅
- [ ] Volatility compression filter improves PF by ≥ 0.05
- [ ] Chandelier exit improves PF by ≥ 0.10
- [ ] All three exit optimizations tested (Chandelier, Partial, Time-based)
- [ ] Volatility scaling improves Sharpe by ≥ 0.05
- [ ] ADX-Kelly sizing reduces DD by ≥ 2pp
- [ ] All changes validated on 11-year backtest
- [ ] Walk-forward shows consistent improvement
- [ ] No regression on any metric (PF, Sharpe, DD, WR)

### Phase 2 Gate ✅
- [ ] Meta-labeling accuracy > 0.55 on test set
- [ ] Partial exit improves PF by ≥ 0.05 on top of Phase 1
- [ ] Meta-labeling improves PF by ≥ 0.05 on top of Phase 1
- [ ] New features correlate with trade outcomes
- [ ] No metric regression

### Phase 3 Gate ✅
- [ ] ADX regime detection accurate > 70% (against visual labels)
- [ ] Regime-switching outperforms single-strategy on all regimes
- [ ] CUSUM reduces regime detection lag by ≥ 5 bars vs ADX-only
- [ ] Complete strategy validated on 11-year + walk-forward + Monte Carlo
- [ ] Live paper trading for 30+ days with positive Sharpe

---

## Estimated Timeline

```
Phase 1: 3 weeks ───→ Gate ───→ Phase 2: 4-6 weeks ───→ Gate ───→ Phase 3: 6-8 weeks
                                                                                  │
                                                                                  ↓
                                                                          Production D4 v2.0
                                                                          (13-17 weeks total)
```

## Risk Assessment

| Risk | Probability | Mitigation |
|------|-------------|------------|
| Changes interact negatively | Medium | Test combinations incrementally |
| Overfitting to backtest | Medium | Walk-forward + Monte Carlo for every change |
| Meta-labeling adds complexity without benefit | Medium | A/B test before deployment |
| Regime-switching introduces whipsaw | Low | Hysteresis band prevents frequent switching |
| Live performance differs from backtest | Low | Forward cache testing before paper trading |
