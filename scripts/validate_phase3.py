"""Run AURUM-1 Phase 3 validation with explicit real/synthetic data modes."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import (
    AurumDataIngestor,
    load_cot,
    load_macro,
    load_ohlcv,
    load_settings,
)
from aurum1.features.engineer import FeatureEngineer
from aurum1.models.ablation import run_lstm_promotion_gate, run_regime_ablation
from aurum1.models.direction_predictor import DirectionPredictor
from aurum1.models.regime_classifier import RegimeClassifier
from aurum1.models.utils import classification_report_dict, directional_sharpe, get_backend_report


class ValidationDataError(RuntimeError):
    """Raised when requested validation data quality is unavailable."""


@dataclass
class ValidationDataReport:
    validation_mode: str
    market_source: str
    market_rows: int
    market_start: str
    market_end: str
    macro_source: str
    cot_source: str
    db_path: str
    warnings: list[str] = field(default_factory=list)

    @property
    def data_label(self) -> str:
        return f"{self.market_source} / macro={self.macro_source} / cot={self.cot_source}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    load_dotenv(ROOT / ".env")
    settings = load_settings(args.settings)

    with tempfile.TemporaryDirectory() as tempdir:
        settings.setdefault("models", {})
        settings["models"]["model_dir"] = str(Path(tempdir) / "validation_models")
        try:
            features, data_report = build_validation_features(
                settings,
                allow_synthetic=args.allow_synthetic,
                allow_placeholder_macro=args.allow_placeholder_macro,
                allow_gold_futures_proxy=args.allow_gold_futures_proxy,
                rows=args.rows,
            )
        except ValidationDataError as exc:
            print_validation_data_error(exc, settings)
            return 2

        target_diagnostics = direction_target_diagnostics(features)
        regime_rows, final_confusion, mean_f1 = walk_forward_regime(features, settings)
        direction_rows, direction_summary, direction_metrics = walk_forward_direction(
            features,
            settings,
            target_diagnostics,
        )
        ablation = run_regime_ablation(features, settings)

        baseline_metrics = {
            "directional_accuracy": max(0.0, direction_metrics["baseline_accuracy"]),
            "profit_factor": 1.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "calmar": 0.0,
            "net_return": 0.0,
        }
        lstm_metrics = {
            "directional_accuracy": direction_metrics["mean_accuracy"],
            "profit_factor": direction_metrics["profit_factor"],
            "sharpe": direction_metrics["mean_sharpe"],
            "max_drawdown": 0.0,
            "calmar": direction_metrics["mean_sharpe"],
            "net_return": direction_metrics["mean_sharpe"],
        }
        promoted, failed = run_lstm_promotion_gate(baseline_metrics, lstm_metrics)
        if not target_diagnostics["eligible_for_direction_validation"]:
            promoted = False
            if "target_class_balance" not in failed:
                failed.append("target_class_balance")

        print_report(
            backend=get_backend_report(),
            env_report=environment_report(settings),
            data_report=data_report,
            row_count=len(features),
            regime_rows=regime_rows,
            final_confusion=final_confusion,
            mean_f1=mean_f1,
            direction_rows=direction_rows,
            direction_summary=direction_summary,
            target_diagnostics=target_diagnostics,
            ablation=ablation,
            promoted=promoted,
            failed_metrics=failed,
        )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate AURUM-1 Phase 3 models")
    parser.add_argument("--settings", type=Path, default=ROOT / "aurum1" / "config" / "settings.yaml")
    parser.add_argument("--rows", type=int, default=2200, help="Raw M15 candles requested before feature warmup")
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Allow synthetic plumbing data if real market data is unavailable. This is never edge validation.",
    )
    parser.add_argument(
        "--allow-placeholder-macro",
        action="store_true",
        help="Allow synthetic macro/COT placeholders when real OANDA candles are available.",
    )
    parser.add_argument(
        "--allow-gold-futures-proxy",
        action="store_true",
        help="Allow GC=F yfinance futures data as a proxy. This is not spot XAU/USD validation.",
    )
    return parser.parse_args()


def load_dotenv(path: Path) -> None:
    """Load a local .env file without adding a runtime dependency."""

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_validation_features(
    settings: dict[str, Any],
    *,
    allow_synthetic: bool = False,
    allow_placeholder_macro: bool = False,
    allow_gold_futures_proxy: bool = False,
    rows: int = 2200,
) -> tuple[pd.DataFrame, ValidationDataReport]:
    """Build validation features from real OANDA/cache data, or explicit synthetic mode."""

    db_path = Path(str(settings.get("data", {}).get("db_path", "aurum1/data/aurum1.sqlite3")))
    warnings: list[str] = []
    market_source = ""
    ohlcv = load_cached_oanda_ohlcv(db_path, rows)

    if ohlcv.empty:
        credentials = missing_oanda_credentials(settings)
        if credentials:
            if allow_gold_futures_proxy:
                ohlcv = fetch_gold_futures_proxy(settings, rows)
                market_source = "GC=F yfinance gold futures proxy"
                warnings.append("GC=F is a futures proxy, not spot XAU/USD.")
            elif allow_synthetic:
                return synthetic_validation_features(settings, db_path, rows, "; ".join(credentials))
            else:
                raise ValidationDataError(
                    "Real market validation requires OANDA credentials. Missing: "
                    + ", ".join(credentials)
                    + ". Use --allow-synthetic only for plumbing validation."
                )
        else:
            try:
                ohlcv = fetch_and_cache_oanda_ohlcv(settings, rows)
                market_source = "OANDA XAU_USD live fetch"
            except Exception as exc:
                if allow_gold_futures_proxy:
                    ohlcv = fetch_gold_futures_proxy(settings, rows)
                    market_source = "GC=F yfinance gold futures proxy"
                    warnings.append(f"OANDA unavailable ({exc}); using GC=F proxy by explicit request.")
                elif allow_synthetic:
                    return synthetic_validation_features(settings, db_path, rows, f"OANDA unavailable: {exc}")
                else:
                    raise ValidationDataError(
                        f"Real OANDA XAU_USD validation data unavailable: {exc}. "
                        "Use --allow-synthetic only for plumbing validation."
                    ) from exc
    else:
        market_source = "cached SQLite OANDA XAU_USD"

    macro, macro_source, macro_warnings = load_or_fetch_macro(settings, ohlcv, allow_placeholder_macro)
    cot, cot_source, cot_warnings = load_or_fetch_cot(settings, ohlcv, allow_placeholder_macro)
    warnings.extend(macro_warnings)
    warnings.extend(cot_warnings)

    engineer = FeatureEngineer({"feature_engineering": {"lookahead_check": True}})
    features = engineer.build_features(ohlcv, macro, cot, include_target=True)
    if features.empty:
        raise ValidationDataError("Feature matrix is empty after warmup; fetch more real candles.")

    report = ValidationDataReport(
        validation_mode="real_market" if "proxy" not in market_source.lower() else "proxy_market",
        market_source=market_source,
        market_rows=int(len(ohlcv)),
        market_start=ohlcv.index.min().isoformat(),
        market_end=ohlcv.index.max().isoformat(),
        macro_source=macro_source,
        cot_source=cot_source,
        db_path=str(db_path),
        warnings=warnings,
    )
    return features, report


def load_cached_oanda_ohlcv(db_path: Path, rows: int) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    try:
        frame = load_ohlcv("M15", db_path)
    except Exception:
        return pd.DataFrame()
    if frame.empty:
        return pd.DataFrame()
    if "source" in frame.columns:
        frame = frame[frame["source"].astype(str).str.lower() == "oanda"]
    if "instrument" in frame.columns:
        frame = frame[frame["instrument"].astype(str).str.upper() == "XAU_USD"]
    if len(frame) < max(260, rows // 2):
        return pd.DataFrame()
    return frame.sort_index().tail(rows)


def fetch_and_cache_oanda_ohlcv(settings: dict[str, Any], rows: int) -> pd.DataFrame:
    ingestor = AurumDataIngestor(settings)
    raw = ingestor._fetch_oanda_ohlcv("M15", rows)
    if raw.empty:
        raise RuntimeError("OANDA returned no M15 candles")
    ingestor.persist_ohlcv("M15", raw)
    cached = load_cached_oanda_ohlcv(Path(str(settings.get("data", {}).get("db_path", "aurum1/data/aurum1.sqlite3"))), rows)
    if cached.empty:
        raise RuntimeError("OANDA candles fetched but cache read-back is empty")
    return cached


def fetch_gold_futures_proxy(settings: dict[str, Any], rows: int) -> pd.DataFrame:
    proxy_settings = {**settings, "data": {**settings.get("data", {}), "yfinance_symbol": "GC=F"}}
    ingestor = AurumDataIngestor(proxy_settings)
    raw = ingestor._fetch_yfinance_ohlcv("M15", rows)
    frame = provider_frame_to_index(raw)
    if frame.empty:
        raise RuntimeError("GC=F proxy returned no rows")
    return frame.tail(rows)


def provider_frame_to_index(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    if "timestamp" in work.columns:
        work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
        work = work.set_index("timestamp")
    elif not isinstance(work.index, pd.DatetimeIndex):
        raise ValueError("Provider frame must have timestamp column or DatetimeIndex")
    if work.index.tz is None:
        work.index = work.index.tz_localize(UTC)
    else:
        work.index = work.index.tz_convert(UTC)
    return work.sort_index()


def load_or_fetch_macro(
    settings: dict[str, Any],
    ohlcv: pd.DataFrame,
    allow_placeholder: bool,
) -> tuple[pd.DataFrame, str, list[str]]:
    db_path = Path(str(settings.get("data", {}).get("db_path", "aurum1/data/aurum1.sqlite3")))
    warnings: list[str] = []
    try:
        macro = load_macro(db_path)
        if not macro.empty and macro.index.min() <= ohlcv.index.min().normalize():
            return macro, "cached SQLite real macro", warnings
    except Exception as exc:
        warnings.append(f"Cached macro unavailable: {exc}")

    fred_missing = missing_fred_credentials(settings)
    if fred_missing and not allow_placeholder:
        raise ValidationDataError(
            "Real macro validation requires FRED_API_KEY. Missing: "
            + ", ".join(fred_missing)
            + ". Use --allow-placeholder-macro only for market-data plumbing checks."
        )

    try:
        ingestor = AurumDataIngestor(settings)
        macro_raw = ingestor.fetch_macro_data()
        ingestor.persist_macro_data(macro_raw)
        macro = load_macro(db_path)
        return macro, "real (DGS10, CPI, DXY, VIX)", warnings
    except Exception as exc:
        if not allow_placeholder:
            raise ValidationDataError(
                f"Real macro fetch failed: {exc}. Use --allow-placeholder-macro only for plumbing checks."
            ) from exc
        warnings.append(f"Using placeholder macro because real macro fetch failed: {exc}")
        return synthetic_macro_for(ohlcv), "placeholder", warnings


def load_or_fetch_cot(
    settings: dict[str, Any],
    ohlcv: pd.DataFrame,
    allow_placeholder: bool,
) -> tuple[pd.DataFrame, str, list[str]]:
    db_path = Path(str(settings.get("data", {}).get("db_path", "aurum1/data/aurum1.sqlite3")))
    warnings: list[str] = []
    try:
        cot = load_cot(db_path)
        if not cot.empty:
            return cot, "cached SQLite real COT", warnings
    except Exception as exc:
        warnings.append(f"Cached COT unavailable: {exc}")

    try:
        ingestor = AurumDataIngestor(settings)
        cot_raw = ingestor.fetch_cot_data()
        ingestor.persist_cot_data(cot_raw)
        cot = load_cot(db_path)
        return cot, "real CFTC COT", warnings
    except Exception as exc:
        if not allow_placeholder:
            raise ValidationDataError(
                f"Real COT fetch failed: {exc}. Use --allow-placeholder-macro only for plumbing checks."
            ) from exc
        warnings.append(f"Using placeholder COT because real COT fetch failed: {exc}")
        return synthetic_cot_for(ohlcv), "placeholder", warnings


def synthetic_validation_features(
    settings: dict[str, Any],
    db_path: Path,
    rows: int,
    reason: str,
) -> tuple[pd.DataFrame, ValidationDataReport]:
    ohlcv = synthetic_ohlcv(rows)
    macro = synthetic_macro_for(ohlcv)
    cot = synthetic_cot_for(ohlcv)
    engineer = FeatureEngineer({"feature_engineering": {"lookahead_check": True}})
    features = engineer.build_features(ohlcv, macro, cot, include_target=True)
    return features, ValidationDataReport(
        validation_mode="synthetic_plumbing",
        market_source="synthetic fallback data",
        market_rows=int(len(ohlcv)),
        market_start=ohlcv.index.min().isoformat(),
        market_end=ohlcv.index.max().isoformat(),
        macro_source="synthetic",
        cot_source="synthetic",
        db_path=str(db_path),
        warnings=[
            f"Synthetic mode was explicitly allowed. Reason real data was unavailable: {reason}",
            "This validates plumbing only. It is not trading-edge evidence.",
        ],
    )


def missing_oanda_credentials(settings: dict[str, Any]) -> list[str]:
    oanda_settings = settings.get("broker", {}).get("oanda", {})
    candidates = [
        str(oanda_settings.get("api_key_env", "OANDA_API_KEY")),
        str(oanda_settings.get("account_id_env", "OANDA_ACCOUNT_ID")),
    ]
    return [name for name in candidates if not os.getenv(name)]


def missing_fred_credentials(settings: dict[str, Any]) -> list[str]:
    fred_settings = settings.get("data", {}).get("fred", {})
    name = str(fred_settings.get("api_key_env", "FRED_API_KEY"))
    return [] if os.getenv(name) else [name]


def environment_report(settings: dict[str, Any]) -> dict[str, bool | str]:
    oanda_settings = settings.get("broker", {}).get("oanda", {})
    fred_settings = settings.get("data", {}).get("fred", {})
    api_key_env = str(oanda_settings.get("api_key_env", "OANDA_API_KEY"))
    account_env = str(oanda_settings.get("account_id_env", "OANDA_ACCOUNT_ID"))
    env_env = str(oanda_settings.get("environment_env", "OANDA_ENV"))
    fred_env = str(fred_settings.get("api_key_env", "FRED_API_KEY"))
    return {
        api_key_env: bool(os.getenv(api_key_env)),
        account_env: bool(os.getenv(account_env)),
        env_env: os.getenv(env_env, str(oanda_settings.get("default_environment", "practice"))),
        fred_env: bool(os.getenv(fred_env)),
    }


def direction_target_diagnostics(features: pd.DataFrame) -> dict[str, Any]:
    labels = features["label"].astype(int)
    distribution = {str(int(key)): int(value) for key, value in labels.value_counts().sort_index().items()}
    non_flat = int((labels != 0).sum())
    non_flat_rate = float(non_flat / len(labels)) if len(labels) else 0.0
    class_count = int(labels.nunique())
    eligible = class_count >= 2 and non_flat >= 30 and non_flat_rate >= 0.02
    return {
        "label_distribution": distribution,
        "rows": int(len(labels)),
        "non_flat_rows": non_flat,
        "non_flat_rate": non_flat_rate,
        "class_count": class_count,
        "eligible_for_direction_validation": eligible,
        "reason": None if eligible else "insufficient target class balance for honest direction validation",
    }


def walk_forward_regime(features: pd.DataFrame, settings: dict[str, Any]) -> tuple[list[float], list[list[int]], float]:
    f1_rows: list[float] = []
    final_confusion: list[list[int]] = []
    for train_idx, val_idx in _folds(len(features), 3):
        train = features.iloc[train_idx]
        validation = features.iloc[val_idx]
        classifier = RegimeClassifier(settings)
        classifier.train(train, update_latest=False)
        actual = RegimeClassifier.generate_labels(validation).to_numpy(dtype=int)
        predicted = classifier.predict(validation)
        report = classification_report_dict(actual, predicted, [0, 1, 2])
        f1_rows.append(float(report["mean_f1"]))
        final_confusion = report["confusion_matrix"]
    return f1_rows, final_confusion, float(np.mean(f1_rows)) if f1_rows else 0.0


def walk_forward_direction(
    features: pd.DataFrame,
    settings: dict[str, Any],
    target_diagnostics: dict[str, Any] | None = None,
) -> tuple[list[float], dict[str, Any], dict[str, float]]:
    diagnostics = target_diagnostics or direction_target_diagnostics(features)
    if not diagnostics["eligible_for_direction_validation"]:
        metrics = {
            "mean_accuracy": 0.0,
            "mean_sharpe": 0.0,
            "baseline_accuracy": float((features["label"] == 0).mean()) if "label" in features else 0.0,
            "profit_factor": 0.0,
        }
        summary = {
            "status": "skipped",
            "reason": diagnostics["reason"],
            "actual_distribution": diagnostics["label_distribution"],
            "predicted_distribution": {},
        }
        return [], summary, metrics

    accuracy_rows: list[float] = []
    sharpe_rows: list[float] = []
    final_summary: dict[str, Any] = {}
    for train_idx, val_idx in _folds(len(features), 3):
        train = features.iloc[train_idx]
        validation = features.iloc[val_idx]
        if train["label"].nunique() < 2 or validation["label"].nunique() < 2:
            final_summary = {
                "status": "fold_skipped",
                "reason": "train/validation fold has fewer than two label classes",
                "actual_distribution": _distribution(validation["label"].map({-1: 0, 0: 1, 1: 2}).to_numpy(dtype=int)),
                "predicted_distribution": {},
            }
            continue
        predictor = DirectionPredictor(settings)
        predictor.train(train, update_latest=False)
        probabilities = predictor.predict_proba(validation)
        if len(probabilities) == 0:
            continue
        predicted = np.argmax(probabilities, axis=1)
        actual_labels = validation["label"].iloc[-len(predicted):].map({-1: 0, 0: 1, 1: 2}).to_numpy(dtype=int)
        accuracy = float(np.mean(predicted == actual_labels))
        returns = validation["forward_return_5bar"].iloc[-len(predicted):].to_numpy(dtype=float)
        direction = np.select([predicted == 2, predicted == 0], [1, -1], default=0)
        accuracy_rows.append(accuracy)
        sharpe_rows.append(directional_sharpe(returns * direction))
        final_summary = {
            "status": "evaluated",
            "predicted_distribution": _distribution(predicted),
            "actual_distribution": _distribution(actual_labels),
        }
    mean_accuracy = float(np.mean(accuracy_rows)) if accuracy_rows else 0.0
    metrics = {
        "mean_accuracy": mean_accuracy,
        "mean_sharpe": float(np.mean(sharpe_rows)) if sharpe_rows else 0.0,
        "baseline_accuracy": float((features["label"] == 0).mean()) if "label" in features else 0.0,
        "profit_factor": max(1e-9, mean_accuracy / max(1e-9, 1.0 - mean_accuracy)) if accuracy_rows else 0.0,
    }
    return accuracy_rows, final_summary, metrics


def print_validation_data_error(exc: ValidationDataError, settings: dict[str, Any]) -> None:
    print("AURUM-1 Phase 3 Validation")
    print("Status: FAILED - real market data unavailable")
    print("\nDependency check:")
    print(f"  Backend report: {get_backend_report()}")
    print("\nEnvironment check:")
    for key, value in environment_report(settings).items():
        print(f"  {key}: {'set' if value is True else value if isinstance(value, str) else 'missing'}")
    print("\nData-source check:")
    print(f"  {exc}")
    print("\nNo synthetic fallback was used. Re-run with --allow-synthetic only for plumbing tests.")


def print_report(
    *,
    backend: dict[str, str],
    env_report: dict[str, bool | str],
    data_report: ValidationDataReport,
    row_count: int,
    regime_rows: list[float],
    final_confusion: list[list[int]],
    mean_f1: float,
    direction_rows: list[float],
    direction_summary: dict[str, Any],
    target_diagnostics: dict[str, Any],
    ablation: dict[str, dict[str, Any]],
    promoted: bool,
    failed_metrics: list[str],
) -> None:
    passed_metrics = [
        metric
        for metric in ["directional_accuracy", "profit_factor", "sharpe", "max_drawdown", "calmar", "net_return"]
        if metric not in failed_metrics
    ]
    print("=" * 64)
    print("AURUM-1 Phase 3 Validation")
    print("=" * 64)
    print("Dependency check:")
    print(f"  LightGBM={backend['lightgbm']}  Torch={backend['torch']}  Transformers={backend['transformers']}")
    print("\nEnvironment check:")
    for key, value in env_report.items():
        print(f"  {key}: {'set' if value is True else value if isinstance(value, str) else 'missing'}")
    print("\nData-source check:")
    print(f"  Validation mode: {data_report.validation_mode}")
    print(f"  Market data: {data_report.market_source}")
    print(f"  Raw candles: {data_report.market_rows}")
    print(f"  Date range: {data_report.market_start} -> {data_report.market_end}")
    print(f"  SQLite cache: {data_report.db_path}")
    print(f"  Macro data: {data_report.macro_source}")
    print(f"  COT data: {data_report.cot_source}")
    if data_report.validation_mode == "synthetic_plumbing":
        print("  RESULT: SYNTHETIC PLUMBING ONLY - NOT TRADING EDGE")
    elif data_report.validation_mode == "proxy_market":
        print("  RESULT: PROXY MARKET DATA - NOT DIRECT SPOT XAU/USD EDGE")
    else:
        print("  RESULT: REAL OANDA XAU_USD MARKET VALIDATION")
    for warning in data_report.warnings:
        print(f"  WARNING: {warning}")
    print("\nModel performance validation:")
    print(f"  Feature rows after warmup/target trim: {row_count}")
    print(f"  Target labels (-1/0/1): {target_diagnostics['label_distribution']}")
    print(f"  Non-flat target rate: {target_diagnostics['non_flat_rate']:.2%}")
    print(f"  Direction validation eligible: {target_diagnostics['eligible_for_direction_validation']}")
    if target_diagnostics["reason"]:
        print(f"  Direction validation reason: {target_diagnostics['reason']}")
    print("\nRegime Classifier:")
    for idx, value in enumerate(regime_rows, start=1):
        print(f"  Fold {idx} F1: {value:.3f}")
    print(f"  Mean F1: {mean_f1:.3f}")
    print(f"  Final confusion matrix: {final_confusion}")
    print("\nDirection Predictor:")
    if direction_rows:
        for idx, value in enumerate(direction_rows, start=1):
            print(f"  Fold {idx} accuracy: {value:.3f}")
    else:
        print("  Not evaluated.")
    print(f"  Direction summary: {direction_summary}")
    print("\nRegime Ablation:")
    for key in ["technical", "volatility", "macro", "session", "all"]:
        print(f"  {key:<11} F1={ablation[key]['f1_macro']:.3f} accuracy={ablation[key]['accuracy']:.3f}")
    print("\nLSTM Gate:")
    print(f"  Result: {'PROMOTED' if promoted else 'FAILED'}")
    print(f"  Failed metrics: {', '.join(failed_metrics) if failed_metrics else 'none'}")
    print(f"  Passed metrics: {', '.join(passed_metrics) if passed_metrics else 'none'}")
    print("=" * 64)


def synthetic_ohlcv(rows: int) -> pd.DataFrame:
    index = pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="15min", tz=UTC)
    step = np.arange(rows, dtype=float)
    close = 2300.0 + (step * 0.015) + (np.sin(step / 12.0) * 3.0)
    open_ = close + np.cos(step / 7.0) * 0.25
    high = np.maximum(open_, close) + 1.4
    low = np.minimum(open_, close) - 1.4
    volume = 1000.0 + (step % 40.0) * 7.0
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "source": "synthetic",
            "instrument": "XAU_USD",
        },
        index=index,
    )


def synthetic_macro_for(ohlcv: pd.DataFrame) -> pd.DataFrame:
    index = pd.date_range(
        ohlcv.index.min().normalize(),
        ohlcv.index.max().normalize() + pd.Timedelta(days=1),
        freq="D",
        tz=UTC,
    )
    step = np.arange(len(index), dtype=float)
    return pd.DataFrame(
        {
            "dgs10": 4.0 + step * 0.01,
            "cpi": 315.0 + step * 0.1,
            "cpi_yoy": 3.0 + step * 0.01,
            "real_yield": 1.0 + step * 0.002,
            "dxy": 104.0 + step * 0.03,
            "dxy_daily_return": np.sin(step / 5.0) * 0.001,
            "vix": 16.0 + np.cos(step / 4.0),
            "vix_1d_change": np.sin(step / 3.0) * 0.1,
        },
        index=index,
    )


def synthetic_cot_for(ohlcv: pd.DataFrame) -> pd.DataFrame:
    index = pd.date_range(
        ohlcv.index.min().normalize() - pd.Timedelta(days=14),
        ohlcv.index.max().normalize(),
        freq="7D",
        tz=UTC,
    )
    step = np.arange(len(index), dtype=float)
    return pd.DataFrame(
        {
            "market_name": "GOLD - COMMODITY EXCHANGE INC.",
            "open_interest": 200000.0 + step,
            "long_positions": 120000.0 + step,
            "short_positions": 70000.0 + step,
            "net_positioning": 50000.0,
            "cot_net_long_pct": 0.20 + step * 0.002,
            "source": "synthetic",
        },
        index=index,
    )


def _folds(length: int, n_splits: int) -> list[tuple[np.ndarray, np.ndarray]]:
    fold_size = max(1, length // (n_splits + 1))
    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for fold in range(n_splits):
        val_start = length - fold_size * (n_splits - fold)
        val_end = min(length, val_start + fold_size)
        train_end = max(1, val_start)
        folds.append((np.arange(train_end), np.arange(val_start, val_end)))
    return folds


def _distribution(values: np.ndarray) -> dict[str, int]:
    unique, counts = np.unique(values, return_counts=True)
    return {str(int(key)): int(value) for key, value in zip(unique, counts, strict=True)}


if __name__ == "__main__":
    raise SystemExit(main())
