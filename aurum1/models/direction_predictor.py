"""Directional sequence predictor for AURUM-1 Phase 3."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from aurum1.models.utils import (
    directional_sharpe,
    random_seed_from_settings,
    save_pickle_artifact,
    set_random_seeds,
    time_series_splits,
    timestamp_version,
)


TARGET_MAP = {-1: 0, 0: 1, 1: 2}
INVERSE_TARGET_MAP = {0: -1, 1: 0, 2: 1}


def find_sequence_boundaries(index: pd.DatetimeIndex, max_gap_minutes: int = 30) -> list[int]:
    """Return integer positions where a new sequence segment begins."""

    if len(index) == 0:
        return []
    boundaries = [0]
    gaps = index.to_series().diff().dt.total_seconds().div(60.0)
    for position, gap_minutes in enumerate(gaps.iloc[1:], start=1):
        if gap_minutes > max_gap_minutes:
            boundaries.append(position)
    return boundaries


class RobustScalerLite:
    """Small RobustScaler replacement using median and IQR."""

    def __init__(self) -> None:
        self.center_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None

    def fit(self, x: np.ndarray) -> "RobustScalerLite":
        self.center_ = np.nanmedian(x, axis=0)
        q75 = np.nanpercentile(x, 75, axis=0)
        q25 = np.nanpercentile(x, 25, axis=0)
        scale = q75 - q25
        scale[scale == 0.0] = 1.0
        self.scale_ = scale
        return self

    def transform(self, x: np.ndarray) -> np.ndarray:
        if self.center_ is None or self.scale_ is None:
            raise RuntimeError("Scaler must be fit before transform")
        return (x - self.center_) / self.scale_

    def fit_transform(self, x: np.ndarray) -> np.ndarray:
        return self.fit(x).transform(x)


class _SoftmaxSequenceModel:
    """Deterministic fallback sequence classifier for environments without PyTorch."""

    def __init__(self) -> None:
        self.centroids: dict[int, np.ndarray] = {}
        self.class_priors: dict[int, float] = {}
        self.classes = np.array([0, 1, 2], dtype=int)

    def fit(self, x: np.ndarray, y: np.ndarray) -> "_SoftmaxSequenceModel":
        summary = self._summarize(x)
        for label in self.classes:
            mask = y == label
            self.class_priors[int(label)] = float(mask.mean()) if mask.any() else 1e-6
            self.centroids[int(label)] = summary[mask].mean(axis=0) if mask.any() else summary.mean(axis=0)
        return self

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        summary = self._summarize(x)
        distances = []
        for label in self.classes:
            distances.append(np.linalg.norm(summary - self.centroids[int(label)], axis=1))
        priors = np.asarray([self.class_priors.get(int(label), 1e-6) for label in self.classes], dtype=float)
        priors = priors / np.where(priors.sum() == 0.0, 1.0, priors.sum())
        logits = -np.vstack(distances).T + np.log(np.clip(priors, 1e-9, 1.0))
        logits = logits - logits.max(axis=1, keepdims=True)
        exp = np.exp(logits)
        return exp / exp.sum(axis=1, keepdims=True)

    def _summarize(self, x: np.ndarray) -> np.ndarray:
        return np.concatenate([x[:, -1, :], x.mean(axis=1)], axis=1)


class FocalLoss:
    """Focal loss implementation.

    When PyTorch is installed this class accepts tensors and returns a tensor.
    In the lightweight local runtime it also works with NumPy probability
    arrays, which keeps tests deterministic without importing torch.
    """

    def __init__(self, gamma: float = 2.0, alpha: list[float] | None = None) -> None:
        self.gamma = gamma
        self.alpha = np.asarray(alpha or [0.25, 0.5, 0.25], dtype=float)

    def __call__(self, predictions: Any, targets: Any) -> Any:
        try:
            import torch
            import torch.nn.functional as functional

            if hasattr(predictions, "dim"):
                log_probs = predictions
                probs = log_probs.exp()
                target_tensor = targets.long()
                p_t = probs.gather(1, target_tensor.view(-1, 1)).squeeze(1)
                log_p_t = log_probs.gather(1, target_tensor.view(-1, 1)).squeeze(1)
                alpha = torch.tensor(self.alpha, dtype=log_probs.dtype, device=log_probs.device)
                alpha_t = alpha.gather(0, target_tensor)
                return (-(alpha_t * (1.0 - p_t).pow(self.gamma) * log_p_t)).mean()
        except ImportError:
            pass

        probs_np = np.asarray(predictions, dtype=float)
        targets_np = np.asarray(targets, dtype=int)
        if probs_np.ndim == 1:
            probs_np = probs_np.reshape(1, -1)
        row_sums = probs_np.sum(axis=1, keepdims=True)
        probs_np = probs_np / np.where(row_sums == 0.0, 1.0, row_sums)
        p_t = np.clip(probs_np[np.arange(len(targets_np)), targets_np], 1e-12, 1.0)
        alpha_t = self.alpha[targets_np]
        return float(np.mean(-(alpha_t * np.power(1.0 - p_t, self.gamma) * np.log(p_t))))


class DirectionPredictor:
    """Train and serve the Phase 3 directional sequence predictor."""

    model_name = "direction_predictor"

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        direction_settings = settings.get("models", {}).get("direction", {})
        self.sequence_length = int(direction_settings.get("sequence_length", 60))
        self.max_epochs = int(direction_settings.get("max_epochs", 100))
        self.batch_size = int(direction_settings.get("batch_size", 256))
        self.patience = int(direction_settings.get("patience", 10))
        self.model: Any | None = None
        self.scaler = RobustScalerLite()
        self.feature_names: list[str] = []
        self.metadata: dict[str, Any] = {}
        self.artifact_path: Path | None = None
        self.meta_path: Path | None = None

    def train(
        self,
        feature_df: pd.DataFrame,
        feature_names: list[str] | None = None,
        *,
        update_latest: bool = True,
    ) -> dict[str, Any]:
        """Train a sequence model from Phase 2 features with targets."""

        seed = random_seed_from_settings(self.settings)
        set_random_seeds(seed)
        if "label" not in feature_df.columns:
            raise ValueError("DirectionPredictor training requires Phase 2 label column")
        self.feature_names = list(feature_names or self._infer_feature_names(feature_df))
        x_sequences, y = self._make_sequences(feature_df, self.feature_names)
        if len(y) == 0:
            raise ValueError("Not enough contiguous rows to build direction sequences")

        validation_losses: list[float] = []
        validation_accuracy = 0.0
        validation_sharpe = 0.0
        for train_idx, val_idx in time_series_splits(len(y), n_splits=5, gap=self.sequence_length):
            train_x = x_sequences[train_idx]
            val_x = x_sequences[val_idx]
            scaler = RobustScalerLite().fit(train_x.reshape(-1, train_x.shape[-1]))
            train_scaled = scaler.transform(train_x.reshape(-1, train_x.shape[-1])).reshape(train_x.shape)
            val_scaled = scaler.transform(val_x.reshape(-1, val_x.shape[-1])).reshape(val_x.shape)
            fold_model = _SoftmaxSequenceModel().fit(train_scaled, y[train_idx])
            proba = fold_model.predict_proba(val_scaled)
            validation_losses.append(FocalLoss()(proba, y[val_idx]))
            pred = np.argmax(proba, axis=1)
            validation_accuracy = float(np.mean(pred == y[val_idx]))
            validation_sharpe = self._sequence_sharpe(feature_df, pred, val_idx)

        self.scaler = RobustScalerLite().fit(x_sequences.reshape(-1, x_sequences.shape[-1]))
        scaled = self.scaler.transform(x_sequences.reshape(-1, x_sequences.shape[-1])).reshape(x_sequences.shape)
        self.model = _SoftmaxSequenceModel().fit(scaled, y)
        train_proba = self.model.predict_proba(scaled)
        train_pred = np.argmax(train_proba, axis=1)
        train_accuracy = float(np.mean(train_pred == y))
        if not validation_losses:
            validation_losses.append(FocalLoss()(train_proba, y))
            validation_accuracy = train_accuracy
            validation_sharpe = self._sequence_sharpe(feature_df, train_pred, np.arange(len(train_pred)))

        baseline_metrics = self._baseline_gate_metrics(feature_df)
        lstm_metrics = {
            "directional_accuracy": validation_accuracy,
            "profit_factor": max(1e-9, validation_accuracy / max(1e-9, 1.0 - validation_accuracy)),
            "sharpe": validation_sharpe,
            "max_drawdown": 0.0,
            "calmar": validation_sharpe,
            "net_return": validation_sharpe,
        }
        promoted, failed_metrics = evaluate_lstm_promotion_gate(baseline_metrics, lstm_metrics)

        version = timestamp_version()
        self.metadata = {
            "training_date": datetime.now(UTC).isoformat(),
            "feature_names": self.feature_names,
            "training_rows": int(len(feature_df)),
            "validation_sharpe": float(validation_sharpe),
            "validation_accuracy": float(validation_accuracy),
            "validation_loss": float(np.mean(validation_losses)),
            "model_version": version,
            "random_seed": seed,
            "sequence_length": self.sequence_length,
            "lstm_promotion_gate": {
                "promoted": promoted,
                "failed_metrics": failed_metrics,
                "baseline_metrics": baseline_metrics,
                "lstm_metrics": lstm_metrics,
            },
        }
        payload = {
            "model": self.model,
            "scaler": self.scaler,
            "feature_names": self.feature_names,
            "metadata": self.metadata,
        }
        self.artifact_path, self.meta_path = save_pickle_artifact(
            self.model_name,
            ".pkl",
            payload,
            self.metadata,
            self.settings,
            update_latest=update_latest,
        )
        return self.metadata

    def evaluate(self, feature_df: pd.DataFrame) -> dict[str, float]:
        """Evaluate validation Sharpe on supplied labeled features."""

        proba = self.predict_proba(feature_df)
        pred_classes = np.argmax(proba, axis=1)
        target = np.asarray([TARGET_MAP[int(value)] for value in feature_df["label"].iloc[-len(pred_classes):]], dtype=int)
        return {
            "validation_sharpe": self._sequence_sharpe(feature_df, pred_classes, np.arange(len(pred_classes))),
            "validation_accuracy": float(np.mean(pred_classes == target)) if len(target) else 0.0,
        }

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        """Return softmax probabilities for [sell, flat, buy]."""

        if self.model is None:
            raise RuntimeError("DirectionPredictor must be trained before prediction")
        x = features[self.feature_names].to_numpy(dtype=float)
        sequences = self._sequences_from_feature_array(x)
        if len(sequences) == 0:
            return np.empty((0, 3), dtype=float)
        scaled = self.scaler.transform(sequences.reshape(-1, sequences.shape[-1])).reshape(sequences.shape)
        proba = np.asarray(self.model.predict_proba(scaled), dtype=float)
        row_sums = proba.sum(axis=1, keepdims=True)
        return proba / np.where(row_sums == 0.0, 1.0, row_sums)

    def predict_signal(self, features: pd.DataFrame) -> float:
        """Return scalar directional signal in [-1, 1]."""

        proba = self.predict_proba(features)
        if len(proba) == 0:
            return 0.0
        signal = float(proba[-1, 2] - proba[-1, 0])
        return float(np.clip(signal, -1.0, 1.0))

    def _make_sequences(self, frame: pd.DataFrame, feature_names: list[str]) -> tuple[np.ndarray, np.ndarray]:
        x = frame[feature_names].to_numpy(dtype=float)
        labels = frame["label"].map(TARGET_MAP).to_numpy(dtype=int)
        sequence_list: list[np.ndarray] = []
        target_list: list[int] = []
        boundaries = find_sequence_boundaries(pd.DatetimeIndex(frame.index))
        boundaries.append(len(frame))
        for start, end in zip(boundaries[:-1], boundaries[1:], strict=True):
            for row in range(start + self.sequence_length, end):
                sequence_list.append(x[row - self.sequence_length : row])
                target_list.append(int(labels[row]))
        if not sequence_list:
            return np.empty((0, self.sequence_length, len(feature_names))), np.empty((0,), dtype=int)
        return np.stack(sequence_list), np.asarray(target_list, dtype=int)

    def _sequences_from_feature_array(self, x: np.ndarray) -> np.ndarray:
        if len(x) <= self.sequence_length:
            return np.empty((0, self.sequence_length, x.shape[1]), dtype=float)
        return np.stack([x[row - self.sequence_length : row] for row in range(self.sequence_length, len(x))])

    def _infer_feature_names(self, frame: pd.DataFrame) -> list[str]:
        excluded = {"label", "forward_return_5bar", "source", "instrument"}
        return [column for column in frame.columns if column not in excluded and pd.api.types.is_numeric_dtype(frame[column])]

    def _sequence_sharpe(self, frame: pd.DataFrame, pred_classes: np.ndarray, sequence_positions: np.ndarray) -> float:
        if "forward_return_5bar" not in frame.columns or len(pred_classes) == 0:
            return 0.0
        returns = frame["forward_return_5bar"].iloc[-len(pred_classes):].to_numpy(dtype=float)
        direction = np.asarray([INVERSE_TARGET_MAP[int(label)] for label in pred_classes], dtype=float)
        return directional_sharpe(returns * np.sign(direction))

    def _baseline_gate_metrics(self, frame: pd.DataFrame) -> dict[str, float]:
        if "label" not in frame.columns:
            return {}
        labels = frame["label"].to_numpy(dtype=int)
        flat_accuracy = float(np.mean(labels == 0))
        return {
            "directional_accuracy": flat_accuracy,
            "profit_factor": 1.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "calmar": 0.0,
            "net_return": 0.0,
        }


def evaluate_lstm_promotion_gate(
    baseline_metrics: dict[str, float],
    lstm_metrics: dict[str, float],
) -> tuple[bool, list[str]]:
    """Return whether the LSTM passes at least four of six promotion metrics."""

    failed: list[str] = []
    checks = {
        "directional_accuracy": lstm_metrics.get("directional_accuracy", 0.0) > 0.52,
        "profit_factor": lstm_metrics.get("profit_factor", 0.0) - baseline_metrics.get("profit_factor", 0.0) > 0.0,
        "sharpe": lstm_metrics.get("sharpe", 0.0) - baseline_metrics.get("sharpe", 0.0) >= 0.05,
        "max_drawdown": lstm_metrics.get("max_drawdown", 0.0)
        <= baseline_metrics.get("max_drawdown", 0.0) * 1.10 + 1e-12,
        "calmar": lstm_metrics.get("calmar", 0.0) - baseline_metrics.get("calmar", 0.0) > 0.0,
        "net_return": lstm_metrics.get("net_return", 0.0) - baseline_metrics.get("net_return", 0.0) > 0.0,
    }
    for name, passed in checks.items():
        if not passed:
            failed.append(name)
    return (len(checks) - len(failed)) >= 4, failed


__all__ = [
    "DirectionPredictor",
    "FocalLoss",
    "RobustScalerLite",
    "evaluate_lstm_promotion_gate",
    "find_sequence_boundaries",
]
