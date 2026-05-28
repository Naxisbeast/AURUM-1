"""Validation and ablation helpers for AURUM-1 Phase 3.5."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from aurum1.models.direction_predictor import DirectionPredictor
from aurum1.models.regime_classifier import REGIME_FEATURES, REGIME_LABEL_FEATURES, RegimeClassifier
from aurum1.models.utils import classification_report_dict, random_seed_from_settings


REGIME_ABLATION_GROUPS = {
    "technical": ["macd_histogram", "rsi_14"],
    "volatility": ["atr_percentile", "bb_width"],
    "macro": ["dxy_daily_return", "vix_level"],
    "session": ["session_london", "session_ny", "session_overlap", "hour_sin", "hour_cos"],
}


def run_regime_ablation(
    feature_df: pd.DataFrame,
    settings: dict,
) -> dict[str, dict[str, Any]]:
    """Evaluate regime classification edge by feature group on time-ordered splits."""

    train, validation = _ordered_split(feature_df, train_fraction=0.70)
    groups = dict(REGIME_ABLATION_GROUPS)
    groups["all"] = _infer_model_features(feature_df)
    results: dict[str, dict[str, Any]] = {}
    labels_train = RegimeClassifier.generate_labels(train).to_numpy(dtype=int)
    labels_validation = RegimeClassifier.generate_labels(validation).to_numpy(dtype=int)
    seed = random_seed_from_settings(settings)
    for name, columns in groups.items():
        present = [column for column in columns if column in feature_df.columns]
        if not present:
            results[name] = {"f1_macro": 0.0, "f1_per_class": {}, "accuracy": 0.0}
            continue
        classifier = RegimeClassifier(settings)
        model = classifier._new_model(seed)  # Uses LightGBM when installed, fallback otherwise.
        model.fit(train[present].astype(float), labels_train)
        prediction = np.asarray(model.predict(validation[present].astype(float)), dtype=int)
        report = classification_report_dict(labels_validation, prediction, [0, 1, 2])
        results[name] = {
            "f1_macro": float(report["mean_f1"]),
            "f1_per_class": report["f1"],
            "accuracy": float(np.mean(prediction == labels_validation)),
        }
    return results


def run_lstm_promotion_gate(
    baseline_metrics: dict[str, float],
    lstm_metrics: dict[str, float],
) -> tuple[bool, list[str]]:
    """Return whether the LSTM passes at least four of six promotion metrics."""

    checks = {
        "directional_accuracy": lstm_metrics.get("directional_accuracy", 0.0) > 0.52,
        "profit_factor": lstm_metrics.get("profit_factor", 0.0) - baseline_metrics.get("profit_factor", 0.0) > 0.0,
        "sharpe": lstm_metrics.get("sharpe", 0.0) - baseline_metrics.get("sharpe", 0.0) >= 0.05,
        "max_drawdown": lstm_metrics.get("max_drawdown", 0.0)
        <= baseline_metrics.get("max_drawdown", 0.0) * 1.10 + 1e-12,
        "calmar": lstm_metrics.get("calmar", 0.0) - baseline_metrics.get("calmar", 0.0) > 0.0,
        "net_return": lstm_metrics.get("net_return", 0.0) - baseline_metrics.get("net_return", 0.0) > 0.0,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return (len(checks) - len(failed)) >= 4, failed


def run_ensemble_ablation(
    feature_df: pd.DataFrame,
    settings: dict,
) -> dict[str, dict[str, float]]:
    """Evaluate simplified signal quality for four ensemble operating modes."""

    train, validation = _ordered_split(feature_df, train_fraction=0.70)
    rule_signal = _rule_signal(validation)
    regime_signal = _regime_filtered_signal(train, validation, settings, rule_signal)
    lstm_signal = _lstm_signal(train, validation, settings, regime_signal)
    sentiment_signal = _sentiment_filtered_signal(validation, regime_signal)
    return {
        "rule_only": _signal_quality(validation, rule_signal),
        "rule_plus_regime": _signal_quality(validation, regime_signal),
        "rule_plus_regime_lstm": _signal_quality(validation, lstm_signal),
        "rule_plus_full": _signal_quality(validation, sentiment_signal),
    }


def _ordered_split(frame: pd.DataFrame, train_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    split = max(1, min(len(frame) - 1, int(len(frame) * train_fraction)))
    return frame.iloc[:split].copy(), frame.iloc[split:].copy()


def _infer_model_features(frame: pd.DataFrame) -> list[str]:
    excluded = {"label", "forward_return_5bar", "source", "instrument"} | REGIME_LABEL_FEATURES
    return [
        column
        for column in REGIME_FEATURES
        if column in frame.columns and column not in excluded and pd.api.types.is_numeric_dtype(frame[column])
    ]


def _rule_signal(frame: pd.DataFrame) -> np.ndarray:
    buy = (frame["adx_14"] > 25.0) & (frame["ema_alignment_score"] >= 3)
    sell = (frame["adx_14"] > 25.0) & (frame["ema_alignment_score"] <= -3)
    return np.select([buy, sell], [1, -1], default=0).astype(int)


def _regime_filtered_signal(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    settings: dict,
    base_signal: np.ndarray,
) -> np.ndarray:
    classifier = RegimeClassifier(settings)
    classifier.train(train, update_latest=False)
    regime_prediction = classifier.predict(validation)
    filtered = base_signal.copy()
    filtered[(filtered == 1) & (regime_prediction != 0)] = 0
    filtered[(filtered == -1) & (regime_prediction != 1)] = 0
    return filtered


def _lstm_signal(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    settings: dict,
    base_signal: np.ndarray,
) -> np.ndarray:
    if "label" not in train.columns or len(train) < 80 or len(validation) < 80:
        return base_signal.copy()
    predictor = DirectionPredictor(settings)
    predictor.train(train, update_latest=False)
    proba = predictor.predict_proba(validation)
    if len(proba) == 0:
        return base_signal.copy()
    lstm_direction = np.select([proba[:, 2] > proba[:, 0], proba[:, 0] > proba[:, 2]], [1, -1], default=0)
    aligned = base_signal.copy()
    aligned[-len(lstm_direction) :] = np.where(aligned[-len(lstm_direction) :] == 0, lstm_direction, aligned[-len(lstm_direction) :])
    return aligned


def _sentiment_filtered_signal(validation: pd.DataFrame, base_signal: np.ndarray) -> np.ndarray:
    sentiment_scalar = validation.get("sentiment_bullish", 0.0) - validation.get("sentiment_bearish", 0.0)
    filtered = base_signal.copy()
    filtered[(filtered == 1) & (np.asarray(sentiment_scalar) < -0.25)] = 0
    filtered[(filtered == -1) & (np.asarray(sentiment_scalar) > 0.25)] = 0
    return filtered


def _signal_quality(frame: pd.DataFrame, signal: np.ndarray) -> dict[str, float]:
    if "forward_return_5bar" not in frame.columns:
        returns = np.zeros(len(frame), dtype=float)
    else:
        returns = frame["forward_return_5bar"].to_numpy(dtype=float)
    actual_direction = np.sign(returns).astype(int)
    active = signal != 0
    if active.any():
        accuracy = float(np.mean(signal[active] == actual_direction[active]))
    else:
        accuracy = 0.0
    buy_returns = returns[signal == 1]
    sell_returns = returns[signal == -1]
    return {
        "directional_accuracy": accuracy,
        "mean_forward_return_buy": float(np.mean(buy_returns)) if buy_returns.size else 0.0,
        "mean_forward_return_sell": float(np.mean(sell_returns)) if sell_returns.size else 0.0,
        "signal_count": float(active.sum()),
    }


__all__ = ["run_ensemble_ablation", "run_lstm_promotion_gate", "run_regime_ablation"]
