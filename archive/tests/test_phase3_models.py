from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from aurum1.models.direction_predictor import DirectionPredictor, FocalLoss, find_sequence_boundaries
from aurum1.models.ensemble import EnsembleSignal, SignalResult
from aurum1.models.regime_classifier import REGIME_FEATURES, RegimeClassifier
from aurum1.models.retrainer import ModelRetrainer
from aurum1.models.sentiment_model import SentimentScorer


def model_settings(model_dir: Path) -> dict:
    return {
        "general": {"random_seed": 7},
        "models": {
            "model_dir": str(model_dir),
            "direction": {"max_epochs": 2, "batch_size": 64, "patience": 2, "sequence_length": 60},
            "ensemble": {
                "buy_threshold": 0.60,
                "sell_threshold": -0.60,
                "ranging_buy_threshold": 0.72,
                "ranging_sell_threshold": -0.72,
            },
        },
    }


def synthetic_feature_frame(rows: int = 800) -> pd.DataFrame:
    index = pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="15min", tz=UTC)
    step = np.arange(rows, dtype=float)
    frame = pd.DataFrame(index=index)
    frame["adx_14"] = np.where(step % 90 < 30, 35.0, np.where(step % 90 < 60, 38.0, 12.0))
    frame["ema_alignment_score"] = np.where(step % 90 < 30, 4, np.where(step % 90 < 60, -4, 0)).astype("int64")
    frame["atr_14"] = 2.0 + (np.sin(step / 17.0) * 0.2)
    frame["atr_percentile"] = np.clip((step % 100) / 100.0, 0.01, 1.0)
    frame["bb_width"] = 0.01 + ((step % 20) / 1000.0)
    frame["rsi_14"] = 50.0 + np.sin(step / 9.0) * 20.0
    frame["macd_histogram"] = np.sin(step / 11.0)
    frame["rel_volume"] = 1.0 + np.cos(step / 13.0) * 0.2
    frame["real_yield"] = 1.0 + step * 0.0001
    frame["dxy_daily_return"] = np.sin(step / 23.0) * 0.001
    frame["vix_level"] = 16.0 + np.cos(step / 19.0)
    frame["session_asia"] = ((frame.index.hour >= 0) & (frame.index.hour < 8)).astype("int64")
    frame["session_london"] = ((frame.index.hour >= 7) & (frame.index.hour < 16)).astype("int64")
    frame["session_ny"] = ((frame.index.hour >= 13) & (frame.index.hour < 22)).astype("int64")
    frame["session_overlap"] = ((frame.index.hour >= 13) & (frame.index.hour < 16)).astype("int64")
    frame["hour_sin"] = np.sin(2.0 * np.pi * frame.index.hour / 24.0)
    frame["hour_cos"] = np.cos(2.0 * np.pi * frame.index.hour / 24.0)
    for idx in range(25):
        frame[f"extra_feature_{idx}"] = np.sin(step / (idx + 3.0))
    frame["forward_return_5bar"] = np.sin(step / 15.0) * 0.001
    frame["label"] = np.select(
        [frame["forward_return_5bar"] > 0.0004, frame["forward_return_5bar"] < -0.0004],
        [1, -1],
        default=0,
    ).astype("int64")
    return frame


def test_regime_classifier_trains_and_predicts() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        features = synthetic_feature_frame(500)
        classifier = RegimeClassifier(model_settings(Path(tempdir)))
        classifier.train(features)

        prediction = classifier.predict(features)
        probabilities = classifier.predict_proba(features)

    assert prediction.shape == (500,)
    assert set(np.unique(prediction)).issubset({0, 1, 2})
    assert probabilities.shape == (500, 3)
    assert np.allclose(probabilities.sum(axis=1), 1.0)
    assert classifier.feature_names == REGIME_FEATURES
    assert not {"adx_14", "ema_alignment_score"} & set(classifier.feature_names)


def test_regime_labels_cover_all_three_classes() -> None:
    labels = RegimeClassifier.generate_labels(synthetic_feature_frame(180))

    assert {0, 1, 2}.issubset(set(labels.unique()))


def test_direction_predictor_sequence_boundaries() -> None:
    first = pd.date_range("2026-01-01T00:00:00Z", periods=5, freq="15min", tz=UTC)
    second = pd.date_range("2026-01-01T03:00:00Z", periods=5, freq="15min", tz=UTC)
    index = first.append(second)

    boundaries = find_sequence_boundaries(index, max_gap_minutes=30)

    assert 5 in boundaries


