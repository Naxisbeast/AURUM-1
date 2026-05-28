from __future__ import annotations

import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from urllib.request import urlopen

import pytest

from aurum1.orchestrator import Orchestrator, OrchestratorInitError
from aurum1.risk import AccountState
from aurum1.signals import CandleRow, MachineMode


def make_settings(db_path: Path, overrides: dict | None = None) -> dict:
    settings = {
        "app": {"random_seed": 7, "log_level": "INFO"},
        "data": {"db_path": str(db_path), "instrument": "XAUUSD", "yfinance_symbol": "XAUUSD=X"},
        "broker": {
            "paper_trade": True,
            "paper_initial_equity": 10000.0,
            "oanda": {
                "instrument": "XAU_USD",
                "api_key_env": "OANDA_API_KEY",
                "account_id_env": "OANDA_ACCOUNT_ID",
                "environment_env": "OANDA_ENV",
                "default_environment": "practice",
            },
        },
        "risk": {
            "risk_per_trade_pct": 0.01,
            "kelly_min_trades": 20,
            "kelly_default_fraction": 0.25,
            "max_spread_pips": 3.0,
            "daily_loss_kill_pct": 0.03,
            "total_drawdown_kill_pct": 0.08,
            "max_portfolio_risk_pct": 3.0,
            "pip_value_per_lot": 1.0,
            "pip_size": 0.01,
            "min_lot_size": 0.01,
            "max_lot_size": 10.0,
            "lot_step": 0.01,
        },
        "signals": {
            "adx_threshold": 25,
            "min_pullback_candles": 1,
            "max_pullback_candles": 4,
            "armed_timeout_candles": 20,
            "window_expiry_candles": 6,
            "atr_sl_multiplier": 2.0,
            "atr_tp_multiplier": 3.0,
            "atr_breakout_buffer": 0.3,
            "require_session_filter": True,
        },
        "execution": {"paper_spread_pips": 1.5, "slippage_std_pips": 0.0},
        "models": {"model_dir": str(db_path.parent / "models")},
        "orchestrator": {
            "mode": "rule_regime",
            "max_consecutive_errors": 10,
            "close_on_shutdown": False,
            "health_port": 0,
            "log_level": "INFO",
            "log_file": "logs/test_phase9.log",
            "shadow_mode": False,
            "retraining_day": 6,
            "retraining_hour": 0,
            "retraining_minute": 0,
            "retraining_window_minutes": 15,
        },
    }
    if overrides:
        for section, values in overrides.items():
            settings.setdefault(section, {}).update(values)
    return settings


def make_orchestrator(overrides: dict | None = None) -> Orchestrator:
    tempdir = tempfile.TemporaryDirectory()
    settings = make_settings(Path(tempdir.name) / "aurum.sqlite3", overrides)
    orchestrator = Orchestrator(settings)
    orchestrator._tempdir = tempdir  # type: ignore[attr-defined]
    return orchestrator


def make_candle(timestamp: datetime | None = None) -> CandleRow:
    return CandleRow(
        timestamp=timestamp or datetime(2026, 1, 1, 12, 15, tzinfo=UTC),
        open=2328.0,
        high=2332.0,
        low=2326.0,
        close=2330.0,
        volume=1000.0,
        atr_14=5.0,
        adx_14=30.0,
        ema_9=2329.0,
        ema_20=2325.0,
        session_london=1,
        session_ny=0,
        session_overlap=0,
    )


def test_orchestrator_initialises_all_components() -> None:
    orchestrator = make_orchestrator()

    components = [
        orchestrator.ingestor,
        orchestrator.feature_engineer,
        orchestrator.regime_classifier,
        orchestrator.direction_predictor,
        orchestrator.sentiment_scorer,
        orchestrator.ensemble,
        orchestrator.state_machine,
        orchestrator.risk_manager,
        orchestrator.execution_engine,
        orchestrator.retrainer,
    ]
    assert all(component is not None for component in components)
    assert {"status", "equity", "active_mode", "broker"}.issubset(orchestrator.get_health())


def test_missing_model_artifacts_block_full_ensemble() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        settings = make_settings(
            Path(tempdir) / "aurum.sqlite3",
            {"orchestrator": {"mode": "full_ensemble"}, "models": {"model_dir": str(Path(tempdir) / "models")}},
        )

        with pytest.raises(OrchestratorInitError, match="FULL_ENSEMBLE requires real deployed model artifacts"):
            Orchestrator(settings)


