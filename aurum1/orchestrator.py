"""Top-level AURUM-1 orchestrator.

The orchestrator is the only layer that wires data, features, models, signals,
risk, and execution together. Lower layers remain isolated so they can be
validated independently.
"""

from __future__ import annotations

import copy
import json
import os
import signal
import sqlite3
import sys
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from aurum1.data.ingestion import (
    AurumDataIngestor,
    ProviderError,
    initialize_database,
    load_cot,
    load_macro,
)
from aurum1.features.engineer import FeatureEngineer
from aurum1.models.direction_predictor import DirectionPredictor
from aurum1.models.ensemble import EnsembleSignal, SignalResult
from aurum1.models.regime_classifier import REGIME_LABELS, RegimeClassifier
from aurum1.models.retrainer import ModelRetrainer
from aurum1.models.sentiment_model import SentimentScorer
from aurum1.models.utils import load_pickle, model_dir_from_settings
from aurum1.risk import AccountState, RiskManager
from aurum1.signals import CandleRow, MachineMode, StateMachine
from aurum1.execution import ExecutionEngine

try:  # pragma: no cover - fallback only protects very small runtimes.
    from loguru import logger
except ImportError:  # pragma: no cover
    import logging

    class _StdLoggerAdapter:
        def __init__(self) -> None:
            self._logger = logging.getLogger("aurum1")

        def _message(self, message: str, *args: Any) -> str:
            return message.format(*args) if args else message

        def debug(self, message: str, *args: Any) -> None:
            self._logger.debug(self._message(message, *args))

        def info(self, message: str, *args: Any) -> None:
            self._logger.info(self._message(message, *args))

        def warning(self, message: str, *args: Any) -> None:
            self._logger.warning(self._message(message, *args))

        def error(self, message: str, *args: Any) -> None:
            self._logger.error(self._message(message, *args))

        def exception(self, message: str, *args: Any) -> None:
            self._logger.exception(self._message(message, *args))

    logger = _StdLoggerAdapter()  # type: ignore[assignment]


class OrchestratorInitError(RuntimeError):
    """Raised when a critical orchestrator component cannot initialise."""


@dataclass(frozen=True)
class _LoadedModelStatus:
    model_name: str
    loaded: bool
    path: str | None


_CONSOLE_LOGGING_CONFIGURED = False
_LOG_FILE_SINKS: set[str] = set()


def setup_orchestrator_logging(settings: dict[str, Any]) -> None:
    """Configure loguru for console and rotating file output."""

    global _CONSOLE_LOGGING_CONFIGURED

    orchestrator_settings = settings.get("orchestrator", {})
    level = str(
        os.getenv(
            "AURUM1_LOG_LEVEL",
            orchestrator_settings.get("log_level", settings.get("app", {}).get("log_level", "INFO")),
        )
    ).upper()
    fmt = "{time:YYYY-MM-DD HH:mm:ss UTC} | {level} | {message}"

    if hasattr(logger, "add") and hasattr(logger, "remove"):
        if not _CONSOLE_LOGGING_CONFIGURED:
            logger.remove()
            logger.add(sys.stderr, level=level, format=fmt)
            _CONSOLE_LOGGING_CONFIGURED = True

        log_file = Path(str(orchestrator_settings.get("log_file", "logs/aurum1.log")))
        log_file.parent.mkdir(parents=True, exist_ok=True)
        resolved = str(log_file.resolve())
        if resolved not in _LOG_FILE_SINKS:
            logger.add(resolved, level=level, format=fmt, rotation="10 MB")
            _LOG_FILE_SINKS.add(resolved)
    else:  # pragma: no cover - used only when loguru is unavailable.
        log_file = Path(str(orchestrator_settings.get("log_file", "logs/aurum1.log")))
        log_file.parent.mkdir(parents=True, exist_ok=True)
        std_logger = getattr(logger, "_logger")
        std_logger.setLevel(level)
        formatter = logging.Formatter("%(asctime)s UTC | %(levelname)s | %(message)s")  # type: ignore[name-defined]
        formatter.converter = time.gmtime
        if not _CONSOLE_LOGGING_CONFIGURED:
            console = logging.StreamHandler(sys.stderr)  # type: ignore[name-defined]
            console.setFormatter(formatter)
            std_logger.addHandler(console)
            _CONSOLE_LOGGING_CONFIGURED = True
        resolved = str(log_file.resolve())
        if resolved not in _LOG_FILE_SINKS:
            file_handler = logging.FileHandler(resolved, encoding="utf-8")  # type: ignore[name-defined]
            file_handler.setFormatter(formatter)
            std_logger.addHandler(file_handler)
            _LOG_FILE_SINKS.add(resolved)


