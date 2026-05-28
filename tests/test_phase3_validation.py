from __future__ import annotations

import tempfile
from datetime import UTC
from pathlib import Path

import numpy as np
import pandas as pd

from aurum1.models.ablation import run_ensemble_ablation, run_lstm_promotion_gate, run_regime_ablation
from aurum1.models.direction_predictor import DirectionPredictor
from aurum1.models.ensemble import EnsembleSignal
from aurum1.models.regime_classifier import RegimeClassifier
from aurum1.models.utils import classification_report_dict, get_backend_report
from aurum1.signals import MachineMode
from scripts.validate_phase3 import (
    ValidationDataError,
    build_validation_features,
    direction_target_diagnostics,
    synthetic_cot_for,
    synthetic_macro_for,
    synthetic_ohlcv,
)


def settings_for(model_dir: Path) -> dict:
    return {
        "general": {"random_seed": 11},
        "models": {
            "model_dir": str(model_dir),
            "direction": {"max_epochs": 5, "batch_size": 64, "patience": 3, "sequence_length": 10},
            "ensemble": {
                "buy_threshold": 0.60,
                "sell_threshold": -0.60,
                "ranging_buy_threshold": 0.72,
                "ranging_sell_threshold": -0.72,
            },
        },
    }


def regime_fixture(rows: int = 600) -> pd.DataFrame:
    index = pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="15min", tz=UTC)
    step = np.arange(rows, dtype=float)
    bucket = (step.astype(int) // 20) % 3
    frame = pd.DataFrame(index=index)
    frame["adx_14"] = np.select([bucket == 0, bucket == 1, bucket == 2], [36.0, 38.0, 12.0])
    frame["ema_alignment_score"] = np.select([bucket == 0, bucket == 1, bucket == 2], [4, -4, 0]).astype("int64")
    frame["macd_histogram"] = np.select([bucket == 0, bucket == 1, bucket == 2], [1.5, -1.5, 0.0])
    frame["rsi_14"] = np.select([bucket == 0, bucket == 1, bucket == 2], [68.0, 32.0, 50.0])
    frame["ema_9_slope"] = np.select([bucket == 0, bucket == 1, bucket == 2], [0.002, -0.002, 0.0])
    frame["atr_14"] = 2.0 + (bucket * 0.1)
    frame["atr_percentile"] = 0.2 + (bucket * 0.2)
    frame["bb_width"] = 0.01 + (bucket * 0.003)
    frame["rel_volume"] = 1.0 + (bucket * 0.1)
    frame["real_yield"] = 1.0 + step * 0.0001
    frame["dxy_daily_return"] = np.sin(step / 13.0) * 0.001
    frame["vix_level"] = 16.0 + np.cos(step / 17.0)
    frame["session_london"] = ((index.hour >= 7) & (index.hour < 16)).astype("int64")
    frame["session_ny"] = ((index.hour >= 13) & (index.hour < 22)).astype("int64")
    frame["session_overlap"] = ((index.hour >= 13) & (index.hour < 16)).astype("int64")
    frame["hour_sin"] = np.sin(2.0 * np.pi * index.hour / 24.0)
    frame["hour_cos"] = np.cos(2.0 * np.pi * index.hour / 24.0)
    frame["forward_return_5bar"] = np.select([bucket == 0, bucket == 1], [0.001, -0.001], default=0.0)
    frame["label"] = np.select([bucket == 0, bucket == 1], [1, -1], default=0).astype("int64")
    frame["sentiment_bullish"] = np.where(bucket == 0, 0.8, 0.1)
    frame["sentiment_bearish"] = np.where(bucket == 1, 0.8, 0.1)
    frame["sentiment_neutral"] = 1.0 - frame["sentiment_bullish"] * 0.2
    return frame


def trend_fixture(rows: int = 800) -> pd.DataFrame:
    index = pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="15min", tz=UTC)
    segment = (np.arange(rows) // 40) % 3
    close = np.zeros(rows, dtype=float)
    for i in range(1, rows):
        if segment[i] == 0:
            close[i] = close[i - 1] + 1.0
        elif segment[i] == 1:
            close[i] = close[i - 1] - 1.0
        else:
            close[i] = close[i - 1] + (0.2 if i % 2 == 0 else -0.2)
    frame = regime_fixture(rows)
    frame["close"] = close
    rolling_up = pd.Series(close, index=index).diff().rolling(5).apply(lambda x: float((x > 0).all()), raw=False)
    rolling_down = pd.Series(close, index=index).diff().rolling(5).apply(lambda x: float((x < 0).all()), raw=False)
    frame["label"] = np.select([rolling_up == 1.0, rolling_down == 1.0], [1, -1], default=0).astype("int64")
    frame["forward_return_5bar"] = pd.Series(close, index=index).shift(-5) / pd.Series(close + 500.0, index=index) - 1.0
    frame["trend_feature"] = pd.Series(close, index=index).diff().rolling(5).sum().fillna(0.0)
    return frame.fillna(0.0)


def test_backend_report_is_logged(capsys) -> None:
    report = get_backend_report()
    with capsys.disabled():
        print(f"\nAURUM-1 backend report: {report}")

    assert isinstance(report, dict)
    assert set(report) == {"lightgbm", "torch", "transformers"}
    assert set(report.values()).issubset({"real", "fallback"})


def test_regime_classifier_f1_above_chance_on_synthetic(capsys) -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        features = regime_fixture(600)
        classifier = RegimeClassifier(settings_for(Path(tempdir)))
        classifier.train(features.iloc[:400])
        actual = RegimeClassifier.generate_labels(features.iloc[400:]).to_numpy(dtype=int)
        predicted = classifier.predict(features.iloc[400:])
        report = classification_report_dict(actual, predicted, [0, 1, 2])
        with capsys.disabled():
            print(f"\nRegime synthetic confusion matrix: {report['confusion_matrix']}")

    assert report["mean_f1"] > 0.40


def test_direction_predictor_learns_synthetic_trend(capsys) -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        features = trend_fixture(800)
        predictor = DirectionPredictor(settings_for(Path(tempdir)))
        feature_names = [column for column in features.columns if column not in {"label", "forward_return_5bar"}]
        predictor.train(features.iloc[:600], feature_names=feature_names)
        proba = predictor.predict_proba(features.iloc[600:])
        predicted = np.argmax(proba, axis=1)
        actual = features["label"].iloc[600:].map({-1: 0, 0: 1, 1: 2}).iloc[-len(predicted):].to_numpy(dtype=int)
        accuracy = float(np.mean(predicted == actual))
        per_class = {
            str(label): float(np.mean(predicted[actual == label] == label)) if (actual == label).any() else 0.0
            for label in [0, 1, 2]
        }
        with capsys.disabled():
            print(f"\nDirection synthetic per-class accuracy: {per_class}")

    assert accuracy > 0.50


def test_ensemble_signal_distribution_not_all_flat(capsys) -> None:
    ensemble = EnsembleSignal(settings_for(Path("unused")))
    counts = {"BUY": 0, "SELL": 0, "FLAT": 0}
    for row in regime_fixture(200).itertuples():
        if row.ema_alignment_score >= 3:
            regime_proba = np.array([0.95, 0.03, 0.02])
            direction_signal = 0.9
            sentiment = {"positive": 0.8, "negative": 0.1, "neutral": 0.1}
        elif row.ema_alignment_score <= -3:
            regime_proba = np.array([0.03, 0.95, 0.02])
            direction_signal = -0.9
            sentiment = {"positive": 0.1, "negative": 0.8, "neutral": 0.1}
        else:
            regime_proba = np.array([0.05, 0.05, 0.90])
            direction_signal = 0.0
            sentiment = {"positive": 0.0, "negative": 0.0, "neutral": 1.0}
        result = ensemble.combine(regime_proba, direction_signal, sentiment)
        counts[result.direction] += 1
    with capsys.disabled():
        print(f"\nEnsemble signal distribution: {counts}")

    assert counts["FLAT"] != 200
    assert counts["BUY"] + counts["SELL"] >= 10


def test_ablation_framework_runs_without_error() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        result = run_regime_ablation(regime_fixture(600), settings_for(Path(tempdir)))

    assert {"technical", "volatility", "macro", "session", "all"}.issubset(result)
    for group in result.values():
        assert isinstance(group["f1_macro"], float)
    assert result["all"]["f1_macro"] >= result["session"]["f1_macro"]


def test_regime_ablation_reports_per_class_f1() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        result = run_regime_ablation(regime_fixture(600), settings_for(Path(tempdir)))

    assert set(result["technical"]["f1_per_class"]) == {"0", "1", "2"}


def test_regime_ablation_accuracy_is_bounded() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        result = run_regime_ablation(regime_fixture(600), settings_for(Path(tempdir)))

    assert all(0.0 <= group["accuracy"] <= 1.0 for group in result.values())


def test_lstm_promotion_gate_promotes_when_four_metrics_pass() -> None:
    promoted, failed = run_lstm_promotion_gate(
        {"directional_accuracy": 0.50, "profit_factor": 1.0, "sharpe": 0.1, "max_drawdown": 0.1, "calmar": 0.1, "net_return": 0.1},
        {"directional_accuracy": 0.53, "profit_factor": 1.1, "sharpe": 0.16, "max_drawdown": 0.1, "calmar": 0.2, "net_return": 0.2},
    )

    assert promoted is True
    assert failed == []


def test_lstm_promotion_gate_fails_when_too_few_metrics_pass() -> None:
    promoted, failed = run_lstm_promotion_gate(
        {"directional_accuracy": 0.50, "profit_factor": 1.0, "sharpe": 0.1, "max_drawdown": 0.1, "calmar": 0.1, "net_return": 0.1},
        {"directional_accuracy": 0.51, "profit_factor": 1.0, "sharpe": 0.12, "max_drawdown": 0.2, "calmar": 0.09, "net_return": 0.09},
    )

    assert promoted is False
    assert len(failed) >= 3


def test_ensemble_ablation_returns_all_modes() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        result = run_ensemble_ablation(regime_fixture(240), settings_for(Path(tempdir)))

    assert set(result) == {"rule_only", "rule_plus_regime", "rule_plus_regime_lstm", "rule_plus_full"}


def test_ensemble_ablation_metrics_have_required_keys() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        result = run_ensemble_ablation(regime_fixture(240), settings_for(Path(tempdir)))

    expected = {"directional_accuracy", "mean_forward_return_buy", "mean_forward_return_sell", "signal_count"}
    assert all(expected.issubset(metrics) for metrics in result.values())


def test_ensemble_ablation_signal_counts_are_non_negative() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        result = run_ensemble_ablation(regime_fixture(240), settings_for(Path(tempdir)))

    assert all(metrics["signal_count"] >= 0.0 for metrics in result.values())


def test_machine_mode_enum_values() -> None:
    assert MachineMode.RULE_ONLY.value == "rule_only"
    assert MachineMode.RULE_REGIME.value == "rule_regime"
    assert MachineMode.RULE_REGIME_SENT.value == "rule_regime_sent"
    assert MachineMode.FULL_ENSEMBLE.value == "full_ensemble"


def test_validation_synthetic_ohlcv_contract() -> None:
    frame = synthetic_ohlcv(300)

    assert isinstance(frame.index, pd.DatetimeIndex)
    assert frame.index.tz == UTC
    assert {"open", "high", "low", "close", "volume", "source", "instrument"}.issubset(frame.columns)


def test_validation_synthetic_macro_contract() -> None:
    ohlcv = synthetic_ohlcv(300)
    macro = synthetic_macro_for(ohlcv)

    assert isinstance(macro.index, pd.DatetimeIndex)
    assert {"real_yield", "dxy_daily_return", "vix", "vix_1d_change"}.issubset(macro.columns)


def test_validation_synthetic_cot_contract() -> None:
    ohlcv = synthetic_ohlcv(300)
    cot = synthetic_cot_for(ohlcv)

    assert isinstance(cot.index, pd.DatetimeIndex)
    assert "cot_net_long_pct" in cot.columns


def test_validation_requires_real_data_by_default(monkeypatch, tmp_path: Path) -> None:
    settings = {
        "data": {"db_path": str(tmp_path / "aurum.sqlite3")},
        "broker": {
            "oanda": {
                "api_key_env": "OANDA_API_KEY",
                "account_id_env": "OANDA_ACCOUNT_ID",
                "environment_env": "OANDA_ENV",
                "default_environment": "practice",
            }
        },
    }
    monkeypatch.delenv("OANDA_API_KEY", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)

    try:
        build_validation_features(settings, rows=300)
    except ValidationDataError as exc:
        assert "Real market validation requires OANDA credentials" in str(exc)
    else:
        raise AssertionError("Expected real validation to fail without OANDA credentials")


def test_validation_allows_synthetic_only_when_requested(monkeypatch, tmp_path: Path) -> None:
    settings = {
        "data": {"db_path": str(tmp_path / "aurum.sqlite3")},
        "broker": {
            "oanda": {
                "api_key_env": "OANDA_API_KEY",
                "account_id_env": "OANDA_ACCOUNT_ID",
                "environment_env": "OANDA_ENV",
                "default_environment": "practice",
            }
        },
    }
    monkeypatch.delenv("OANDA_API_KEY", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)

    features, report = build_validation_features(settings, allow_synthetic=True, rows=300)

    assert not features.empty
    assert report.validation_mode == "synthetic_plumbing"
    assert report.market_source == "synthetic fallback data"


def test_direction_predictor_all_flat_labels_do_not_default_to_sell(tmp_path: Path) -> None:
    features = regime_fixture(180)
    features["label"] = 0
    features["forward_return_5bar"] = 0.0
    predictor = DirectionPredictor(settings_for(tmp_path))
    feature_names = [column for column in features.columns if column not in {"label", "forward_return_5bar"}]
    predictor.train(features, feature_names=feature_names)

    predicted = np.argmax(predictor.predict_proba(features), axis=1)

    assert set(predicted) == {1}


def test_direction_target_diagnostics_flags_collapsed_labels() -> None:
    features = regime_fixture(300)
    features["label"] = 0

    diagnostics = direction_target_diagnostics(features)

    assert diagnostics["eligible_for_direction_validation"] is False
    assert diagnostics["non_flat_rows"] == 0


def test_regime_classifier_validation_predict_proba_rows_sum_to_one() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        features = regime_fixture(600)
        classifier = RegimeClassifier(settings_for(Path(tempdir)))
        classifier.train(features.iloc[:400])
        proba = classifier.predict_proba(features.iloc[400:])

    assert np.allclose(proba.sum(axis=1), 1.0)