def test_orchestrator_processes_single_candle() -> None:
    orchestrator = make_orchestrator()

    orchestrator._process_candle(make_candle())

    with closing(sqlite3.connect(orchestrator.db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM performance_log WHERE metric_name='equity'").fetchone()[0]
    assert count == 1
    assert orchestrator.consecutive_errors == 0


def test_orchestrator_continues_on_candle_error() -> None:
    orchestrator = make_orchestrator()
    calls = {"count": 0}

    def flaky_process(_candle: CandleRow) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("boom")

    orchestrator._process_candle = flaky_process  # type: ignore[method-assign]

    orchestrator._run_iteration(make_candle())
    assert orchestrator.consecutive_errors == 1
    orchestrator._run_iteration(make_candle())
    assert orchestrator.consecutive_errors == 0
    assert not orchestrator.stop_event.is_set()


def test_orchestrator_stops_on_max_consecutive_errors() -> None:
    orchestrator = make_orchestrator({"orchestrator": {"max_consecutive_errors": 3}})
    orchestrator._process_candle = MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    for _ in range(3):
        orchestrator._run_iteration(make_candle())

    assert orchestrator.stop_event.is_set()
    assert orchestrator.status == "stopped"


def test_kill_switch_skips_entry_not_loop() -> None:
    orchestrator = make_orchestrator()
    account = AccountState(
        equity=10000.0,
        balance=10000.0,
        open_trade_count=0,
        daily_pnl=-400.0,
        peak_equity_30d=10000.0,
        current_spread_pips=1.0,
        open_risk_pct=0.0,
    )
    orchestrator.execution_engine.broker.get_account_state = MagicMock(return_value=account)  # type: ignore[method-assign]
    orchestrator.state_machine.on_candle = MagicMock()  # type: ignore[method-assign]

    orchestrator._process_candle(make_candle())

    orchestrator.state_machine.on_candle.assert_not_called()
    assert not orchestrator.stop_event.is_set()


def test_graceful_shutdown_logs_final_equity() -> None:
    orchestrator = make_orchestrator()

    orchestrator.stop("test")

    with closing(sqlite3.connect(orchestrator.db_path)) as conn:
        row = conn.execute(
            """
            SELECT payload_json FROM performance_log
            WHERE metric_name='equity'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    assert "shutdown_complete" in row[0]


def test_health_endpoint_returns_valid_json() -> None:
    orchestrator = make_orchestrator()
    orchestrator.start_health_thread()
    time.sleep(0.1)

    with urlopen(f"http://127.0.0.1:{orchestrator.health_port}/health", timeout=5) as response:
        payload = response.read().decode("utf-8")

    assert response.status == 200
    assert '"status":"running"' in payload
    assert '"active_mode":"rule_regime"' in payload
    orchestrator.stop("test")


def test_candle_timing_calculation() -> None:
    now = datetime(2026, 1, 1, 12, 12, 0, tzinfo=UTC)

    seconds = Orchestrator._seconds_until_next_candle_close(now=now)

    assert 0 <= seconds <= (15 * 60 + 5)
    assert seconds == pytest.approx(185.0)


def test_weekly_retraining_triggered_on_sunday() -> None:
    orchestrator = make_orchestrator()
    orchestrator.retrainer.retrain_all = MagicMock(return_value={"regime_classifier": True})  # type: ignore[method-assign]

    orchestrator._process_candle(make_candle(datetime(2026, 1, 4, 0, 5, tzinfo=UTC)))

    orchestrator.retrainer.retrain_all.assert_called_once()


def test_paper_broker_prices_updated_each_candle() -> None:
    orchestrator = make_orchestrator()
    orchestrator.execution_engine.update_paper_prices = MagicMock()  # type: ignore[method-assign]

    orchestrator._process_candle(make_candle(datetime(2026, 1, 1, 12, 15, tzinfo=UTC)))
    orchestrator._process_candle(make_candle(datetime(2026, 1, 1, 12, 30, tzinfo=UTC)))

    assert orchestrator.execution_engine.update_paper_prices.call_count == 2
