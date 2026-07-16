# Research Library — Master Index

## Strategic Edge

| Document | Description | Priority |
|----------|-------------|----------|
| [Edge Analysis](01_strategy_math/edge_analysis.md) | Where does the strategy's edge come from? Mathematical derivation | ⭐ MUST-READ |
| [Donchian Breakout Math](01_strategy_math/donchian_breakout_math.md) | Breakout mechanics, probability, and dynamics | Required |
| [ATR Derivation](01_strategy_math/atr_derivation.md) | True Range, ATR, statistical properties | Reference |
| [Kelly Criterion](01_strategy_math/kelly_criterion.md) | Optimal sizing, fractional Kelly, edge preservation | Required |

## Entry Improvements

| Document | Impact | Complexity | Priority |
|----------|--------|------------|----------|
| [Volatility Compression Filter](02_entry_improvements/volatility_compression_filter.md) | HIGH | LOW | ⭐ IMPLEMENT FIRST |
| [Pullback Entry Hybrid](02_entry_improvements/pullback_entry_hybrid.md) | HIGH | MED | ⭐ HIGH PRIORITY |
| [MTF Confirmation](02_entry_improvements/mtf_confirmation.md) | MED-HIGH | LOW | ⭐ HIGH PRIORITY |
| [OBV Divergence Filter](02_entry_improvements/obv_divergence_filter.md) | MEDIUM | LOW | Phase 2 |

## Exit Optimization

| Document | Impact | Complexity | Priority |
|----------|--------|------------|----------|
| [Chandelier Exit](03_exit_optimization/chandelier_exit.md) | HIGH | MED | ⭐ IMPLEMENT EARLY |
| [Partial TP + Trail](03_exit_optimization/partial_tp_trail.md) | HIGH | MED | ⭐ HIGH PRIORITY |
| [Time-Based Exit](03_exit_optimization/time_based_exit.md) | MEDIUM | LOW | Phase 2 |
| [Breakeven Timing](03_exit_optimization/breakeven_timing.md) | MEDIUM | LOW | Phase 1 |

## Risk & Sizing

| Document | Impact | Complexity | Priority |
|----------|--------|------------|----------|
| [Volatility Scaling](04_risk_sizing/volatility_scaling.md) | MEDIUM | LOW | Phase 1 |
| [Anti-Martingale](04_risk_sizing/anti_martingale.md) | MEDIUM | LOW | Phase 2 |
| [Regime-Dependent Kelly](04_risk_sizing/regime_dependent_kelly.md) | MEDIUM | LOW | Phase 1 |
| [Portfolio Risk Framework](04_risk_sizing/portfolio_risk_framework.md) | MEDIUM | MED | Phase 3 |

## Regime Detection

| Document | Impact | Complexity | Priority |
|----------|--------|------------|----------|
| [ADX Regime Classification](05_regime_detection/adx_regime_classification.md) | MEDIUM | LOW | ⭐ TRY FIRST |
| [CUSUM Change Detection](05_regime_detection/cusum_change_detection.md) | MEDIUM | MED | Phase 2 |
| [HMM Regime Switching](05_regime_detection/hmm_regime_switching.md) | MEDIUM | HIGH | Phase 3 |
| [Seasonality Patterns](05_regime_detection/seasonality_patterns.md) | LOW-MED | LOW | Phase 2 |

## Feature Engineering

| Document | Impact | Complexity | Priority |
|----------|--------|------------|----------|
| [Yang-Zhang Volatility](06_feature_engineering/yang_zhang_volatility.md) | MEDIUM | LOW | Phase 1 |
| [Intermarket Features](06_feature_engineering/intermarket_features.md) | MEDIUM | LOW | Phase 1 |
| [Micro Structure Features](06_feature_engineering/micro_structure_features.md) | MEDIUM | LOW | Phase 2 |

## Hybrid Architectures

| Document | Impact | Complexity | Priority |
|----------|--------|------------|----------|
| [Meta-Labeling](07_hybrid_architectures/meta_labeling.md) | HIGH | MED | Phase 2 |
| [Regime-Switching Strategy](07_hybrid_architectures/regime_switching_strategy.md) | HIGH | MED-HIGH | Phase 3 |
| [Signal Fusion](07_hybrid_architectures/signal_fusion.md) | MED-HIGH | MED | Phase 3 |
| [Multi-Timeframe Framework](07_hybrid_architectures/multi_timeframe_framework.md) | MED-HIGH | LOW-MED | Phase 2 |

## Robustness & Testing

| Document | Purpose | Priority |
|----------|---------|----------|
| [Validation Pipeline](08_robustness_testing/validation_pipeline.md) | Complete testing framework for changes | ⭐ REQUIRED READING |
| [Monte Carlo Methodology](08_robustness_testing/monte_carlo_methodology.md) | Risk of ruin, drawdown estimation | Reference |
| [Walk-Forward Methodology](08_robustness_testing/walk_forward_methodology.md) | Out-of-sample validation | Required reading |
| [Statistical Significance Tests](08_robustness_testing/statistical_significance_tests.md) | Is the edge real? | Required reading |
| [Backtest Overfitting Prevention](08_robustness_testing/backtest_overfitting_prevention.md) | Deflation ratio, DSC, combinatorially symmetric cross-validation | Required reading |

## Implementation Planning

| Document | Purpose |
|----------|---------|
| [Phased Roadmap](11_implementation_order/phased_roadmap.md) | Build order, dependencies, timeline |
| [A/B Testing Framework](10_experiment_design/ab_testing_framework.md) | How to test each change |
| [Performance Metrics](10_experiment_design/performance_metrics.md) | Complete metric definitions |
| [Conflict Matrix](10_experiment_design/conflict_matrix.md) | Compatibility between changes |
