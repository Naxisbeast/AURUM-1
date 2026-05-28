"""FinBERT-based sentiment scoring for AURUM-1 Phase 3."""

from __future__ import annotations

import threading
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from aurum1.models.utils import model_dir_from_settings


NEUTRAL_SENTIMENT = {"positive": 0.0, "negative": 0.0, "neutral": 1.0}
EMPTY_SENTIMENT = {"positive": 0.0, "negative": 0.0, "neutral": 1.0, "quality": "empty"}


class SentimentScorer:
    """Lazy, thread-safe FinBERT headline scorer."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._pipeline: Any | None = None

    def score_headlines(self, headlines: list[str]) -> list[dict[str, float]]:
        """Score headlines as positive, negative, and neutral probabilities."""

        unique_headlines = _deduplicate_headlines(headlines)
        if not unique_headlines:
            return []
        with self._lock:
            scorer = self._load_pipeline()
            raw_scores = scorer(unique_headlines)
        return [self._normalize_pipeline_output(item) for item in raw_scores]

    def score_window(
        self,
        headlines: list[str],
        timestamps: list[datetime],
        as_of: datetime,
        window_hours: int = 6,
        relevance_scores: list[float | None] | None = None,
    ) -> dict[str, float | str]:
        """Score weighted headline sentiment in the trailing time window."""

        as_of_utc = _to_utc(as_of)
        window_start = as_of_utc - timedelta(hours=window_hours)
        relevance_values = relevance_scores if relevance_scores is not None else [None] * len(headlines)
        filtered = _deduplicate_window_items(
            [
                (headline, _relevance_or_default(relevance))
                for headline, timestamp, relevance in zip(headlines, timestamps, relevance_values, strict=False)
                if window_start <= _to_utc(timestamp) <= as_of_utc
            ]
        )
        if not filtered:
            return dict(EMPTY_SENTIMENT)

        filtered_headlines = [headline for headline, _ in filtered]
        weights = np.asarray([weight for _, weight in filtered], dtype=float)
        scores = self.score_headlines(filtered_headlines)
        quality = _quality_label(len(scores), weights)
        sentiment_values = {
            "positive": np.asarray([score["positive"] for score in scores], dtype=float),
            "negative": np.asarray([score["negative"] for score in scores], dtype=float),
            "neutral": np.asarray([score["neutral"] for score in scores], dtype=float),
        }
        if np.sum(weights) == 0.0:
            weighted = {key: float(np.mean(values)) for key, values in sentiment_values.items()}
        else:
            weighted = {key: float(np.sum(values * weights) / np.sum(weights)) for key, values in sentiment_values.items()}
        return {**weighted, "quality": quality}

    def _load_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        try:
            from transformers import pipeline
        except ImportError as exc:
            raise RuntimeError("transformers is required for live FinBERT sentiment scoring") from exc
        cache_dir = model_dir_from_settings(self.settings) / "finbert"
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._pipeline = pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            return_all_scores=True,
            cache_dir=str(cache_dir),
        )
        return self._pipeline

    def _normalize_pipeline_output(self, raw: Any) -> dict[str, float]:
        if isinstance(raw, dict):
            raw_items = [raw]
        else:
            raw_items = list(raw)
        scores = {"positive": 0.0, "negative": 0.0, "neutral": 0.0}
        for item in raw_items:
            label = str(item.get("label", "")).lower()
            score = float(item.get("score", 0.0))
            if "pos" in label:
                scores["positive"] = score
            elif "neg" in label:
                scores["negative"] = score
            elif "neu" in label:
                scores["neutral"] = score
        total = sum(scores.values())
        if total <= 0.0:
            return dict(NEUTRAL_SENTIMENT)
        return {key: float(value / total) for key, value in scores.items()}


def _normalize_title(title: str) -> str:
    stripped = re.sub(r"[^\w\s]", " ", title.lower())
    return " ".join(stripped.split())


def _deduplicate_headlines(headlines: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for headline in headlines:
        normalized = _normalize_title(headline)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(headline)
    return result


def _deduplicate_window_items(items: list[tuple[str, float]]) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    seen: set[str] = set()
    for headline, relevance in items:
        normalized = _normalize_title(headline)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append((headline, relevance))
    return result


def _relevance_or_default(value: float | None) -> float:
    if value is None:
        return 0.5
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.5


def _quality_label(headline_count: int, weights: np.ndarray) -> str:
    if headline_count == 0:
        return "empty"
    mean_relevance = float(np.mean(weights)) if weights.size else 0.0
    if headline_count >= 3 and mean_relevance > 0.5:
        return "good"
    return "low"


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["SentimentScorer"]
