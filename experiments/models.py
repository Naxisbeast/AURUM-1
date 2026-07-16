"""Data models for the experiment validation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ExperimentConfig:
    """Configuration for a single experiment (one strategy change)."""

    name: str
    description: str
    category: str  # 'entry', 'exit', 'risk', 'ml', 'hybrid', 'feature'
    settings_overrides: dict[str, Any] = field(default_factory=dict)
    parent_experiment_id: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class MetricComparison:
    """One metric compared against baseline."""

    metric_name: str
    baseline_value: float
    experiment_value: float
    absolute_change: float
    relative_change: float
    p_value: float
    is_significant: bool

    @property
    def passed(self) -> bool:
        return self.is_significant and self.absolute_change > 0.0


@dataclass
class WalkForwardMetrics:
    """Aggregated walk-forward statistics."""

    window_count: int
    positive_window_rate: float
    mean_profit_factor: float
    mean_sharpe: float
    mean_win_rate: float
    mean_max_drawdown: float
    std_profit_factor: float
    std_sharpe: float
    pf_stability: float  # 1 - std/mean
    pf_trend_slope: float  # degradation detection
    windows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StressTestResult:
    """Results from a single stress test."""

    test_name: str
    profit_factor: float
    sharpe: float
    max_drawdown: float
    net_pnl: float
    win_rate: float
    trade_count: int
    passed: bool  # survived without catastrophic failure


@dataclass
class MonteCarloResult:
    """Results from Monte Carlo simulation."""

    n_simulations: int
    median_final_equity: float
    pct5_final_equity: float
    pct95_final_equity: float
    median_max_drawdown: float
    pct95_max_drawdown: float
    median_sharpe: float
    ruin_probability: float
    passed: bool


@dataclass
class ExperimentResult:
    """Complete result of one experiment."""

    experiment_id: str
    config: ExperimentConfig
    created_at: str
    status: str  # 'passed', 'failed', 'error'
    duration_seconds: float

    # Core metrics
    trade_count: int = 0
    profit_factor: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_net_pnl: float = 0.0
    avg_r: float = 0.0

    # Comparisons
    metric_comparisons: list[MetricComparison] = field(default_factory=list)
    walk_forward: WalkForwardMetrics | None = None
    stress_tests: list[StressTestResult] = field(default_factory=list)
    monte_carlo: MonteCarloResult | None = None

    # Decision
    gates_passed: int = 0
    gates_total: int = 7
    gate_details: dict[str, bool] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "passed" and self.gates_passed >= (self.gates_total - 1)

    def report(self) -> str:
        """Generate a human-readable report."""
        lines = [
            "=" * 68,
            f"EXPERIMENT: {self.config.name}",
            f"Category: {self.config.category}  |  Status: {self.status.upper()}",
            f"Description: {self.config.description}",
            f"Duration: {self.duration_seconds:.1f}s  |  Trades: {self.trade_count}",
            "",
            "METRICS                     BASELINE    NEW     CHANGE   p-val   SIG",
            "-" * 68,
        ]
        for mc in self.metric_comparisons:
            sig = "✅" if mc.is_significant else "❌"
            lines.append(
                f"  {mc.metric_name:<26} {mc.baseline_value:>8.3f}  "
                f"{mc.experiment_value:>8.3f}  {mc.relative_change:>+6.1%}  "
                f"{mc.p_value:.3f}  {sig}"
            )

        if self.walk_forward:
            wf = self.walk_forward
            lines += [
                "",
                "WALK-FORWARD",
                f"  Windows: {wf.window_count}  |  Positive: {wf.positive_window_rate:.0%}",
                f"  Mean PF: {wf.mean_profit_factor:.3f} ({'▲' if wf.mean_profit_factor > 1.14 else '▼'})  |  "
                f"Mean Sharpe: {wf.mean_sharpe:.3f}",
                f"  Stability: {wf.pf_stability:.1%}  |  "
                f"Trend: {wf.pf_trend_slope:+.4f}/window",
            ]

        if self.stress_tests:
            lines += ["", "STRESS TESTS"]
            for st in self.stress_tests:
                mark = "✅" if st.passed else "❌"
                lines.append(f"  {mark} {st.test_name:<20} PF={st.profit_factor:.3f}  "
                            f"Sharpe={st.sharpe:.2f}  DD={st.max_drawdown:.1%}  "
                            f"PnL={st.net_pnl:+.0f}")

        if self.monte_carlo:
            mc = self.monte_carlo
            mark = "✅" if mc.passed else "❌"
            lines += [
                "",
                "MONTE CARLO (10,000 sims)",
                f"  {mark} Ruin: {mc.ruin_probability:.1%}  |  "
                f"Median DD: {mc.median_max_drawdown:.1%}  |  "
                f"95th DD: {mc.pct95_max_drawdown:.1%}",
            ]

        lines += [
            "",
            "GATES",
            f"  Passed: {self.gates_passed}/{self.gates_total}",
        ]
        for gate_name, passed in self.gate_details.items():
            mark = "✅" if passed else "❌"
            lines.append(f"  {mark} {gate_name}")

        lines += ["=" * 68]
        return "\n".join(lines)
