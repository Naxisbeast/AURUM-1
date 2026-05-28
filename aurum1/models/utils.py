"""Shared utilities for Phase 3 model training, metrics, and artifacts."""

from __future__ import annotations

import json
import os
import pickle
import random
import shutil
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import numpy as np


def get_backend_report() -> dict[str, str]:
    """Report whether optional ML backends are active or using fallbacks."""

    return {
        "lightgbm": "real" if importlib.util.find_spec("lightgbm") is not None else "fallback",
        "torch": "real" if importlib.util.find_spec("torch") is not None else "fallback",
        "transformers": "real" if importlib.util.find_spec("transformers") is not None else "fallback",
    }


def set_random_seeds(seed: int) -> None:
    """Set every available random seed used by Phase 3 train functions."""

    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass


def random_seed_from_settings(settings: dict[str, Any]) -> int:
    """Read the configured random seed with conservative defaults."""

    return int(settings.get("general", {}).get("random_seed", settings.get("app", {}).get("random_seed", 42)))


def model_dir_from_settings(settings: dict[str, Any]) -> Path:
    """Resolve and create the model artifact directory."""

    model_dir = Path(str(settings.get("models", {}).get("model_dir", "aurum1/models/artifacts")))
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir


def timestamp_version() -> str:
    """Return a filesystem-friendly UTC timestamp."""

    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def time_series_splits(
    n_samples: int,
    n_splits: int = 5,
    gap: int = 0,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Yield deterministic expanding-window time-series splits."""

    if n_samples < 3:
        return
    usable_splits = max(1, min(n_splits, n_samples - 2))
    test_size = max(1, n_samples // (usable_splits + 1))
    for split in range(usable_splits):
        test_start = n_samples - test_size * (usable_splits - split)
        train_end = max(1, test_start - gap)
        test_end = min(n_samples, test_start + test_size)
        if train_end <= 0 or test_start >= test_end:
            continue
        yield np.arange(train_end), np.arange(test_start, test_end)


def classification_report_dict(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    classes: list[int],
) -> dict[str, Any]:
    """Compute precision, recall, F1, and confusion matrix without sklearn."""

    confusion = np.zeros((len(classes), len(classes)), dtype=int)
    class_to_index = {label: index for index, label in enumerate(classes)}
    for true, pred in zip(y_true, y_pred, strict=False):
        if int(true) in class_to_index and int(pred) in class_to_index:
            confusion[class_to_index[int(true)], class_to_index[int(pred)]] += 1

    precision: dict[str, float] = {}
    recall: dict[str, float] = {}
    f1: dict[str, float] = {}
    for label in classes:
        idx = class_to_index[label]
        tp = float(confusion[idx, idx])
        fp = float(confusion[:, idx].sum() - confusion[idx, idx])
        fn = float(confusion[idx, :].sum() - confusion[idx, idx])
        precision[str(label)] = tp / (tp + fp) if (tp + fp) else 0.0
        recall[str(label)] = tp / (tp + fn) if (tp + fn) else 0.0
        denom = precision[str(label)] + recall[str(label)]
        f1[str(label)] = 2.0 * precision[str(label)] * recall[str(label)] / denom if denom else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mean_f1": float(np.mean(list(f1.values()))) if f1 else 0.0,
        "confusion_matrix": confusion.tolist(),
    }


def directional_sharpe(returns: np.ndarray) -> float:
    """Annualized Sharpe for M15-style directional returns."""

    cleaned = np.asarray(returns, dtype=float)
    cleaned = cleaned[np.isfinite(cleaned)]
    if cleaned.size == 0:
        return 0.0
    std = float(np.std(cleaned))
    if std == 0.0:
        return 0.0
    return float(np.mean(cleaned) / std * np.sqrt(252 * 26))


def save_pickle_artifact(
    model_name: str,
    suffix: str,
    payload: Any,
    metadata: dict[str, Any],
    settings: dict[str, Any],
    *,
    update_latest: bool = True,
) -> tuple[Path, Path]:
    """Save a versioned pickle artifact and JSON metadata sidecar."""

    model_dir = model_dir_from_settings(settings)
    version = str(metadata["model_version"])
    artifact_path = model_dir / f"{model_name}_{version}{suffix}"
    meta_path = model_dir / f"{model_name}_{version}_meta.json"
    with artifact_path.open("wb") as handle:
        pickle.dump(payload, handle)
    meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    if update_latest:
        copy_to_latest(model_name, suffix, artifact_path, meta_path, model_dir)
    return artifact_path, meta_path


def copy_to_latest(
    model_name: str,
    suffix: str,
    artifact_path: Path,
    meta_path: Path,
    model_dir: Path,
) -> None:
    """Copy the most recent artifact to a stable latest filename."""

    latest_model = model_dir / f"{model_name}_latest{suffix}"
    latest_meta = model_dir / f"{model_name}_latest_meta.json"
    shutil.copy2(artifact_path, latest_model)
    shutil.copy2(meta_path, latest_meta)


def read_latest_metadata(settings: dict[str, Any], model_name: str) -> dict[str, Any] | None:
    """Read deployed latest metadata when present."""

    meta_path = model_dir_from_settings(settings) / f"{model_name}_latest_meta.json"
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def load_pickle(path: str | Path) -> Any:
    """Load a pickle artifact."""

    with Path(path).open("rb") as handle:
        return pickle.load(handle)


def atomic_log_json(value: Any) -> str:
    """Serialize log payloads consistently for SQLite performance logs."""

    return json.dumps(value, sort_keys=True, default=str)
