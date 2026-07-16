"""Meta-labeler: ML model that predicts whether a breakout signal will succeed.

Meta-labeling (López de Prado, 2018) is a two-step approach:
1. Primary model: predicts trade direction (the Donchian breakout)
2. Meta-labeler: predicts whether the primary signal will succeed or fail

The meta-labeler is trained ONLY on bars where a signal fired, and learns
to filter out low-probability trades.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

LOGGER = logging.getLogger("aurum1.meta_labeler")


@dataclass
class MetaLabelerResult:
    """Prediction from the meta-labeler."""

    predicted_success: bool
    success_probability: float
    confidence: str  # 'high', 'medium', 'low'


class MetaLabeler:
    """LightGBM classifier for signal quality prediction.

    Trains on historical breakout signals to predict which ones will succeed.
    """

    def __init__(self, settings: dict[str, Any]):
        ml_settings = settings.get("models", {}).get("meta_labeler", {})
        self.n_estimators = int(ml_settings.get("n_estimators", 100))
        self.max_depth = int(ml_settings.get("max_depth", 3))
        self.learning_rate = float(ml_settings.get("learning_rate", 0.1))
        self.subsample = float(ml_settings.get("subsample", 0.8))
        self.colsample_bytree = float(ml_settings.get("colsample_bytree", 0.8))
        self.class_weight = ml_settings.get("class_weight", "balanced")
        self.min_samples = int(ml_settings.get("min_samples", 20))

        self.model = None
        self.feature_names: list[str] = []
        self.threshold: float = 0.50  # Decision threshold (tunable)

    def build_features(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """Build features for meta-labeling from OHLCV data.

        Each row is one M15 bar.
        """
        frame = ohlcv.copy()
        close = frame["close"].astype(float)
        high = frame["high"].astype(float)
        low = frame["low"].astype(float)
        volume = frame["volume"].astype(float)

        # ATR and volatility features
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        frame["atr_14"] = tr.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()

        # ATR percentile (100-bar lookback)
        atr = frame["atr_14"]
        frame["atr_percentile"] = atr.rolling(100, min_periods=100).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
        )

        # ADX
        frame["adx_14"] = self._adx(high, low, close, 14)

        # Donchian channel
        donchian_high = high.rolling(20, min_periods=20).max()
        donchian_low = low.rolling(20, min_periods=20).min()
        donchian_mid = (donchian_high + donchian_low) / 2

        # Breakout distance (how far through the channel)
        frame["breakout_distance"] = np.where(
            close > donchian_high.shift(1),
            (close - donchian_mid) / atr.replace(0, np.nan),
            np.where(
                close < donchian_low.shift(1),
                (donchian_mid - close) / atr.replace(0, np.nan),
                0.0,
            ),
        )

        # EMA alignment
        frame["ema_9"] = close.ewm(span=9, adjust=False, min_periods=9).mean()
        frame["ema_20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
        frame["ema_alignment"] = np.sign(frame["ema_9"] - frame["ema_20"])

        # Session features
        hours = frame.index.hour
        frame["session_london"] = ((hours >= 7) & (hours < 16)).astype(int)
        frame["session_ny"] = ((hours >= 13) & (hours < 22)).astype(int)
        frame["session_asia"] = ((hours >= 0) & (hours < 8)).astype(int)

        # Day of week
        frame["dow"] = frame.index.dayofweek
        frame["is_monday"] = (frame["dow"] == 0).astype(int)
        frame["is_friday"] = (frame["dow"] == 4).astype(int)

        # Recent win rate (rolling 10 trades)
        # This requires trade history — placeholder for now

        # Turn-of-month
        frame["is_turn_of_month"] = frame.index.day.isin([1, 2, 3, 28, 29, 30, 31]).astype(int)

        # Consecutive breakout signals
        buy_signal = close > high.rolling(20, min_periods=20).max().shift(1)
        sell_signal = close < low.rolling(20, min_periods=20).min().shift(1)
        frame["signal_count_20"] = (buy_signal | sell_signal).rolling(20, min_periods=1).sum()

        # Collect valid features
        feature_cols = [
            "atr_14", "atr_percentile", "adx_14", "breakout_distance",
            "ema_alignment", "session_london", "session_ny", "session_asia",
            "is_monday", "is_friday", "is_turn_of_month", "signal_count_20",
        ]
        self.feature_names = feature_cols
        return frame[feature_cols]

    def generate_labels(
        self,
        ohlcv: pd.DataFrame,
        trades: list[dict[str, Any]],
    ) -> tuple[pd.DataFrame, np.ndarray]:
        """Generate training labels from backtest trades.

        For each bar where a trade was taken:
            label = 1 if the trade had positive net PnL, else 0

        Returns
        -------
        X : pd.DataFrame of features at signal bars
        y : np.ndarray of labels (1 = winning trade, 0 = losing)
        """
        features = self.build_features(ohlcv)

        # Map trades to signal bars
        signal_mask = np.zeros(len(ohlcv), dtype=int)
        for trade in trades:
            signal_time = trade.get("signal_time", trade.get("open_time"))
            if signal_time is None:
                continue
            try:
                ts = pd.Timestamp(signal_time)
                if ts in ohlcv.index:
                    idx = ohlcv.index.get_loc(ts)
                    if 0 <= idx < len(signal_mask):
                        pnl = float(trade.get("net_pnl", trade.get("pnl_after_fees", 0)))
                        signal_mask[idx] = 1 if pnl > 0 else -1
            except (ValueError, KeyError):
                continue

        # Filter to signal bars only
        signal_indices = np.where(signal_mask != 0)[0]
        if len(signal_indices) < self.min_samples:
            LOGGER.warning("Only %d signal bars found, need %d", len(signal_indices), self.min_samples)
            return features.iloc[:0], np.array([])

        X = features.iloc[signal_indices].copy()
        y = np.where(signal_mask[signal_indices] > 0, 1, 0)

        return X, y

    def train(
        self,
        X: pd.DataFrame,
        y: np.ndarray,
        update_latest: bool = False,
    ) -> None:
        """Train the meta-labeler model.

        Parameters
        ----------
        X : pd.DataFrame of features
        y : np.ndarray of binary labels (1 = win, 0 = loss)
        update_latest : bool, save as the latest model artifact
        """
        if len(X) < self.min_samples:
            LOGGER.warning("Not enough samples to train meta-labeler: %d < %d", len(X), self.min_samples)
            return

        try:
            import lightgbm as lgb
        except ImportError:
            LOGGER.error("lightgbm not installed, cannot train meta-labeler")
            return

        LOGGER.info("Training meta-labeler on %d samples (%.1f%% win rate)",
                     len(X), y.mean() * 100)

        self.model = lgb.LGBMClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            subsample=self.subsample,
            colsample_bytree=self.colsample_bytree,
            class_weight=self.class_weight,
            random_state=42,
            verbosity=-1,
        )
        self.model.fit(X, y, feature_name=self.feature_names)

        # Feature importance
        importance = pd.DataFrame({
            "feature": self.feature_names,
            "importance": self.model.feature_importances_,
        }).sort_values("importance", ascending=False)
        LOGGER.info("Meta-labeler feature importance:\n%s", importance.to_string())

        # Calibrate threshold on training data
        preds = self.model.predict_proba(X)[:, 1]
        win_rate = y.mean()
        # Set threshold to target the top 60% of signals (similar density to D4)
        self.threshold = float(np.percentile(preds, 100 * (1 - win_rate)))

    def predict(self, features: pd.Series | pd.DataFrame) -> MetaLabelerResult:
        """Predict whether a single signal will succeed."""
        if self.model is None:
            return MetaLabelerResult(
                predicted_success=True,
                success_probability=0.5,
                confidence="low",
            )

        if isinstance(features, pd.Series):
            features = features.to_frame().T

        proba = float(self.model.predict_proba(features[self.feature_names])[0, 1])

        predicted = proba >= self.threshold

        if proba >= 0.75:
            confidence = "high"
        elif proba >= 0.50:
            confidence = "medium"
        else:
            confidence = "low"

        return MetaLabelerResult(
            predicted_success=predicted,
            success_probability=proba,
            confidence=confidence,
        )

    def get_size_multiplier(self, features: pd.Series) -> float:
        """Get position size multiplier based on meta-labeler confidence.

        Returns
        -------
        float: multiplier for base position size (0.0 = skip, 2.0 = double)
        """
        result = self.predict(features)

        if result.confidence == "high" and result.predicted_success:
            return 1.5  # 150% size
        elif result.confidence == "medium" and result.predicted_success:
            return 1.0  # Normal size
        elif result.confidence == "low" and result.predicted_success:
            return 0.5  # Half size
        else:
            return 0.0  # Skip trade

    def save(self, path: str | Path) -> None:
        """Save model to disk."""
        import cloudpickle
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            cloudpickle.dump({"model": self.model, "features": self.feature_names, "threshold": self.threshold}, f)
        LOGGER.info("Meta-labeler saved to %s", path)

    def load(self, path: str | Path) -> None:
        """Load model from disk."""
        import cloudpickle
        with open(Path(path), "rb") as f:
            data = cloudpickle.load(f)
        self.model = data["model"]
        self.feature_names = data["features"]
        self.threshold = data.get("threshold", 0.50)
        LOGGER.info("Meta-labeler loaded from %s", path)

    @staticmethod
    def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
        """Compute ADX."""
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
        minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
        plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr.replace(0, np.nan)
        minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr.replace(0, np.nan)
        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        return dx.ewm(alpha=1.0 / period, adjust=False).mean()


__all__ = ["MetaLabeler", "MetaLabelerResult"]
