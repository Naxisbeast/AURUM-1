"""Weekly retraining and promotion logic for AURUM-1 Phase 3."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from aurum1.data.ingestion import initialize_database
from aurum1.models.direction_predictor import DirectionPredictor
from aurum1.models.regime_classifier import RegimeClassifier
from aurum1.models.utils import copy_to_latest, model_dir_from_settings, read_latest_metadata


class ModelRetrainer:
    """Retrain Phase 3 models and promote only when validation evidence improves."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.model_dir = model_dir_from_settings(settings)

    def retrain_all(self, feature_df: pd.DataFrame, db_path: str | Path) -> dict[str, bool]:
        """Train regime and direction models and return promotion decisions."""

        validation = self._validation_window(feature_df)
        decisions: dict[str, bool] = {}
        models: dict[str, Any] = {
            "regime_classifier": RegimeClassifier(self.settings),
        }
        if bool(self.settings.get("models", {}).get("enable_direction_predictor", False)):
            models["direction_predictor"] = DirectionPredictor(self.settings)
        for model_name, model in models.items():
            metadata = model.train(feature_df, update_latest=False)
            evaluation = model.evaluate(validation) if len(validation) else {"validation_sharpe": 0.0}
            new_sharpe = float(evaluation.get("validation_sharpe", metadata.get("validation_sharpe", 0.0)))
            old_metadata = read_latest_metadata(self.settings, model_name)
            old_sharpe = None if old_metadata is None else float(old_metadata.get("validation_sharpe", 0.0))
            promoted = old_sharpe is None or new_sharpe > old_sharpe + 0.05
            decisions[model_name] = promoted
            if promoted and model.artifact_path is not None and model.meta_path is not None:
                copy_to_latest(model_name, ".pkl", model.artifact_path, model.meta_path, self.model_dir)
            self._log_decision(
                db_path,
                model_name,
                {
                    "old_sharpe": old_sharpe,
                    "new_sharpe": new_sharpe,
                    "promoted": promoted,
                    "training_rows": int(len(feature_df)),
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        return decisions

    def log_ensemble_ablation_results(self, db_path: str | Path, results: dict[str, dict[str, float]]) -> None:
        """Log Phase 3 ensemble ablation result sets to performance_log."""

        initialize_database(db_path)
        with closing(sqlite3.connect(Path(db_path))) as conn:
            with conn:
                for version_name, metrics in results.items():
                    conn.execute(
                        """
                        INSERT INTO performance_log
                        (timestamp, metric_name, metric_value, payload_json)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            datetime.now(UTC).isoformat(),
                            f"ensemble_ablation_{version_name}",
                            float(metrics.get("sharpe", 0.0)),
                            json.dumps(metrics, sort_keys=True),
                        ),
                    )

    def _validation_window(self, feature_df: pd.DataFrame) -> pd.DataFrame:
        if isinstance(feature_df.index, pd.DatetimeIndex) and not feature_df.empty:
            cutoff = feature_df.index.max() - timedelta(days=30)
            window = feature_df.loc[feature_df.index >= cutoff]
            if not window.empty:
                return window
        return feature_df.tail(max(1, min(len(feature_df), 100)))

    def _log_decision(self, db_path: str | Path, model_name: str, payload: dict[str, Any]) -> None:
        initialize_database(db_path)
        with closing(sqlite3.connect(Path(db_path))) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO performance_log
                    (timestamp, metric_name, metric_value, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        datetime.now(UTC).isoformat(),
                        f"retraining_{model_name}",
                        float(payload["new_sharpe"]),
                        json.dumps(payload, sort_keys=True, default=str),
                    ),
                )


__all__ = ["ModelRetrainer"]