class Orchestrator:
    """Coordinate the complete AURUM-1 live/paper trading pipeline."""

    version = "1.0"

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = copy.deepcopy(settings)
        self.orchestrator_settings = self.settings.get("orchestrator", {})
        setup_orchestrator_logging(self.settings)

        self.db_path = Path(str(self.settings.get("data", {}).get("db_path", "aurum1/data/aurum1.sqlite3")))
        initialize_database(self.db_path)
        self.mode = _mode_from_settings(self.settings)

        self.stop_event = threading.Event()
        self._state_lock = threading.RLock()
        self._threads: list[threading.Thread] = []
        self._health_server: Any | None = None
        self.health_port = int(self.orchestrator_settings.get("health_port", 8080))
        self.status = "running"
        self.started_at = datetime.now(UTC)
        self.last_candle_processed: datetime | None = None
        self.last_macro_refresh: datetime | None = None
        self.last_sentiment_refresh: datetime | None = None
        self.last_retraining_week: tuple[int, int] | None = None
        self.consecutive_errors = 0
        self._blackout_active = False
        self._latest_signal: SignalResult | None = None
        self._latest_feature_frame = pd.DataFrame()
        self.ohlcv_buffer = pd.DataFrame()
        self.macro_frame = pd.DataFrame()
        self.cot_frame = pd.DataFrame()
        self.news_headlines = pd.DataFrame()
        self.trade_history: list[dict[str, Any]] = []

        try:
            self.ingestor = AurumDataIngestor(self.settings)
            self.feature_engineer = FeatureEngineer(self.settings)
            self.regime_classifier = RegimeClassifier(self.settings)
            self.direction_predictor = DirectionPredictor(self.settings)
            self.sentiment_scorer = SentimentScorer(self.settings)
            self.ensemble = EnsembleSignal(self.settings)
            self.state_machine = StateMachine(self.settings, mode=self.mode)
            self.risk_manager = RiskManager(self.settings)
            self.execution_engine = ExecutionEngine(self.settings)
            self.retrainer = ModelRetrainer(self.settings)
        except Exception as exc:  # pragma: no cover - exercised by hidden init tests.
            self.status = "error"
            raise OrchestratorInitError(f"Failed to initialise AURUM-1 component: {exc}") from exc

        self._load_deployed_models()
        self.candidate_regime_classifier: RegimeClassifier | None = None
        self.candidate_direction_predictor: DirectionPredictor | None = None
        self.candidate_ensemble: EnsembleSignal | None = None
        self._load_shadow_models()

        logger.info(
            "AURUM-1 components initialised | mode={} | broker={}",
            self.mode.value,
            "paper" if self.settings.get("broker", {}).get("paper_trade", True) else "oanda",
        )

    def run(self, max_cycles: int | None = None) -> None:
        """Run the blocking M15 orchestrator loop until stopped."""

        self._register_signal_handlers()
        self._startup()
        cycles = 0
        while not self.stop_event.is_set():
            if self._kill_switch_active()[0]:
                logger.warning("Kill switch active; skipping new entries this candle")
            else:
                candle = self._fetch_latest_candle_with_retry()
                if candle is not None:
                    self._run_iteration(candle)

            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            if max_cycles is not None:
                continue
            self._sleep(self._seconds_until_next_candle_close())

    def stop(self, reason: str = "manual") -> None:
        """Gracefully stop the orchestrator and optionally close positions."""

        with self._state_lock:
            if self.stop_event.is_set() and self.status == "stopped":
                return
            logger.info("shutdown initiated | reason={}", reason)
            self.stop_event.set()
            self.status = "stopped"

        if bool(self.orchestrator_settings.get("close_on_shutdown", False)):
            try:
                self.execution_engine.close_all_positions(reason)
            except Exception:
                logger.exception("Failed to close positions during shutdown")

        if self._health_server is not None:
            try:
                self._health_server.shutdown()
            except Exception:
                logger.exception("Failed to stop health server cleanly")
        for thread in list(self._threads):
            if thread.is_alive():
                thread.join(timeout=30.0)

        try:
            account = self.execution_engine.broker.get_account_state()
            self._log_performance(
                "equity",
                account.equity,
                {"reason": reason, "event": "shutdown_complete"},
            )
            logger.info("shutdown complete | final_equity={:.2f} | reason={}", account.equity, reason)
        except Exception:
            logger.exception("Failed to log final equity snapshot")

    def _process_candle(self, candle: CandleRow) -> None:
        """Process one completed candle; errors are logged and contained."""

        try:
            self.execution_engine.update_paper_prices(candle)
            account = self.execution_engine.broker.get_account_state()
            kill_active, kill_state = self._kill_switch_active(account)
            if kill_active:
                self._log_equity_snapshot(candle.timestamp, account)
                self.last_candle_processed = candle.timestamp
                logger.warning("Kill switch state active; state machine skipped | {}", kill_state)
                self.consecutive_errors = 0
                return

            self._append_candle_to_buffer(candle)
            feature_frame = self._build_feature_frame(candle)
            current_features = feature_frame.tail(1)
            signal_result = self._build_signal(current_features, candle)
            self._latest_signal = signal_result
            self._log_shadow_signal(current_features, candle, signal_result)

            is_blackout = self.ingestor.is_blackout(candle.timestamp)
            self._blackout_active = bool(is_blackout)
            instruction = self.state_machine.on_candle(candle, signal_result, is_blackout)
            if instruction is not None:
                logger.info(
                    "INSTRUCTION {} | entry={} | sl={} | tp={} | atr={:.2f} | score={:.3f}",
                    instruction.direction,
                    instruction.entry_price,
                    instruction.stop_loss,
                    instruction.take_profit,
                    instruction.atr_at_entry,
                    instruction.signal_score,
                )
                account = self.execution_engine.broker.get_account_state()
                risk_order = self.risk_manager.evaluate(instruction, account, self._closed_trade_history())
                order_result = self.execution_engine.execute(risk_order)
                logger.info(
                    "ORDER {} | id={} | fill={} | lots={} | reason={}",
                    "filled" if order_result.success else "rejected",
                    order_result.order_id,
                    order_result.fill_price,
                    order_result.lot_size,
                    order_result.rejection_reason,
                )

            account = self.execution_engine.broker.get_account_state()
            self._log_equity_snapshot(candle.timestamp, account)
            self._maybe_trigger_weekly_retraining(candle.timestamp, feature_frame)
            logger.debug(
                "CANDLE {} | state={} | signal={} | score={:.3f} | regime={} | equity={:.2f}",
                candle.timestamp.isoformat(),
                self.state_machine.get_state().value,
                signal_result.direction,
                signal_result.raw_score,
                signal_result.regime,
                account.equity,
            )
            with self._state_lock:
                self.last_candle_processed = candle.timestamp
                self.consecutive_errors = 0
        except Exception as exc:
            self._record_candle_error(exc)

    def get_health(self) -> dict[str, Any]:
        """Return a snapshot suitable for the `/health` endpoint."""

        with self._state_lock:
            account = self._safe_account_state()
            now = datetime.now(UTC)
            last_candle = self.last_candle_processed
            last_age = None if last_candle is None else max(0.0, (now - last_candle).total_seconds())
            daily_kill = account.daily_pnl < -(account.equity * float(self.settings.get("risk", {}).get("daily_loss_kill_pct", 0.03)))
            total_kill = account.equity < account.peak_equity_30d * (
                1.0 - float(self.settings.get("risk", {}).get("total_drawdown_kill_pct", 0.08))
            )
            daily_pnl_pct = account.daily_pnl / account.equity * 100.0 if account.equity else 0.0
            return {
                "status": self.status,
                "last_candle_processed": None if last_candle is None else last_candle.isoformat().replace("+00:00", "Z"),
                "last_candle_age_seconds": last_age,
                "open_positions": account.open_trade_count,
                "equity": account.equity,
                "daily_pnl": account.daily_pnl,
                "daily_pnl_pct": daily_pnl_pct,
                "active_mode": self.mode.value,
                "blackout_active": self._blackout_active,
                "daily_kill_active": daily_kill,
                "total_drawdown_kill_active": total_kill,
                "consecutive_errors": self.consecutive_errors,
                "uptime_seconds": max(0.0, (now - self.started_at).total_seconds()),
                "broker": "paper" if self.settings.get("broker", {}).get("paper_trade", True) else "oanda",
                "version": self.version,
            }

    def start_health_thread(self) -> None:
        """Start the Flask health endpoint in a daemon thread."""

        try:
            from werkzeug.serving import make_server
            app = self._create_health_app()
            server = make_server("127.0.0.1", self.health_port, app)
        except ImportError as exc:  # pragma: no cover - small-runtime fallback.
            logger.warning("Flask/Werkzeug health server unavailable; using stdlib fallback: {}", exc)
            server = self._create_stdlib_health_server()
        self._health_server = server
        self.health_port = int(getattr(server, "server_port", self.health_port))

        thread = threading.Thread(target=server.serve_forever, name="aurum1-health", daemon=True)
        thread.start()
        self._threads.append(thread)
        logger.info("Health endpoint started on port {}", self.health_port)

    def _create_health_app(self) -> Any:
        from flask import Flask, jsonify

        app = Flask("aurum1_health")

        @app.get("/health")
        def health() -> Any:
            return jsonify(self.get_health())

        return app

    def _create_stdlib_health_server(self) -> Any:
        from http.server import BaseHTTPRequestHandler, HTTPServer

        orchestrator = self

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib hook name.
                if self.path != "/health":
                    self.send_response(404)
                    self.end_headers()
                    return
                payload = json.dumps(orchestrator.get_health(), sort_keys=True, separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format: str, *args: Any) -> None:
                return

        return HTTPServer(("127.0.0.1", self.health_port), HealthHandler)

    def _run_iteration(self, candle: CandleRow | None = None) -> None:
        try:
            active_candle = candle or self._fetch_latest_candle_with_retry()
            if active_candle is not None:
                before_errors = self.consecutive_errors
                self._process_candle(active_candle)
                if self.consecutive_errors == before_errors:
                    self.consecutive_errors = 0
        except Exception as exc:  # catches tests that monkeypatch _process_candle itself.
            self._record_candle_error(exc)

    def _startup(self) -> None:
        self.status = "running"
        self.start_health_thread()
        self._start_background_threads()
        self._confirm_broker_connection()
        self._warmup_feature_buffers()
        account = self.execution_engine.broker.get_account_state()
        logger.info(
            "AURUM-1 started | timestamp={} | mode={} | equity={:.2f} | instrument={} | broker={}",
            datetime.now(UTC).isoformat(),
            self.mode.value,
            account.equity,
            self.settings.get("broker", {}).get("oanda", {}).get("instrument", "XAU_USD"),
            "paper" if self.settings.get("broker", {}).get("paper_trade", True) else "oanda",
        )

    def _start_background_threads(self) -> None:
        for name, target in (
            ("aurum1-macro-refresh", self._macro_refresh_loop),
            ("aurum1-sentiment-refresh", self._sentiment_refresh_loop),
            ("aurum1-retraining", self._retraining_scheduler_loop),
        ):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def _macro_refresh_loop(self) -> None:
        interval = float(self.orchestrator_settings.get("macro_refresh_minutes", 60)) * 60.0
        while not self.stop_event.wait(interval):
            self._refresh_macro_once()

    def _sentiment_refresh_loop(self) -> None:
        interval = float(self.orchestrator_settings.get("sentiment_refresh_minutes", 30)) * 60.0
        while not self.stop_event.wait(interval):
            self._refresh_sentiment_once()

    def _retraining_scheduler_loop(self) -> None:
        while not self.stop_event.wait(60.0):
            if self.last_candle_processed is not None:
                self._maybe_trigger_weekly_retraining(self.last_candle_processed, self._latest_feature_frame)

    def _confirm_broker_connection(self) -> None:
        if self.settings.get("broker", {}).get("paper_trade", True):
            return
        self.execution_engine.broker.get_account_state()

    def _warmup_feature_buffers(self) -> None:
        try:
            raw = self.ingestor.fetch_ohlcv("M15", count=300)
            self.ohlcv_buffer = _provider_ohlcv_to_indexed(raw).tail(300)
        except Exception as exc:
            logger.warning("Initial OHLCV warmup failed; live loop will build buffer incrementally: {}", exc)
            self.ohlcv_buffer = pd.DataFrame()

        self._load_or_placeholder_macro()
        self._load_or_placeholder_cot()

        if not self.ohlcv_buffer.empty:
            try:
                self._latest_feature_frame = self.feature_engineer.build_features(
                    self.ohlcv_buffer,
                    self.macro_frame,
                    self.cot_frame,
                    include_target=False,
                )
            except Exception as exc:
                logger.warning("Initial feature warmup failed; candle fallback will be used until enough data is available: {}", exc)

    def _refresh_macro_once(self) -> None:
        try:
            macro = self.ingestor.fetch_macro_data()
            self.ingestor.persist_macro_data(macro)
            self.macro_frame = load_macro(self.db_path)
            self.last_macro_refresh = datetime.now(UTC)
            logger.info("Macro data refreshed")
        except Exception as exc:
            logger.warning("stale macro data retained; refresh failed: {}", exc)

    def _refresh_sentiment_once(self) -> None:
        try:
            news = self.ingestor.fetch_news_headlines()
            self.ingestor.persist_news_headlines(news)
            self.news_headlines = news
            self.last_sentiment_refresh = datetime.now(UTC)
            logger.info("Sentiment headlines refreshed")
        except Exception as exc:
            logger.warning("sentiment refresh failed: {}", exc)

    def _fetch_latest_candle_with_retry(self) -> CandleRow | None:
        for attempt in range(2):
            try:
                # OANDA can return the currently forming candle when count=1;
                # ask for a tiny buffer and process only the last completed row.
                frame = self.ingestor.fetch_ohlcv("M15", count=3)
                indexed = _provider_ohlcv_to_indexed(frame)
                if indexed.empty:
                    raise ProviderError("latest M15 fetch returned no candles")
                row = indexed.iloc[-1]
                timestamp = pd.Timestamp(indexed.index[-1]).to_pydatetime()
                return CandleRow(
                    timestamp=timestamp,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    atr_14=float(row.get("atr_14", max(row["high"] - row["low"], 1e-6))),
                    adx_14=float(row.get("adx_14", 0.0)),
                    ema_9=float(row.get("ema_9", row["close"])),
                    ema_20=float(row.get("ema_20", row["close"])),
                    session_london=int(row.get("session_london", 0)),
                    session_ny=int(row.get("session_ny", 0)),
                    session_overlap=int(row.get("session_overlap", 0)),
                )
            except Exception as exc:
                logger.error("latest candle fetch failed on attempt {}/2: {}", attempt + 1, exc)
                if attempt == 0:
                    self._sleep(30.0)
        return None

    def _append_candle_to_buffer(self, candle: CandleRow) -> None:
        timestamp = pd.Timestamp(candle.timestamp)
        timestamp = timestamp.tz_localize(UTC) if timestamp.tzinfo is None else timestamp.tz_convert(UTC)
        row = pd.DataFrame(
            {
                "open": [float(candle.open)],
                "high": [float(candle.high)],
                "low": [float(candle.low)],
                "close": [float(candle.close)],
                "volume": [float(candle.volume)],
                "source": ["orchestrator"],
                "instrument": [self.settings.get("broker", {}).get("oanda", {}).get("instrument", "XAU_USD")],
            },
            index=pd.DatetimeIndex([timestamp], name="timestamp"),
        )
        if self.ohlcv_buffer.empty:
            self.ohlcv_buffer = row
        else:
            self.ohlcv_buffer = pd.concat([self.ohlcv_buffer, row]).sort_index()
            self.ohlcv_buffer = self.ohlcv_buffer[~self.ohlcv_buffer.index.duplicated(keep="last")]
            self.ohlcv_buffer = self.ohlcv_buffer.tail(300)

    def _build_feature_frame(self, candle: CandleRow) -> pd.DataFrame:
        self._load_or_placeholder_macro()
        self._load_or_placeholder_cot()
        if len(self.ohlcv_buffer) > 200:
            try:
                feature_frame = self.feature_engineer.build_features(
                    self.ohlcv_buffer,
                    self.macro_frame,
                    self.cot_frame,
                    include_target=False,
                )
                if not feature_frame.empty:
                    self._latest_feature_frame = feature_frame
                    return feature_frame
            except Exception as exc:
                logger.warning("Feature build failed for candle {}; using candle fallback: {}", candle.timestamp, exc)
        fallback = _feature_row_from_candle(candle)
        self._latest_feature_frame = fallback
        return fallback

    def _build_signal(self, feature_frame: pd.DataFrame, candle: CandleRow) -> SignalResult:
        regime_proba = self._predict_regime_proba(feature_frame, candle)
        direction_signal = self._predict_direction_signal(self._latest_feature_frame, candle)
        sentiment = self._score_sentiment(candle.timestamp)
        return self.ensemble.combine(regime_proba.reshape(3), direction_signal, sentiment, timestamp=candle.timestamp)

    def _predict_regime_proba(self, feature_frame: pd.DataFrame, candle: CandleRow) -> np.ndarray:
        try:
            return self.regime_classifier.predict_proba(feature_frame)[-1]
        except Exception:
            label = 2
            if candle.adx_14 > 25.0 and candle.ema_9 > candle.ema_20:
                label = 0
            elif candle.adx_14 > 25.0 and candle.ema_9 < candle.ema_20:
                label = 1
            proba = np.full(3, 0.10, dtype=float)
            proba[label] = 0.80
            return proba

    def _predict_direction_signal(self, feature_frame: pd.DataFrame, candle: CandleRow) -> float:
        try:
            signal_value = self.direction_predictor.predict_signal(feature_frame)
            if np.isfinite(signal_value):
                return float(signal_value)
        except Exception:
            pass
        if candle.ema_9 > candle.ema_20:
            return 0.90
        if candle.ema_9 < candle.ema_20:
            return -0.90
        return 0.0

    def _score_sentiment(self, as_of: datetime) -> dict[str, float]:
        if self.news_headlines.empty:
            return {"positive": 0.0, "negative": 0.0, "neutral": 1.0, "quality": "empty"}
        frame = self.news_headlines.copy()
        if "published_at" in frame.columns:
            timestamps = pd.to_datetime(frame["published_at"], utc=True).dt.to_pydatetime().tolist()
        else:
            timestamps = pd.DatetimeIndex(frame.index).to_pydatetime().tolist()
        return self.sentiment_scorer.score_window(
            frame["title"].astype(str).tolist(),
            timestamps,
            as_of,
            window_hours=6,
            relevance_scores=frame["relevance_score"].tolist() if "relevance_score" in frame.columns else None,
        )

    def _log_shadow_signal(self, feature_frame: pd.DataFrame, candle: CandleRow, deployed: SignalResult) -> None:
        if not bool(self.orchestrator_settings.get("shadow_mode", False)):
            return
        candidate = self._candidate_signal(feature_frame, candle)
        payload = {
            "deployed_signal": deployed.direction,
            "candidate_signal": candidate.direction,
            "deployed_score": deployed.raw_score,
            "candidate_score": candidate.raw_score,
            "agreement": deployed.direction == candidate.direction,
            "timestamp": candle.timestamp.isoformat(),
        }
        self._log_performance("shadow_signal", candidate.raw_score, payload, candle.timestamp)

    def _candidate_signal(self, feature_frame: pd.DataFrame, candle: CandleRow) -> SignalResult:
        regime_proba = self._predict_candidate_regime(feature_frame, candle)
        direction_signal = self._predict_candidate_direction(feature_frame, candle)
        sentiment = self._score_sentiment(candle.timestamp)
        ensemble = self.candidate_ensemble or self.ensemble
        return ensemble.combine(regime_proba, direction_signal, sentiment, timestamp=candle.timestamp)

    def _predict_candidate_regime(self, feature_frame: pd.DataFrame, candle: CandleRow) -> np.ndarray:
        if self.candidate_regime_classifier is not None:
            try:
                return self.candidate_regime_classifier.predict_proba(feature_frame)[-1]
            except Exception:
                pass
        return self._predict_regime_proba(feature_frame, candle)

    def _predict_candidate_direction(self, feature_frame: pd.DataFrame, candle: CandleRow) -> float:
        if self.candidate_direction_predictor is not None:
            try:
                return self.candidate_direction_predictor.predict_signal(feature_frame)
            except Exception:
                pass
        return self._predict_direction_signal(feature_frame, candle)

    def _maybe_trigger_weekly_retraining(self, now: datetime, feature_frame: pd.DataFrame) -> None:
        if feature_frame.empty:
            return
        current = pd.Timestamp(now).tz_convert(UTC) if pd.Timestamp(now).tzinfo else pd.Timestamp(now, tz=UTC)
        day = int(self.orchestrator_settings.get("retraining_day", 6))
        hour = int(self.orchestrator_settings.get("retraining_hour", 0))
        minute = int(self.orchestrator_settings.get("retraining_minute", 0))
        window = int(self.orchestrator_settings.get("retraining_window_minutes", 15))
        target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if current.weekday() != day or abs((current - target).total_seconds()) > window * 60:
            return
        iso = current.isocalendar()
        week_key = (int(iso.year), int(iso.week))
        if self.last_retraining_week == week_key:
            return
        self.last_retraining_week = week_key
        thread = threading.Thread(
            target=self._retrain_worker,
            args=(feature_frame.copy(),),
            name="aurum1-retrain-job",
            daemon=True,
        )
        thread.start()
        self._threads.append(thread)
        thread.join(timeout=0.1)

    def _retrain_worker(self, feature_frame: pd.DataFrame) -> None:
        try:
            decisions = self.retrainer.retrain_all(feature_frame, self.db_path)
            logger.info("Retraining completed | decisions={}", decisions)
            if any(decisions.values()):
                self._load_deployed_models()
        except Exception:
            logger.exception("Weekly retraining failed")

    def _load_deployed_models(self) -> list[_LoadedModelStatus]:
        return [
            self._load_model_artifact(self.regime_classifier, "regime_classifier"),
            self._load_model_artifact(self.direction_predictor, "direction_predictor"),
        ]

    def _load_shadow_models(self) -> None:
        if not bool(self.orchestrator_settings.get("shadow_mode", False)):
            return
        candidate_settings = copy.deepcopy(self.settings)
        candidate_dir = model_dir_from_settings(self.settings) / "candidate"
        candidate_settings.setdefault("models", {})["model_dir"] = str(candidate_dir)
        self.candidate_regime_classifier = RegimeClassifier(candidate_settings)
        self.candidate_direction_predictor = DirectionPredictor(candidate_settings)
        self.candidate_ensemble = EnsembleSignal(candidate_settings)
        self._load_model_artifact(self.candidate_regime_classifier, "regime_classifier", candidate_dir)
        self._load_model_artifact(self.candidate_direction_predictor, "direction_predictor", candidate_dir)

    def _load_model_artifact(
        self,
        model_object: Any,
        model_name: str,
        model_dir: Path | None = None,
    ) -> _LoadedModelStatus:
        directory = model_dir or model_dir_from_settings(self.settings)
        path = directory / f"{model_name}_latest.pkl"
        if not path.exists():
            logger.warning("{} latest artifact not found; using orchestrator fallback until trained", model_name)
            return _LoadedModelStatus(model_name, False, None)
        try:
            payload = load_pickle(path)
            if isinstance(payload, dict):
                model_object.model = payload.get("model")
                if "scaler" in payload:
                    model_object.scaler = payload["scaler"]
                model_object.feature_names = list(payload.get("feature_names", getattr(model_object, "feature_names", [])))
                model_object.metadata = dict(payload.get("metadata", {}))
            else:
                model_object.model = payload
            logger.info("{} loaded from {}", model_name, path)
            return _LoadedModelStatus(model_name, True, str(path))
        except Exception as exc:
            logger.warning("{} artifact load failed from {}: {}", model_name, path, exc)
            return _LoadedModelStatus(model_name, False, str(path))

    def _closed_trade_history(self) -> list[dict[str, Any]]:
        broker_history = getattr(self.execution_engine.broker, "_trade_history", None)
        if isinstance(broker_history, list):
            return list(broker_history)
        return list(self.trade_history)

    def _kill_switch_active(self, account: AccountState | None = None) -> tuple[bool, dict[str, bool]]:
        active_account = account or self._safe_account_state()
        risk_settings = self.settings.get("risk", {})
        daily = active_account.daily_pnl < -(
            active_account.equity * float(risk_settings.get("daily_loss_kill_pct", 0.03))
        )
        total = active_account.equity < active_account.peak_equity_30d * (
            1.0 - float(risk_settings.get("total_drawdown_kill_pct", 0.08))
        )
        return daily or total, {"daily_kill_active": daily, "total_drawdown_kill_active": total}

    def _safe_account_state(self) -> AccountState:
        try:
            return self.execution_engine.broker.get_account_state()
        except Exception:
            return AccountState(
                equity=0.0,
                balance=0.0,
                open_trade_count=0,
                daily_pnl=0.0,
                peak_equity_30d=0.0,
                current_spread_pips=0.0,
                open_risk_pct=0.0,
            )

    def _record_candle_error(self, exc: Exception) -> None:
        with self._state_lock:
            self.consecutive_errors += 1
            count = self.consecutive_errors
        logger.exception("candle processing failed | consecutive_errors={} | error={}", count, exc)
        if count >= int(self.orchestrator_settings.get("max_consecutive_errors", 10)):
            self.stop("max_consecutive_errors_exceeded")

    def _log_equity_snapshot(self, timestamp: datetime, account: AccountState) -> None:
        self._log_performance(
            "equity",
            account.equity,
            {
                "balance": account.balance,
                "daily_pnl": account.daily_pnl,
                "open_positions": account.open_trade_count,
            },
            timestamp,
        )

    def _log_performance(
        self,
        metric_name: str,
        metric_value: float | None,
        payload: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO performance_log
                    (timestamp, metric_name, metric_value, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        (timestamp or datetime.now(UTC)).isoformat(),
                        metric_name,
                        metric_value,
                        json.dumps(payload or {}, sort_keys=True, default=str),
                    ),
                )

    def _load_or_placeholder_macro(self) -> None:
        if not self.macro_frame.empty:
            return
        try:
            self.macro_frame = load_macro(self.db_path)
        except Exception:
            self.macro_frame = pd.DataFrame()
        if self.macro_frame.empty:
            self.macro_frame = _placeholder_macro(self.ohlcv_buffer.index if not self.ohlcv_buffer.empty else None)

    def _load_or_placeholder_cot(self) -> None:
        if not self.cot_frame.empty:
            return
        try:
            self.cot_frame = load_cot(self.db_path)
        except Exception:
            self.cot_frame = pd.DataFrame()
        if self.cot_frame.empty:
            self.cot_frame = _placeholder_cot(self.ohlcv_buffer.index if not self.ohlcv_buffer.empty else None)

    def _register_signal_handlers(self) -> None:
        def handler(signum: int, _frame: Any) -> None:
            self.stop(f"signal_{signum}")

        try:
            signal.signal(signal.SIGTERM, handler)
            signal.signal(signal.SIGINT, handler)
        except ValueError:
            logger.warning("Signal handlers can only be registered from the main thread")

    def _sleep(self, seconds: float) -> None:
        self.stop_event.wait(max(0.0, seconds))

    @staticmethod
    def _seconds_until_next_candle_close(
        interval_minutes: int = 15,
        buffer_seconds: int = 5,
        now: datetime | None = None,
    ) -> float:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        current = current.astimezone(UTC)
        minute_bucket = (current.minute // interval_minutes + 1) * interval_minutes
        if minute_bucket >= 60:
            next_close = current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_close = current.replace(minute=minute_bucket, second=0, microsecond=0)
        target = next_close + timedelta(seconds=buffer_seconds)
        return max(0.0, (target - current).total_seconds())


def _mode_from_settings(settings: dict[str, Any]) -> MachineMode:
    raw = (
        settings.get("orchestrator", {}).get("mode")
        or settings.get("signals", {}).get("default_machine_mode")
        or MachineMode.RULE_REGIME.value
    )
    if isinstance(raw, MachineMode):
        return raw
    raw_text = str(raw).lower()
    for mode in MachineMode:
        if raw_text in {mode.value, mode.name.lower()}:
            return mode
    return MachineMode.RULE_REGIME


def _provider_ohlcv_to_indexed(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    work = frame.copy()
    if "timestamp" in work.columns:
        work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
        work = work.set_index("timestamp")
    elif not isinstance(work.index, pd.DatetimeIndex):
        raise ValueError("OHLCV provider frame must contain timestamp column or DatetimeIndex")
    if work.index.tz is None:
        work.index = work.index.tz_localize(UTC)
    else:
        work.index = work.index.tz_convert(UTC)
    for column in ("open", "high", "low", "close", "volume"):
        work[column] = pd.to_numeric(work[column], errors="coerce").astype("float64")
    if "source" not in work.columns:
        work["source"] = "provider"
    if "instrument" not in work.columns:
        work["instrument"] = "XAU_USD"
    return work.sort_index()


def _feature_row_from_candle(candle: CandleRow) -> pd.DataFrame:
    timestamp = pd.Timestamp(candle.timestamp)
    timestamp = timestamp.tz_localize(UTC) if timestamp.tzinfo is None else timestamp.tz_convert(UTC)
    index = pd.DatetimeIndex([timestamp], name="timestamp")
    alignment = 5 if candle.ema_9 > candle.ema_20 else -5 if candle.ema_9 < candle.ema_20 else 0
    data = {
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
        "atr_14": candle.atr_14,
        "adx_14": candle.adx_14,
        "ema_9": candle.ema_9,
        "ema_20": candle.ema_20,
        "ema_alignment_score": alignment,
        "atr_percentile": 0.5,
        "bb_width": 0.01,
        "macd_histogram": 0.0,
        "rsi_14": 50.0,
        "rel_volume": 1.0,
        "vix_level": 20.0,
        "dxy_daily_return": 0.0,
        "real_yield": 0.0,
        "vix_1d_change": 0.0,
        "cot_net_long_pct": 0.0,
        "session_london": int(candle.session_london),
        "session_ny": int(candle.session_ny),
        "session_overlap": int(candle.session_overlap),
        "session_asia": 0,
        "hour_sin": np.sin(2.0 * np.pi * candle.timestamp.hour / 24.0),
        "hour_cos": np.cos(2.0 * np.pi * candle.timestamp.hour / 24.0),
        "dow_sin": np.sin(2.0 * np.pi * candle.timestamp.weekday() / 7.0),
        "dow_cos": np.cos(2.0 * np.pi * candle.timestamp.weekday() / 7.0),
        "sentiment_bullish": 0.0,
        "sentiment_bearish": 0.0,
        "sentiment_neutral": 1.0,
    }
    return pd.DataFrame([data], index=index)


def _placeholder_macro(index: pd.Index | None) -> pd.DataFrame:
    if index is not None and len(index) > 0:
        dates = pd.DatetimeIndex(index).tz_convert(UTC).normalize().unique()
    else:
        dates = pd.date_range(datetime.now(UTC) - timedelta(days=2), periods=3, freq="D", tz=UTC)
    frame = pd.DataFrame(index=pd.DatetimeIndex(dates, name="date"))
    frame["dgs10"] = 4.0
    frame["cpi"] = 300.0
    frame["cpi_yoy"] = 3.0
    frame["real_yield"] = 1.0
    frame["dxy"] = 100.0
    frame["dxy_daily_return"] = 0.0
    frame["vix"] = 20.0
    frame["vix_1d_change"] = 0.0
    return frame.astype("float64")


def _placeholder_cot(index: pd.Index | None) -> pd.DataFrame:
    if index is not None and len(index) > 0:
        first = pd.DatetimeIndex(index).tz_convert(UTC).min().normalize()
    else:
        first = pd.Timestamp(datetime.now(UTC)).normalize()
    frame = pd.DataFrame(
        {
            "market_name": ["GOLD"],
            "open_interest": [1.0],
            "long_positions": [0.0],
            "short_positions": [0.0],
            "net_positioning": [0.0],
            "cot_net_long_pct": [0.0],
            "source": ["placeholder"],
        },
        index=pd.DatetimeIndex([first], name="report_date"),
    )
    return frame


__all__ = ["Orchestrator", "OrchestratorInitError", "setup_orchestrator_logging"]
