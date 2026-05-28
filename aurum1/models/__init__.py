"""Machine-learning model layer for AURUM-1."""

from aurum1.models.direction_predictor import (
    DirectionPredictor,
    FocalLoss,
    evaluate_lstm_promotion_gate,
    find_sequence_boundaries,
)
from aurum1.models.ensemble import EnsembleSignal, SignalResult
from aurum1.models.regime_classifier import RegimeClassifier
from aurum1.models.retrainer import ModelRetrainer
from aurum1.models.sentiment_model import SentimentScorer
from aurum1.models.utils import get_backend_report

__all__ = [
    "DirectionPredictor",
    "EnsembleSignal",
    "FocalLoss",
    "ModelRetrainer",
    "RegimeClassifier",
    "SentimentScorer",
    "SignalResult",
    "evaluate_lstm_promotion_gate",
    "find_sequence_boundaries",
    "get_backend_report",
]
