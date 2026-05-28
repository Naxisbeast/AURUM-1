"""Regime classification model for AURUM-1 Phase 3."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from aurum1.models.utils import (
    classification_report_dict,
    directional_sharpe,
    random_seed_from_settings,
    save_pickle_artifact,
    set_random_seeds,
    time_series_splits,
    timestamp_version,
)


REGIME_LABELS = {0: "TRENDING_UP", 1: "TRENDING_DOWN", 2: "RANGING"}
REGIME_FEATURES = [
    "atr_percentile",
    "bb_width",
    "macd_histogram",
    "rsi_14",
    "rel_volume",
    "vix_level",
    "dxy_daily_return",
]
REGIME_LABEL_FEATURES = {"adx_14", "ema_alignment_score"}


class _CentroidClassifier:
    """Small deterministic fallback when LightGBM is unavailable."""

    def __init__(self) -> None:
        self.centroids: dict[int, np.ndarray] = {}
        self.class_priors: dict[int, float] = {}
        self.classes = np.array([0, 1, 2], dtype=int)

    def fit(self, x: np.ndarray, y: np.ndarray) -> "_CentroidClassifier":
        for label in self.classes:
            mask = y == label
            self.class_priors[int(label)] = float(mask.mean()) if mask.any() else 1e-6
            if mask.any():
                self.centroids[int(label)] = np.nanmean(x[mask], axis=0)
            else:
                self.centroids[int(label)] = np.nanmean(x, axis=0)
        return self

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        distances = []
        for label in self.classes:
            centroid = self.centroids[int(label)]
            distances.append(np.linalg.norm(x - centroid, axis=1))
        distance_matrix = np.vstack(distances).T
        logits = -distance_matrix
        logits = logits - logits.max(axis=1, keepdims=True)
        exp = np.exp(logits)
        probabilities = exp / exp.sum(axis=1, keepdims=True)
        return probabilities

    def predict(self, x: np.ndarray) -> np.ndarray:
        return self.classes[np.argmax(self.predict_proba(x), axis=1)]


class RegimeClassifier:
    """Train and serve the AURUM-1 market regime classifier."""

    model_name = "regime_classifier"

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.model: Any | None = None
        self.feature_names = list(REGIME_FEATURES)
        self.metadata: dict[str, Any] = {}
        self.artifact_path: Path | None = None
        self.meta_path: Path | None = None

    @staticmethod
    def generate_labels(features: pd.DataFrame) -> pd.Series:
        """Generate regime labels from ADX and EMA alignment."""

        labels = pd.Series(2, index=features.index, dtype="int64", name="regime_label")
        labels[(features["adx_14"] > 25.0) & (features["ema_alignment_score"] >= 3)] = 0
        labels[(features["adx_14"] > 25.0) & (features["ema_alignment_score"] <= -3)] = 1
        return labels

    def train(
        self,
        feature_df: pd.DataFrame,
        feature_names: list[str] | None = None,
        *,
        update_latest: bool = True,
    ) -> dict[str, Any]:
        """Train on the most recent 252 days and write model artifacts."""

        seed = random_seed_from_settings(self.settings)
        set_random_seeds(seed)
        training_frame = self._latest_rolling_window(feature_df)
        labels = self.generate_labels(training_frame)
        self.feature_names = self._validated_feature_names(feature_names or REGIME_FEATURES)
        x = training_frame[self.feature_names].astype(float)
        y = labels.to_numpy(dtype=int)

        fold_reports: list[dict[str, Any]] = []
        for train_idx, val_idx in time_series_splits(len(training_frame), n_splits=5):
            fold_model = self._new_model(seed)
            fold_model.fit(x.iloc[train_idx], y[train_idx])
            pred = fold_model.predict(x.iloc[val_idx])
            fold_reports.append(classification_report_dict(y[val_idx], pred, [0, 1, 2]))

        self.model = self._new_model(seed)
        self.model.fit(x, y)
        prediction = self.model.predict(x)
        full_report = classification_report_dict(y, prediction, [0, 1, 2])
        validation_f1 = self._mean_f1_per_class(fold_reports) if fold_reports else full_report["f1"]
        validation_sharpe = self._validation_sharpe(training_frame, prediction)
        ablation_results = self.run_ablation(training_frame)

        version = timestamp_version()
        self.metadata = {
            "training_date": datetime.now(UTC).isoformat(),
            "feature_names": self.feature_names,
            "training_rows": int(len(training_frame)),
            "validation_sharpe": validation_sharpe,
            "validation_f1_per_class": validation_f1,
            "cv_fold_reports": fold_reports,
            "model_version": version,
            "random_seed": seed,
            "regime_labels": REGIME_LABELS,
            "ablation_results": ablation_results,
        }
        payload = {"model": self.model, "feature_names": self.feature_names, "metadata": self.metadata}
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
        """Evaluate validation Sharpe and mean F1 on supplied features."""

        labels = self.generate_labels(feature_df).to_numpy(dtype=int)
        pred = self.predict(feature_df)
        report = classification_report_dict(labels, pred, [0, 1, 2])
        return {
            "validation_sharpe": self._validation_sharpe(feature_df, pred),
            "mean_f1": float(report["mean_f1"]),
        }

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        """Return integer regime predictions in {0, 1, 2}."""

        if self.model is None:
            raise RuntimeError("RegimeClassifier must be trained before prediction")
        return np.asarray(self.model.predict(features[self.feature_names].astype(float)), dtype=int)

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        """Return regime probabilities with columns [TRENDING_UP, TRENDING_DOWN, RANGING]."""

        if self.model is None:
            raise RuntimeError("RegimeClassifier must be trained before prediction")
        probabilities = np.asarray(self.model.predict_proba(features[self.feature_names].astype(float)), dtype=float)
        row_sums = probabilities.sum(axis=1, keepdims=True)
        return probabilities / np.where(row_sums == 0.0, 1.0, row_sums)

    def run_ablation(self, feature_df: pd.DataFrame) -> dict[str, Any]:
        """Run feature-group ablation evidence gates for the regime classifier."""

        groups = {
            "technical_only": ["macd_histogram", "rsi_14"],
            "volatility_only": ["atr_percentile", "bb_width"],
            "macro_only": ["dxy_daily_return", "vix_level"],
            "session_only": [
                "session_asia",
                "session_london",
                "session_ny",
                "session_overlap",
                "hour_sin",
                "hour_cos",
            ],
            "all_features_combined": list(REGIME_FEATURES),
        }
        labels = self.generate_labels(feature_df).to_numpy(dtype=int)
        results: dict[str, Any] = {}
        baseline = 0.0
        for name, columns in groups.items():
            present = [column for column in columns if column in feature_df.columns and column not in {"label", "forward_return_5bar"}]
            if not present:
                continue
            report = self._cross_validated_group_report(feature_df[present].to_numpy(dtype=float), labels)
            results[name] = report
            if name == "technical_only":
                baseline = float(report["mean_f1"])
        kept = [
            name
            for name, report in results.items()
            if name == "technical_only" or float(report["mean_f1"]) > baseline
        ]
        return {"groups": results, "baseline_group": "technical_only", "kept_feature_groups": kept}

    def _validated_feature_names(self, feature_names: list[str]) -> list[str]:
        leaked = sorted(set(feature_names) & REGIME_LABEL_FEATURES)
        if leaked:
            raise ValueError(
                "Regime classifier feature set contains label-definition columns: "
                f"{leaked}. Use only indirect regime indicators."
            )
        return list(feature_names)

    def _cross_validated_group_report(self, x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
        reports: list[dict[str, Any]] = []
        seed = random_seed_from_settings(self.settings)
        for train_idx, val_idx in time_series_splits(len(y), n_splits=5):
            model = _CentroidClassifier().fit(x[train_idx], y[train_idx])
            reports.append(classification_report_dict(y[val_idx], model.predict(x[val_idx]), [0, 1, 2]))
        if not reports:
            return classification_report_dict(y, _CentroidClassifier().fit(x, y).predict(x), [0, 1, 2])
        f1 = {str(label): float(np.mean([report["f1"][str(label)] for report in reports])) for label in [0, 1, 2]}
        precision = {
            str(label): float(np.mean([report["precision"][str(label)] for report in reports])) for label in [0, 1, 2]
        }
        recall = {str(label): float(np.mean([report["recall"][str(label)] for report in reports])) for label in [0, 1, 2]}
        confusion = np.sum([np.asarray(report["confusion_matrix"]) for report in reports], axis=0).tolist()
        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "mean_f1": float(np.mean(list(f1.values()))),
            "confusion_matrix": confusion,
            "random_seed": seed,
        }

    def _new_model(self, seed: int) -> Any:
        try:
            from lightgbm import LGBMClassifier

            params = self.settings.get("models", {}).get("regime", {})
            return LGBMClassifier(
                n_estimators=int(params.get("n_estimators", 300)),
                learning_rate=float(params.get("learning_rate", 0.05)),
                max_depth=int(params.get("max_depth", 4)),
                num_leaves=int(params.get("num_leaves", 15)),
                min_child_samples=int(params.get("min_child_samples", 20)),
                subsample=float(params.get("subsample", 0.8)),
                colsample_bytree=float(params.get("colsample_bytree", 0.8)),
                class_weight=params.get("class_weight", "balanced"),
                random_state=seed,
                verbose=-1,
            )
        except ImportError:
            return _CentroidClassifier()

    def _latest_rolling_window(self, frame: pd.DataFrame) -> pd.DataFrame:
        if isinstance(frame.index, pd.DatetimeIndex) and not frame.empty:
            cutoff = frame.index.max() - timedelta(days=252)
            window = frame.loc[frame.index >= cutoff]
            if len(window) >= 20:
                return window.copy()
        return frame.copy()

    def _mean_f1_per_class(self, reports: list[dict[str, Any]]) -> dict[str, float]:
        return {str(label): float(np.mean([report["f1"][str(label)] for report in reports])) for label in [0, 1, 2]}

    def _validation_sharpe(self, frame: pd.DataFrame, prediction: np.ndarray) -> float:
        if "forward_return_5bar" not in frame.columns:
            return 0.0
        direction = np.where(prediction == 0, 1.0, np.where(prediction == 1, -1.0, 0.0))
        return directional_sharpe(frame["forward_return_5bar"].to_numpy(dtype=float) * direction)


__all__ = ["REGIME_FEATURES", "REGIME_LABEL_FEATURES", "REGIME_LABELS", "RegimeClassifier"]