def test_direction_predictor_trains_and_predicts() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        features = synthetic_feature_frame(800)
        predictor = DirectionPredictor(model_settings(Path(tempdir)))
        predictor.train(features)

        probabilities = predictor.predict_proba(features)
        signal = predictor.predict_signal(features)

    assert probabilities.shape[0] >= 1
    assert probabilities.shape[1] == 3
    assert np.allclose(probabilities.sum(axis=1), 1.0)
    assert isinstance(signal, float)
    assert -1.0 <= signal <= 1.0


def test_focal_loss_down_weights_easy_examples() -> None:
    loss = FocalLoss(gamma=2.0)

    confident = loss(np.array([[0.001, 0.998, 0.001]]), np.array([1]))
    uncertain = loss(np.array([[0.25, 0.50, 0.25]]), np.array([1]))

    assert confident < uncertain * 0.01


def test_sentiment_scorer_score_window_filters_by_time() -> None:
    scorer = SentimentScorer({"models": {"model_dir": "unused"}})
    scorer._pipeline = Mock(
        return_value=[
            [
                {"label": "positive", "score": 0.6},
                {"label": "negative", "score": 0.1},
                {"label": "neutral", "score": 0.3},
            ],
            [
                {"label": "positive", "score": 0.6},
                {"label": "negative", "score": 0.1},
                {"label": "neutral", "score": 0.3},
            ],
            [
                {"label": "positive", "score": 0.6},
                {"label": "negative", "score": 0.1},
                {"label": "neutral", "score": 0.3},
            ],
        ]
    )
    as_of = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    headlines = ["in1", "in2", "old1", "in3", "old2"]
    timestamps = [
        as_of - timedelta(hours=1),
        as_of - timedelta(hours=3),
        as_of - timedelta(hours=7),
        as_of - timedelta(hours=5, minutes=59),
        as_of - timedelta(days=1),
    ]

    result = scorer.score_window(headlines, timestamps, as_of, window_hours=6)

    assert np.isclose(result["positive"], 0.6)
    assert np.isclose(result["negative"], 0.1)
    assert np.isclose(result["neutral"], 0.3)
    scorer._pipeline.assert_called_once_with(["in1", "in2", "in3"])


def test_sentiment_scorer_returns_neutral_default_on_empty_window() -> None:
    scorer = SentimentScorer({"models": {"model_dir": "unused"}})
    as_of = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)

    result = scorer.score_window(["old"], [as_of - timedelta(days=1)], as_of, window_hours=6)

    assert result == {"positive": 0.0, "negative": 0.0, "neutral": 1.0, "quality": "empty"}


def test_sentiment_deduplicates_identical_headlines() -> None:
    scorer = SentimentScorer({"models": {"model_dir": "unused"}})
    scorer._pipeline = Mock(
        return_value=[
            [
                {"label": "positive", "score": 0.6},
                {"label": "negative", "score": 0.1},
                {"label": "neutral", "score": 0.3},
            ]
        ]
    )
    as_of = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)

    scorer.score_window(
        ["Gold rallies on dollar weakness!", "gold rallies on dollar weakness", "Gold: rallies on dollar weakness"],
        [as_of, as_of, as_of],
        as_of,
        window_hours=6,
        relevance_scores=[0.9, 0.8, 0.7],
    )

    scorer._pipeline.assert_called_once_with(["Gold rallies on dollar weakness!"])


def test_sentiment_weights_by_relevance() -> None:
    scorer = SentimentScorer({"models": {"model_dir": "unused"}})
    scorer._pipeline = Mock(
        return_value=[
            [
                {"label": "positive", "score": 0.9},
                {"label": "negative", "score": 0.0},
                {"label": "neutral", "score": 0.1},
            ],
            [
                {"label": "positive", "score": 0.0},
                {"label": "negative", "score": 0.9},
                {"label": "neutral", "score": 0.1},
            ],
        ]
    )
    as_of = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)

    result = scorer.score_window(
        ["Gold demand jumps", "Gold demand fades"],
        [as_of, as_of],
        as_of,
        window_hours=6,
        relevance_scores=[0.9, 0.1],
    )

    assert result["positive"] > result["negative"]


def test_sentiment_low_quality_flag_on_single_headline() -> None:
    scorer = SentimentScorer({"models": {"model_dir": "unused"}})
    scorer._pipeline = Mock(
        return_value=[
            [
                {"label": "positive", "score": 0.6},
                {"label": "negative", "score": 0.1},
                {"label": "neutral", "score": 0.3},
            ]
        ]
    )
    as_of = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)

    result = scorer.score_window(["Gold rises"], [as_of], as_of, window_hours=6, relevance_scores=[0.9])

    assert result["quality"] == "low"


def test_ensemble_buy_signal_above_threshold() -> None:
    ensemble = EnsembleSignal(model_settings(Path("unused")))

    result = ensemble.combine(np.array([0.95, 0.03, 0.02]), 0.9, {"positive": 0.8, "negative": 0.1})

    assert result.direction == "BUY"
    assert result.raw_score > 0.60


