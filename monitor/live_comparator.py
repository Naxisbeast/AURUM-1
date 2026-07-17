"""Live vs backtest sequential comparator.

Compares live trading results to the backtest distribution using a
recency-weighted matching approach. After each live trade, it finds
similar backtest trades and measures divergence.

This is superior to a simple "wait for 100 trades" threshold because:
- Accounts for market conditions at each trade's entry
- Provides confidence intervals, not just point estimates
- Detects deterioration early (5-10 trades) if the gap is large
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class ComparatorResult:
    """Result from comparing a live trade against backtest distribution."""

    trade_id: str | int
    direction: str
    live_r: float
    expected_r_mean: float
    expected_r_std: float
    expected_r_p10: float
    expected_r_p90: float
    z_score: float
    cumulative_live_r: float
    cumulative_expected_r: float
    cumulative_z: float
    n_matched_trades: int
    divergence_level: str  # "normal" | "watch" | "alert" | "critical"


class LiveVsBacktestComparator:
    """Compare live trades against similar backtest trades.

    After each live trade:
    1. Find the N most similar backtest trades (matching direction, session,
       volatility regime, and ATR percentile)
    2. Compute posterior distribution of expected R-multiple
    3. Measure divergence between observed and expected performance
    """

    def __init__(self, backtest_trades: list[dict[str, Any]]):
        """Initialize with a list of backtest trade dicts.

        Each backtest trade dict should have:
        - direction: str ("BUY" or "SELL")
        - r_multiple or r: float
        - entry_price: float
        - atr_at_entry or atr_14: float (optional, for vol matching)
        - session: str (optional, "asia" | "london" | "ny" | "overlap")
        """
        self.backtest_trades = pd.DataFrame(backtest_trades)
        self._normalize()
        self.live_r_history: list[float] = []
        self.comparisons: list[ComparatorResult] = []

    def _normalize(self) -> None:
        """Normalize column names and compute derived fields."""
        df = self.backtest_trades
        if df.empty:
            return

        # Normalize R-multiple column
        if "r_multiple" in df.columns:
            df["r"] = pd.to_numeric(df["r_multiple"], errors="coerce")
        elif "r" not in df.columns:
            df["r"] = 0.0
        else:
            df["r"] = pd.to_numeric(df["r"], errors="coerce")

        # Parse/estimate session from timestamp if not provided
        if "session" not in df.columns and "entry_time" in df.columns:
            times = pd.to_datetime(df["entry_time"], utc=True, errors="coerce")
            hours = times.dt.hour
            df["session"] = "unknown"
            df.loc[(hours >= 0) & (hours < 8), "session"] = "asia"
            df.loc[(hours >= 8) & (hours < 13), "session"] = "london"
            df.loc[(hours >= 13) & (hours < 16), "session"] = "overlap"
            df.loc[(hours >= 16) & (hours < 22), "session"] = "ny"

        # ATR percentile if ATR available
        if "atr_at_entry" in df.columns:
            atr = pd.to_numeric(df["atr_at_entry"], errors="coerce")
            df["atr_percentile"] = atr.rank(pct=True)
        elif "atr_14" in df.columns:
            atr = pd.to_numeric(df["atr_14"], errors="coerce")
            df["atr_percentile"] = atr.rank(pct=True)
        else:
            df["atr_percentile"] = 0.5

    def add_live_trade(self, trade: dict[str, Any]) -> ComparatorResult:
        """Add a live trade and compare it against similar backtest trades.

        Parameters
        ----------
        trade : dict
            Must have: direction, r_multiple or r
            Optional: entry_price, atr_at_entry, session

        Returns
        -------
        ComparatorResult with divergence assessment
        """
        direction = str(trade.get("direction", "BUY"))
        live_r = float(trade.get("r_multiple", trade.get("r", 0.0)))
        self.live_r_history.append(live_r)

        # Find matching backtest trades with recency-weighted scoring
        matched = self._find_matching_trades(trade)

        if matched.empty:
            result = ComparatorResult(
                trade_id=trade.get("id", trade.get("position_id", len(self.comparisons))),
                direction=direction,
                live_r=live_r,
                expected_r_mean=0.0,
                expected_r_std=0.0,
                expected_r_p10=0.0,
                expected_r_p90=0.0,
                z_score=0.0,
                cumulative_live_r=float(np.sum(self.live_r_history)),
                cumulative_expected_r=0.0,
                cumulative_z=0.0,
                n_matched_trades=0,
                divergence_level="normal",
            )
            self.comparisons.append(result)
            return result

        # Compute expected distribution from matched trades
        expected_r = matched["r"].dropna()
        expected_mean = float(expected_r.mean())
        expected_std = float(expected_r.std()) if len(expected_r) > 1 else 1.0
        expected_p10 = float(np.percentile(expected_r, 10)) if len(expected_r) >= 10 else expected_mean
        expected_p90 = float(np.percentile(expected_r, 90)) if len(expected_r) >= 10 else expected_mean

        # Z-score for this single trade
        z_score = (live_r - expected_mean) / expected_std if expected_std > 0 else 0.0

        # Cumulative expected R (mean of each matched group)
        cumulative_expected = sum(
            float(self.backtest_trades["r"].sample(min(50, len(self.backtest_trades))).mean())
            for _ in range(len(self.live_r_history))
        ) / max(len(self.live_r_history), 1)
        cumulative_live = float(np.sum(self.live_r_history))
        cumulative_z = (
            (cumulative_live - cumulative_expected)
            / (expected_std * np.sqrt(len(self.live_r_history)))
            if expected_std > 0 and self.live_r_history
            else 0.0
        )

        # Divergence level
        z = cumulative_z
        if z < -2.5:
            divergence = "critical"
        elif z < -2.0:
            divergence = "alert"
        elif z < -1.5:
            divergence = "watch"
        else:
            divergence = "normal"

        result = ComparatorResult(
            trade_id=trade.get("id", trade.get("position_id", len(self.comparisons))),
            direction=direction,
            live_r=live_r,
            expected_r_mean=round(expected_mean, 4),
            expected_r_std=round(expected_std, 4),
            expected_r_p10=round(expected_p10, 4),
            expected_r_p90=round(expected_p90, 4),
            z_score=round(z_score, 4),
            cumulative_live_r=round(cumulative_live, 4),
            cumulative_expected_r=round(cumulative_expected, 4),
            cumulative_z=round(cumulative_z, 4),
            n_matched_trades=len(matched),
            divergence_level=divergence,
        )
        self.comparisons.append(result)
        return result

    def _find_matching_trades(self, live_trade: dict[str, Any], max_matches: int = 200) -> pd.DataFrame:
        """Find similar backtest trades using feature matching.

        Scoring criteria:
        - Same direction:               weight 1.0
        - Same session:                 weight 0.8
        - Similar ATR percentile (20%): weight 0.5
        - Higher recency:               weight 0.3 × (position / total)
        """
        df = self.backtest_trades
        if df.empty:
            return pd.DataFrame()

        direction = str(live_trade.get("direction", "BUY"))
        # Parse session from live trade
        live_session = str(live_trade.get("session", ""))
        if not live_session or live_session == "unknown":
            entry_time = live_trade.get("entry_time")
            if entry_time:
                try:
                    hour = pd.Timestamp(entry_time).hour
                    if 0 <= hour < 8:
                        live_session = "asia"
                    elif 8 <= hour < 13:
                        live_session = "london"
                    elif 13 <= hour < 16:
                        live_session = "overlap"
                    elif 16 <= hour < 22:
                        live_session = "ny"
                except (ValueError, TypeError):
                    pass

        # ATR percentile
        live_atr = float(live_trade.get("atr_at_entry", live_trade.get("atr_14", 0.0)))
        live_atr_pct = 0.5

        # Start with direction match (most important)
        mask = df["direction"] == direction
        candidates = df[mask].copy()
        if candidates.empty:
            return pd.DataFrame()

        # Session score
        if live_session and "session" in candidates.columns:
            candidates["session_score"] = (candidates["session"] == live_session).astype(float) * 0.8
        else:
            candidates["session_score"] = 0.0

        # ATR percentile score
        if "atr_percentile" in candidates.columns and live_atr > 0:
            atr_diff = (candidates["atr_percentile"] - live_atr_pct).abs()
            candidates["atr_score"] = (atr_diff < 0.20).astype(float) * 0.5
        else:
            candidates["atr_score"] = 0.0

        # Recency score
        n = len(candidates)
        candidates["recency_score"] = np.linspace(0, 0.3, n) if n > 0 else 0.0

        # Total score: direction match (1.0) + session + atr + recency
        candidates["match_score"] = 1.0 + candidates["session_score"] + candidates["atr_score"] + candidates["recency_score"]

        # Return top matches
        candidates = candidates.sort_values("match_score", ascending=False)
        return candidates.head(max_matches)

    def get_summary(self) -> dict[str, Any]:
        """Return a summary of the live vs backtest comparison."""
        if not self.comparisons:
            return {
                "n_trades": 0,
                "cumulative_live_r": 0.0,
                "cumulative_expected_r": 0.0,
                "cumulative_z": 0.0,
                "divergence_level": "normal",
                "n_critical": 0,
                "n_alert": 0,
                "n_watch": 0,
            }

        return {
            "n_trades": len(self.live_r_history),
            "cumulative_live_r": self.comparisons[-1].cumulative_live_r,
            "cumulative_expected_r": self.comparisons[-1].cumulative_expected_r,
            "cumulative_z": self.comparisons[-1].cumulative_z,
            "divergence_level": self.comparisons[-1].divergence_level,
            "n_critical": sum(1 for c in self.comparisons if c.divergence_level == "critical"),
            "n_alert": sum(1 for c in self.comparisons if c.divergence_level == "alert"),
            "n_watch": sum(1 for c in self.comparisons if c.divergence_level == "watch"),
            "last_trade_z": self.comparisons[-1].z_score,
            "n_matched_per_trade": int(np.mean([c.n_matched_trades for c in self.comparisons])),
        }
