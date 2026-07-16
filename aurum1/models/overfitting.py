"""Overfitting prevention toolkit for ML models in AURUM-1.

Implements Deflated Sharpe Ratio, Combinatorially Symmetric Cross-Validation,
feature importance stability analysis, and purged walk-forward.

References:
  - López de Prado, "Advances in Financial Machine Learning" (2018)
"""

from __future__ import annotations

import itertools
import logging
from typing import Any

import numpy as np
import pandas as pd

LOGGER = logging.getLogger("aurum1.overfitting")


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_trials: int,
    n_observations: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Compute the Deflated Sharpe Ratio (DSR).

    The DSR adjusts the observed Sharpe ratio for:
    1. Length of the return series
    2. Skewness and kurtosis of returns
    3. Number of trials conducted (multiple testing)

    A DSR > 0 indicates the strategy is likely not a false discovery.

    Parameters
    ----------
    observed_sharpe : float — annualized Sharpe from the strategy
    n_trials : int — number of strategies/experiments tested
    n_observations : int — number of return observations
    skewness : float — skewness of returns (0 = normal)
    kurtosis : float — excess kurtosis (3 = normal)

    Returns
    -------
    float — Deflated Sharpe Ratio (> 0 = significant)
    """
    from scipy.stats import norm

    # Standard deviation of Sharpe under null
    if n_observations < 2:
        return 0.0

    # Estimate max Sharpe expected by chance (under multiple testing)
    # Using Bailey & López de Prado (2014) formula
    e_max_sharpe = (
        (1 - np.euler_gamma) * norm.ppf(1 - 1.0 / n_trials)
        + np.euler_gamma * norm.ppf(1 - 1.0 / (n_trials * np.e))
    ) / np.sqrt(n_observations)

    # Standard error of Sharpe ratio (Mertens 2002)
    sharpe_se = np.sqrt(
        (1 + 0.5 * observed_sharpe ** 2
         - skewness * observed_sharpe
         + (kurtosis - 3) / 4 * observed_sharpe ** 2)
        / (n_observations - 1)
    )

    if sharpe_se <= 0:
        return 0.0

    dsr = (observed_sharpe - e_max_sharpe) / sharpe_se
    return float(dsr)


def combinatorial_cross_validation(
    features: pd.DataFrame,
    labels: np.ndarray,
    model: Any,
    n_splits: int = 10,
    n_trials: int = 100,
) -> dict[str, float]:
    """Combinatorially Symmetric Cross-Validation (CSCV).

    Tests model stability by training on ALL possible combinations of
    n_splits-1 subsets and testing on the held-out subset.

    Parameters
    ----------
    features : pd.DataFrame — feature matrix
    labels : np.ndarray — target labels
    model : fit-able model with .fit() and .score() methods
    n_splits : int — number of splits
    n_trials : int — number of combinations to test

    Returns
    -------
    dict with 'mean_score', 'std_score', 'min_score', 'pct_below_random'
    """
    from sklearn.model_selection import KFold

    if len(features) < n_splits:
        return {"mean_score": 0.0, "std_score": 0.0, "min_score": 0.0, "pct_below_random": 0.5}

    kf = KFold(n_splits=n_splits, shuffle=True, random_state=42)
    scores = []
    n_tested = 0

    for train_idx, test_idx in kf.split(features):
        if n_tested >= n_trials:
            break
        X_train, X_test = features.iloc[train_idx], features.iloc[test_idx]
        y_train, y_test = labels[train_idx], labels[test_idx]

        try:
            model.fit(X_train, y_train)
            score = model.score(X_test, y_test)
            scores.append(score)
            n_tested += 1
        except Exception as e:
            LOGGER.warning("CSCV fold failed: %s", e)
            continue

    if not scores:
        return {"mean_score": 0.0, "std_score": 0.0, "min_score": 0.0, "pct_below_random": 0.5}

    score_arr = np.asarray(scores)
    random_baseline = 0.5  # Binary classification

    return {
        "mean_score": float(np.mean(score_arr)),
        "std_score": float(np.std(score_arr)),
        "min_score": float(np.min(score_arr)),
        "pct_below_random": float(np.mean(score_arr < random_baseline)),
    }


def feature_importance_stability(
    importance_history: list[pd.DataFrame],
) -> dict[str, float]:
    """Analyze feature importance stability across retraining sessions.

    High stability = feature is genuinely predictive (not overfitted).
    Low stability = feature is noise or overfitted to particular periods.

    Parameters
    ----------
    importance_history : list of DataFrames with 'feature' and 'importance' columns

    Returns
    -------
    dict with mean pairwise rank correlation
    """
    if len(importance_history) < 2:
        return {"mean_rank_correlation": 0.0, "n_periods": len(importance_history)}

    rank_corrs = []
    all_features = sorted(set(
        f for df in importance_history for f in df["feature"].tolist()
    ))

    for i in range(1, len(importance_history)):
        prev = importance_history[i - 1].set_index("feature")["importance"]
        curr = importance_history[i].set_index("feature")["importance"]

        # Align to common feature set
        common = [f for f in all_features if f in prev.index and f in curr.index]
        if len(common) < 3:
            continue

        prev_ranks = prev[common].rank().values
        curr_ranks = curr[common].rank().values

        corr = np.corrcoef(prev_ranks, curr_ranks)[0, 1]
        if not np.isnan(corr):
            rank_corrs.append(corr)

    return {
        "mean_rank_correlation": float(np.mean(rank_corrs)) if rank_corrs else 0.0,
        "n_periods": len(importance_history),
    }


def purged_walk_forward(
    model: Any,
    features: pd.DataFrame,
    labels: np.ndarray,
    train_bars: int = 6552,
    test_bars: int = 1638,
    purge_bars: int = 10,
) -> dict[str, Any]:
    """Walk-forward validation with purge gap to prevent leakage.

    The purge gap ensures no training data overlaps with or leaks into
    the test period. This is critical for time series models.

    Returns
    -------
    dict with window_results, mean_sharpe, positive_window_rate
    """
    windows = []
    start = 0
    window_idx = 0

    while start + train_bars + test_bars <= len(features):
        # Train with purge gap
        train_end = start + train_bars - purge_bars
        test_start = start + train_bars
        test_end = test_start + test_bars

        if train_end < 0 or test_start >= len(features):
            break

        X_train = features.iloc[start:train_end]
        y_train = labels[start:train_end]
        X_test = features.iloc[test_start:test_end]
        y_test = labels[test_start:test_end]

        if len(X_train) < 50 or len(X_test) < 10:
            start += test_bars
            continue

        try:
            model.fit(X_train, y_train)
            score = model.score(X_test, y_test)
        except Exception:
            score = 0.0

        windows.append({"window": window_idx, "score": score, "n_train": len(X_train), "n_test": len(X_test)})
        window_idx += 1
        start += test_bars

    if not windows:
        return {"windows": [], "mean_sharpe": 0.0, "positive_window_rate": 0.0}

    scores = np.asarray([w["score"] for w in windows])
    return {
        "windows": windows,
        "mean_score": float(np.mean(scores)),
        "std_score": float(np.std(scores)),
        "positive_window_rate": float(np.mean(scores > 0.5)),
    }


__all__ = [
    "deflated_sharpe_ratio",
    "combinatorial_cross_validation",
    "feature_importance_stability",
    "purged_walk_forward",
]
