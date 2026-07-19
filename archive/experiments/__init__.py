"""AURUM-1 Experiment Validation Pipeline.

Every strategy change must go through this pipeline before deployment.
Tests against baseline D4, runs stress scenarios, and validates statistical significance.

Usage:
    from experiments.runner import ExperimentRunner
    from experiments.models import ExperimentConfig

    # Run a single change
    result = ExperimentRunner().run(
        ExperimentConfig(
            name="chandelier_exit_m2.5",
            description="Replace fixed 2R with Chandelier trail at 2.5x ATR",
            category="exit",
            settings_overrides={
                "signals": {"exit_mode": "CHANDELIER", "chandelier_multiplier": 2.5}
            }
        )
    )
    print(result.report())
"""
