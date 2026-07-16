#!/usr/bin/env python3
"""Run a strategy experiment through the full validation pipeline.

Usage:
    python scripts/run_experiment.py --name "my_change" --category "exit" \\
        --override '{"signals": {"exit_mode": "CHANDELIER", "chandelier_multiplier": 2.5}}'

    python scripts/run_experiment.py --list  # List past experiments
    python scripts/run_experiment.py --id abc123  # Show specific experiment
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.runner import ExperimentRunner
from experiments.models import ExperimentConfig
from experiments.tracker import ExperimentTracker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AURUM-1 experiment runner")
    parser.add_argument("--name", type=str, default="unnamed_experiment", help="Experiment name")
    parser.add_argument("--description", type=str, default="", help="Human-readable description")
    parser.add_argument("--category", type=str, default="other",
                        choices=["entry", "exit", "risk", "ml", "hybrid", "feature", "other"],
                        help="Change category")
    parser.add_argument("--override", type=str, default="{}", help="JSON settings overrides")
    parser.add_argument("--parent", type=str, default=None, help="Parent experiment ID")
    parser.add_argument("--tag", type=str, action="append", default=[], help="Tag to add")

    # Query mode
    parser.add_argument("--list", action="store_true", help="List past experiments")
    parser.add_argument("--id", type=str, default=None, help="Show specific experiment details")

    args = parser.parse_args(argv)
    tracker = ExperimentTracker()

    # Query mode
    if args.list:
        print(tracker.summary_table())
        return 0

    if args.id:
        exp = tracker.get_experiment(args.id)
        if exp is None:
            print(f"Experiment {args.id} not found.")
            return 1
        print(f"\nExperiment: {exp['name']} ({exp['id']})")
        print(f"  Category: {exp['category']}  |  Status: {exp['status']}")
        print(f"  PF: {exp['profit_factor']:.3f}  |  Sharpe: {exp['sharpe']:.3f}  |  "
              f"DD: {exp['max_drawdown']:.1%}  |  PnL: ${exp['total_net_pnl']:+.0f}")
        print(f"  Gates: {exp['gates_passed']}/7  |  Trades: {exp['trade_count']}")
        print(f"  Created: {exp['created_at']}")
        if exp.get("metrics"):
            print("\n  Metrics:")
            for m in exp["metrics"]:
                sig = "✅" if m["is_significant"] else "❌"
                print(f"    {m['metric_name']:<20} {m['baseline_value']:>8.3f} → "
                      f"{m['experiment_value']:>8.3f} ({m['relative_change']:>+6.1%}) {sig}")
        if exp.get("stress_tests"):
            print("\n  Stress Tests:")
            for s in exp["stress_tests"]:
                mark = "✅" if s["passed"] else "❌"
                print(f"    {mark} {s['test_name']:<16} PF={s['profit_factor']:.3f}")
        return 0

    # Run experiment
    config = ExperimentConfig(
        name=args.name,
        description=args.description or args.name,
        category=args.category,
        settings_overrides=json.loads(args.override),
        parent_experiment_id=args.parent,
        tags=args.tag,
    )

    runner = ExperimentRunner()
    result = runner.run(config)

    # Save
    tracker.save_result(result)

    print()
    print(result.report())

    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
