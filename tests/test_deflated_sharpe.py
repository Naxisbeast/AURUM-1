"""Tests for the Deflated Sharpe Ratio implementation.

Validates against the canonical paper example: N=1000 pure-noise trials,
expected max Sharpe ≈ 3.26 under the null (Bailey & López de Prado 2014).
"""

from __future__ import annotations

import numpy as np
import pytest

from aurum1.research.deflated_sharpe import (
    expected_max_sharpe,
    effective_n,
    deflated_sharpe_ratio,
)


class TestExpectedMaxSharpe:
    """Expected maximum Sharpe under the null."""

    def test_returns_zero_for_single_trial(self):
        assert expected_max_sharpe(np.array([0.5])) == 0.0

    def test_returns_zero_for_empty(self):
        assert expected_max_sharpe(np.array([])) == 0.0

    def test_canonical_n1000_example(self):
        """Known case: 1000 pure-noise trials → E[max SR] ≈ 3.26."""
        rng = np.random.RandomState(42)
        trials = rng.normal(0, 1, 1000)
        result = expected_max_sharpe(trials)
        # Should be in the ballpark of 3.26 (varies slightly with RNG seed)
        assert 2.5 < result < 4.5, f"Expected ~3.26, got {result:.4f}"

    def test_increases_with_more_trials(self):
        rng = np.random.RandomState(42)
        small = expected_max_sharpe(rng.normal(0, 1, 10))
        large = expected_max_sharpe(rng.normal(0, 1, 100))
        assert large > small


class TestEffectiveN:
    """Effective independent trials correction."""

    def test_perfectly_correlated(self):
        """If all trials have ρ=1, effective N should be 1."""
        assert effective_n(10, 1.0) == pytest.approx(1.0)

    def test_perfectly_uncorrelated(self):
        """If all trials have ρ=0, effective N should equal M."""
        assert effective_n(10, 0.0) == pytest.approx(10.0)

    def test_partially_correlated(self):
        """With ρ=0.5 and M=10, effective N should be 5.5."""
        assert effective_n(10, 0.5) == pytest.approx(5.5)


class TestDeflatedSharpeRatio:
    """Full DSR computation."""

    def test_returns_zero_for_too_few_samples(self):
        result = deflated_sharpe_ratio(
            np.array([0.1, 0.2]),
            np.array([0.1, 0.2, 0.3]),
        )
        assert result == 0.0

    def test_higher_for_stronger_candidate(self):
        """A clearly superior candidate should have higher DSR."""
        rng = np.random.RandomState(42)
        # Weak trials: noisy, low Sharpe
        trial_sharpes = rng.normal(0, 0.5, 50)
        # Strong candidate returns
        strong_returns = rng.normal(0.01, 0.02, 100)
        weak_returns = rng.normal(0.001, 0.02, 100)

        strong_dsr = deflated_sharpe_ratio(strong_returns, trial_sharpes)
        weak_dsr = deflated_sharpe_ratio(weak_returns, trial_sharpes)
        assert strong_dsr >= weak_dsr

    def test_known_distribution_produces_expected_range(self):
        """With a clear edge and few trials, DSR should be confidently high."""
        rng = np.random.RandomState(42)
        # Wide trial pool with moderate variance
        trial_sharpes = rng.normal(0, 0.3, 7)  # D1-D7 style
        trial_sharpes[3] = 1.27  # D4 is the standout

        # Generate returns that produce ~1.27 Sharpe
        sr_target = 1.27
        n = 100
        vol = 0.02
        mean_req = sr_target * vol
        candidate = rng.normal(mean_req, vol, n)

        dsr = deflated_sharpe_ratio(candidate, trial_sharpes)
        # Should be reasonably confident
        assert dsr > 0.5, f"Expected >0.5, got {dsr:.4f}"