def test_ensemble_ranging_requires_higher_threshold() -> None:
    ensemble = EnsembleSignal(model_settings(Path("unused")))

    flat = ensemble.combine(np.array([0.0, 0.0, 1.0]), 0.65, {"positive": 0.0, "negative": 0.0})
    buy_with_lower_test_threshold = EnsembleSignal(
        {
            "models": {
                "ensemble": {
                    "buy_threshold": 0.60,
                    "sell_threshold": -0.60,
                    "ranging_buy_threshold": 0.30,
                    "ranging_sell_threshold": -0.72,
                }
            }
        }
    ).combine(np.array([0.0, 0.0, 1.0]), 0.85, {"positive": 0.0, "negative": 0.0})

    assert flat.direction == "FLAT"
    assert buy_with_lower_test_threshold.direction == "BUY"


def test_ensemble_signal_result_fields_populated() -> None:
    ensemble = EnsembleSignal(model_settings(Path("unused")))

    result = ensemble.combine(np.array([0.7, 0.2, 0.1]), 0.2, {"positive": 0.5, "negative": 0.2})

    assert isinstance(result, SignalResult)
    assert result.direction in {"BUY", "SELL", "FLAT"}
    assert isinstance(result.raw_score, float)
    assert isinstance(result.regime, str)
    assert isinstance(result.regime_confidence, float)
    assert isinstance(result.direction_signal, float)
    assert isinstance(result.sentiment_scalar, float)
    assert isinstance(result.timestamp, datetime)


def test_ensemble_treats_low_quality_sentiment_as_neutral() -> None:
    ensemble = EnsembleSignal(model_settings(Path("unused")))

    result = ensemble.combine(
        np.array([0.7, 0.2, 0.1]),
        0.2,
        {"positive": 1.0, "negative": 0.0, "neutral": 0.0, "quality": "low"},
    )

    assert result.sentiment_scalar == 0.0


def test_retrainer_promotes_when_no_existing_model() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        settings = model_settings(Path(tempdir) / "models")
        db_path = Path(tempdir) / "aurum.sqlite3"
        mock_regime = Mock()
        mock_regime.train.return_value = {"validation_sharpe": 0.4}
        mock_regime.evaluate.return_value = {"validation_sharpe": 0.4}
        mock_regime.artifact_path = None
        mock_regime.meta_path = None
        mock_direction = Mock()
        mock_direction.train.return_value = {"validation_sharpe": 0.5}
        mock_direction.evaluate.return_value = {"validation_sharpe": 0.5}
        mock_direction.artifact_path = None
        mock_direction.meta_path = None

        with patch("aurum1.models.retrainer.RegimeClassifier", return_value=mock_regime), patch(
            "aurum1.models.retrainer.DirectionPredictor", return_value=mock_direction
        ):
            result = ModelRetrainer(settings).retrain_all(synthetic_feature_frame(200), db_path)

    assert result == {"regime_classifier": True, "direction_predictor": True}


def test_retrainer_does_not_promote_below_threshold() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        settings = model_settings(Path(tempdir) / "models")
        model_dir = Path(settings["models"]["model_dir"])
        model_dir.mkdir(parents=True, exist_ok=True)
        for name in ("regime_classifier", "direction_predictor"):
            (model_dir / f"{name}_latest_meta.json").write_text(
                json.dumps({"validation_sharpe": 0.78}),
                encoding="utf-8",
            )
        db_path = Path(tempdir) / "aurum.sqlite3"
        mock_model = Mock()
        mock_model.train.return_value = {"validation_sharpe": 0.80}
        mock_model.evaluate.return_value = {"validation_sharpe": 0.80}
        mock_model.artifact_path = None
        mock_model.meta_path = None

        with patch("aurum1.models.retrainer.RegimeClassifier", return_value=mock_model), patch(
            "aurum1.models.retrainer.DirectionPredictor", return_value=mock_model
        ):
            result = ModelRetrainer(settings).retrain_all(synthetic_feature_frame(200), db_path)

    assert result == {"regime_classifier": False, "direction_predictor": False}


def test_model_metadata_sidecar_written_on_save() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        classifier = RegimeClassifier(model_settings(Path(tempdir)))
        metadata = classifier.train(synthetic_feature_frame(500))
        assert classifier.meta_path is not None
        saved = json.loads(classifier.meta_path.read_text(encoding="utf-8"))

    for key in ["training_date", "feature_names", "training_rows", "model_version", "random_seed"]:
        assert key in saved
    assert "validation_f1_per_class" in saved
    assert "ablation_results" in saved
    assert metadata["model_version"] == saved["model_version"]
