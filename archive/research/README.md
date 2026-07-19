# AURUM-1 Research Library

**Purpose:** Central repository for strategy research, mathematical derivations, academic references, and experimental results for the AURUM-1 XAU/USD M15 trading system.

**Current Best Strategy:** D4 — Donchian 20-bar breakout, BUY+SELL, fixed 2R exit, no filters
- 11-year backtest: 8,175 trades, PF 1.14, +$42,678 PnL
- Walk-forward L20: PF 1.14, Sharpe 1.27, 88.9% positive windows
- Monte Carlo (10k sims): 0% ruin, 99% DD 20.3%

---

## Directory Structure

```
research/
├── README.md                     # This file
├── index.md                      # Master index of all research documents
│
├── 01_strategy_math/             # Core mathematical foundations
│   ├── donchian_breakout_math.md
│   ├── atr_derivation.md
│   ├── kelly_criterion.md
│   └── edge_analysis.md
│
├── 02_entry_improvements/        # Entry signal research
│   ├── volatility_compression_filter.md
│   ├── pullback_entry_hybrid.md
│   ├── mtf_confirmation.md
│   └── obv_divergence_filter.md
│
├── 03_exit_optimization/         # Exit strategy research
│   ├── chandelier_exit.md
│   ├── partial_tp_trail.md
│   ├── time_based_exit.md
│   └── breakeven_timing.md
│
├── 04_risk_sizing/               # Risk management & position sizing
│   ├── volatility_scaling.md
│   ├── anti_martingale.md
│   ├── regime_dependent_kelly.md
│   └── portfolio_risk_framework.md
│
├── 05_regime_detection/          # Market regime & state identification
│   ├── adx_regime_classification.md
│   ├── cusum_change_detection.md
│   ├── hmm_regime_switching.md
│   └── seasonality_patterns.md
│
├── 06_feature_engineering/       # New features & transformations
│   ├── yang_zhang_volatility.md
│   ├── intermarket_features.md
│   └── micro_structure_features.md
│
├── 07_hybrid_architectures/      # Multi-strategy & integration approaches
│   ├── meta_labeling.md
│   ├── regime_switching_strategy.md
│   ├── signal_fusion.md
│   └── multi_timeframe_framework.md
│
├── 08_robustness_testing/        # Validation & statistical significance
│   ├── monte_carlo_methodology.md
│   ├── walk_forward_methodology.md
│   ├── statistical_significance_tests.md
│   └── backtest_overfitting_prevention.md
│
├── 09_academic_references/       # External papers & resources
│   ├── paper_summaries.md
│   └── bibliography.md
│
├── 10_experiment_design/         # How to A/B test changes
│   ├── ab_testing_framework.md
│   ├── performance_metrics.md
│   └── conflict_matrix.md
│
└── 11_implementation_order/      # Prioritized build pipeline
    └── phased_roadmap.md
```

## How To Use This Library

1. **Start with `01_strategy_math/edge_analysis.md`** — understand where the strategy's edge comes from mathematically.
2. **Review each improvement category** — each document contains: mathematical derivation, expected impact, implementation notes, and an A/B testing plan.
3. **Check the `conflict_matrix.md`** before implementing any change to see which improvements are compatible.
4. **Use the `phased_roadmap.md`** to prioritize implementation based on impact/complexity ratio.
5. **Validate every change** using the frameworks in `08_robustness_testing/`.

## Core Principles

1. **Causality first** — every change must be verified as causal (no lookahead).
2. **Statistical significance** — no changes accepted without proper significance testing.
3. **Walk-forward validation** — every change validated on out-of-sample windows.
4. **Edge preservation** — improvements should not destroy existing edge while trying to add new edge.
5. **Interpretability** — prefer transparent mathematical formulations over black-box ML where possible.
