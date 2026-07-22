"""Deflated Sharpe Ratio (DSR) — correction for selection bias and non-normality.

Implements Bailey & López de Prado (2014):

    The Deflated Sharpe Ratio: Correcting for Selection Bias,
    Backtest Overfitting, and Non-Normality.

The DSR adjusts the Sharpe ratio of a selected strategy for:
1. The number of trials that were tested to find it
2. Non-normality of returns (skew and kurtosis)

Usage at the 100-trade gate:
    import numpy as np
    from aurum1.research.deflated_sharpe import deflated_sharpe_ratio

    # Live trade R-multiples from D4
    live_r = np.array([...])   # 100 trade outcomes

    # Unannualized Sharpe ratios from all historical trials
    trial_sharpes = np.array([...])  # D1-D7 walk-forward Sharpes

    dsr = deflated_sharpe_ratio(live_r, trial_sharpes)
    print(f"DSR = {dsr:.3f}")   # ≥0.95 is the standard confidence bar
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm


EULER_GAMMA = 0.5772156649015328606


def expected_max_sharpe(sr_trials: np.ndarray) -> float:
    """Compute the deflated benchmark SR₀.

    The expected maximum Sharpe ratio from N independent trials under the
    null hypothesis that the true Sharpe of every trial is zero. A strategy's
    observed Sharpe must beat this benchmark to be considered non-spurious.

    Parameters
    ----------
    sr_trials : np.ndarray
        Unannualized Sharpe ratios from ALL trials (including the candidate).

    Returns
    -------
    float
        The deflated benchmark. The candidate's adjusted Sharpe must exceed
        this to clear the selection-bias correction.
    """
    n = len(sr_trials)
    if n < 2:
        return 0.0
    var_sr = float(np.var(sr_trials, ddof=1))
    z1 = float(norm.ppf(1 - 1.0 / n))
    z2 = float(norm.ppf(1 - 1.0 / (n * np.e)))
    return np.sqrt(var_sr) * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def effective_n(m_trials: int, avg_pairwise_corr: float) -> float:
    """Adjust raw trial count for correlation among variants.

    Parameters
    ----------
    m_trials : int
        Raw number of trials run.
    avg_pairwise_corr : float
        Average pairwise correlation between trial return series.

    Returns
    -------
    float
        Effective number of independent trials.
    """
    return avg_pairwise_corr + (1 - avg_pairwise_corr) * m_trials


def deflated_sharpe_ratio(
    candidate_returns: np.ndarray,
    sr_trials: np.ndarray,
) -> float:
    """Compute the Deflated Sharpe Ratio for a selected strategy.

    Parameters
    ----------
    candidate_returns : np.ndarray
        The winning variant's per-trade or per-window returns (unannualized).
    sr_trials : np.ndarray
        Unannualized Sharpe ratios from ALL trials, including the candidate.

    Returns
    -------
    float
        The Deflated Sharpe Ratio — probability (0 to 1) that the true Sharpe
        exceeds the selection-bias-adjusted benchmark. Convention: ≥0.95 is
        the statistical confidence bar.

    Notes
    -----
    - candidate_returns must be the same observation type (per-trade or
      per-window) for every trial in sr_trials. Do NOT mix per-trade and
      per-window returns in the same DSR computation.
    - Kurtosis must be RAW kurtosis, not excess. Use:
        scipy.stats.kurtosis(series, fisher=False)
    """
    from scipy.stats import skew, kurtosis

    n = len(candidate_returns)
    if n < 3:
        return 0.0

    sr_hat = float(np.mean(candidate_returns) / np.std(candidate_returns, ddof=1))
    g3 = float(skew(candidate_returns))
    g4 = float(kurtosis(candidate_returns, fisher=False))  # raw kurtosis

    sr0 = expected_max_sharpe(sr_trials)

    denom = float(np.sqrt(1.0 - g3 * sr_hat + ((g4 - 1.0) / 4.0) * sr_hat**2))
    if denom <= 0.0:
        return 0.0

    z = float((sr_hat - sr0) * np.sqrt(n - 1) / denom)
    return float(norm.cdf(z))
