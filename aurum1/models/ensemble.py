"""Pure ensemble combination logic for AURUM-1 Phase 3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np


REGIME_NAMES = {0: "TRENDING_UP", 1: "TRENDING_DOWN", 2: "RANGING"}


@dataclass(frozen=True)
class SignalResult:
    """Final ensemble signal and component evidence."""

    direction: str
    raw_score: float
    regime: str
    regime_confidence: float
    direction_signal: float
    sentiment_scalar: float
    timestamp: datetime


class EnsembleSignal:
    """Combine regime, direction, and sentiment into a tradeable signal."""

    def __init__(self, settings: dict[str, Any]) -> None:
        ensemble_settings = settings.get("models", {}).get("ensemble", {})
        self.buy_threshold = float(ensemble_settings.get("buy_threshold", 0.60))
        self.sell_threshold = float(ensemble_settings.get("sell_threshold", -0.60))
        self.ranging_buy_threshold = float(ensemble_settings.get("ranging_buy_threshold", 0.72))
        self.ranging_sell_threshold = float(ensemble_settings.get("ranging_sell_threshold", -0.72))

    def combine(
        self,
        regime_proba: np.ndarray,
        direction_signal: float,
        sentiment: dict[str, float],
        timestamp: datetime | None = None,
    ) -> SignalResult:
        """Apply the exact Phase 3 weighted ensemble formula."""

        proba = np.asarray(regime_proba, dtype=float).reshape(3)
        proba = proba / (proba.sum() if proba.sum() else 1.0)
        regime_class = int(np.argmax(proba))
        regime_confidence = float(proba[regime_class])
        if sentiment.get("quality") == "low":
            sentiment_scalar = 0.0
        else:
            sentiment_scalar = float(sentiment.get("positive", 0.0) - sentiment.get("negative", 0.0))
        clipped_direction = float(np.clip(direction_signal, -1.0, 1.0))
        ranging_penalty = 0.5 if regime_class == 2 else 1.0
        raw_signal = (
            clipped_direction * 0.50
            + regime_confidence * 0.30 * float(np.sign(clipped_direction))
            + sentiment_scalar * 0.20
        ) * ranging_penalty

        active_buy_threshold = self.ranging_buy_threshold if regime_class == 2 else self.buy_threshold
        active_sell_threshold = self.ranging_sell_threshold if regime_class == 2 else self.sell_threshold
        if raw_signal > active_buy_threshold:
            direction = "BUY"
        elif raw_signal < active_sell_threshold:
            direction = "SELL"
        else:
            direction = "FLAT"

        return SignalResult(
            direction=direction,
            raw_score=float(raw_signal),
            regime=REGIME_NAMES[regime_class],
            regime_confidence=regime_confidence,
            direction_signal=clipped_direction,
            sentiment_scalar=sentiment_scalar,
            timestamp=timestamp or datetime.now(UTC),
        )


__all__ = ["EnsembleSignal", "SignalResult"]
