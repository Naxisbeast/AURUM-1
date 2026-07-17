from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from scripts.forward_shadow_donchian import (
    RISK_PER_TRADE_PCT,
    assert_shadow_safety,
    build_weekly_report,
    init_shadow_db,
    run_shadow_once,
    setup_logging,
    status_report,
    validate_ohlcv,
    weekly_report,
    write_shadow_state,
)
from scripts.donchian_research_runner import donchian_signals, run_donchian_backtest
from scripts.research.research_edge_prototypes import build_research_features


def settings_for(tmp_path: Path) -> dict:
    return {
        "broker": {
            "paper_trade": True,
            "paper_initial_equity": 10000.0,
            "oanda": {
                "instrument": "XAU_USD",
                "api_key_env": "OANDA_API_KEY",
                "environment_env": "OANDA_ENV",
                "default_environment": "practice",
            },
        },
        "execution": {"paper_spread_pips": 1.5, "slippage_std_pips": 0.0},
        "risk": {"pip_size": 0.01, "max_spread_pips": 3.0},
        "instruments": {
            "XAU_USD": {
                "oanda_instrument": "XAU_USD",
                "account_currency": "USD",
                "pip_size": 0.01,
                "ounces_per_unit": 1.0,
                "units_per_lot": 100.0,
                "min_units": 1.0,
                "max_units": 1000.0,
                "unit_precision": 0,
                "min_lot_size": 0.01,
                "max_lot_size": 10.0,
                "lot_step": 0.01,
            }
        },
        "backtesting": {"market_data_db_path": str(tmp_path / "market.sqlite3")},
        "forward_shadow": {
            "strategy": "raw_donchian_fixed_2r",
            "paper_trade": True,
            "allow_oanda_orders": False,
            "risk_per_trade_pct": RISK_PER_TRADE_PCT,
            "lookback": 20,
            "exit_mode": "FIXED",
            "direction": "BUY_ONLY",
        },
    }


def write_market_cache(path: Path, frame: pd.DataFrame) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE ohlcv_M15 (
                timestamp TEXT PRIMARY KEY,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                source TEXT,
                instrument TEXT
            )
            """
        )
        records = []
        for timestamp, row in frame.iterrows():
            records.append(
                (
                    timestamp.isoformat(),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row.get("volume", 1.0)),
                    "oanda",
                    "XAU_USD",
                )
            )
        conn.executemany("INSERT INTO ohlcv_M15 VALUES (?, ?, ?, ?, ?, ?, ?, ?)", records)


def shadow_frame() -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=45, freq="15min", tz="UTC")
    frame = pd.DataFrame(
        {
            "open": [100.0] * len(index),
            "high": [100.0] * len(index),
            "low": [99.0] * len(index),
            "close": [99.5] * len(index),
            "volume": [1.0] * len(index),
            "source": ["oanda"] * len(index),
            "instrument": ["XAU_USD"] * len(index),
        },
        index=index,
    )
    frame.iloc[21, frame.columns.get_loc("high")] = 101.0
    frame.iloc[21, frame.columns.get_loc("close")] = 101.0
    frame.iloc[22, frame.columns.get_loc("open")] = 101.25
    frame.iloc[22, frame.columns.get_loc("high")] = 102.0
    frame.iloc[22, frame.columns.get_loc("low")] = 100.5
    frame.iloc[22, frame.columns.get_loc("close")] = 102.0
    frame.iloc[23, frame.columns.get_loc("open")] = 102.25
    frame.iloc[23, frame.columns.get_loc("high")] = 102.5
    frame.iloc[23, frame.columns.get_loc("low")] = 100.75
    frame.iloc[23, frame.columns.get_loc("close")] = 101.5
    frame.iloc[24, frame.columns.get_loc("high")] = 120.0
    frame.iloc[24, frame.columns.get_loc("close")] = 118.0
    return frame


def test_forward_shadow_blocks_oanda_order_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    monkeypatch.setenv("ALLOW_OANDA_ORDERS", "true")

    with pytest.raises(RuntimeError, match="ALLOW_OANDA_ORDERS"):
        assert_shadow_safety(settings)


@pytest.mark.parametrize(
    ("env_name", "env_value", "match"),
    [
        ("ALLOW_LIVE_TRADING", "true", "ALLOW_LIVE_TRADING"),
        ("OANDA_ENV", "live", "OANDA_ENV"),
    ],
)
def test_forward_shadow_blocks_unsafe_env_states(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_name: str,
    env_value: str,
    match: str,
) -> None:
    settings = settings_for(tmp_path)
    monkeypatch.setenv(env_name, env_value)

    with pytest.raises(RuntimeError, match=match):
        assert_shadow_safety(settings)


@pytest.mark.parametrize(
    ("config_path", "value", "match"),
    [
        (("broker", "paper_trade"), False, "paper_trade"),
        (("forward_shadow", "strategy"), "other", "strategy"),
        (("forward_shadow", "lookback"), 55, "lookback"),
        (("forward_shadow", "exit_mode"), "DONCHIAN_LOW", "exit_mode"),
        (("forward_shadow", "direction"), "SELL", "direction"),
        (("forward_shadow", "allow_oanda_orders"), True, "allow_oanda_orders"),
    ],
)
def test_forward_shadow_blocks_unsafe_config_states(
    tmp_path: Path,
    config_path: tuple[str, str],
    value: object,
    match: str,
) -> None:
    settings = settings_for(tmp_path)
    settings[config_path[0]][config_path[1]] = value

    with pytest.raises(RuntimeError, match=match):
        assert_shadow_safety(settings)


def test_forward_shadow_locks_strategy_parameters(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings["forward_shadow"]["risk_per_trade_pct"] = 0.01

    with pytest.raises(RuntimeError, match="0.25"):
        assert_shadow_safety(settings)


def test_shadow_trades_skips_and_candles_are_logged_idempotently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_OANDA_ORDERS", raising=False)
    monkeypatch.delenv("ALLOW_LIVE_TRADING", raising=False)
    settings = settings_for(tmp_path)
    market_db = tmp_path / "market.sqlite3"
    shadow_db = tmp_path / "shadow.sqlite3"
    write_market_cache(market_db, shadow_frame())

    state = run_shadow_once(settings, market_db, "2026-01-01T00:00:00Z")
    assert state.trades
    assert state.skipped["open_position_skip"] >= 1
    assert any(candle.signal_decision.startswith("entered") for candle in state.candles)

    init_shadow_db(shadow_db, settings)
    write_shadow_state(shadow_db, state, settings, "2026-01-01T00:00:00Z")
    write_shadow_state(shadow_db, state, settings, "2026-01-01T00:00:00Z")

    with sqlite3.connect(shadow_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM shadow_trades").fetchone()[0] == len(state.trades)
        assert conn.execute("SELECT COUNT(*) FROM shadow_signals WHERE status='skipped'").fetchone()[0] >= 1
        assert conn.execute("SELECT COUNT(*) FROM shadow_candles").fetchone()[0] == len(state.candles)
        assert conn.execute("SELECT COUNT(*) FROM shadow_audit_snapshots").fetchone()[0] > 0


def test_forward_shadow_status_survives_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_OANDA_ORDERS", raising=False)
    settings = settings_for(tmp_path)
    market_db = tmp_path / "market.sqlite3"
    shadow_db = tmp_path / "shadow.sqlite3"
    write_market_cache(market_db, shadow_frame())
    state = run_shadow_once(settings, market_db, "2026-01-01T00:00:00Z")

    init_shadow_db(shadow_db, settings)
    write_shadow_state(shadow_db, state, settings, "2026-01-01T00:00:00Z")
    init_shadow_db(shadow_db, settings)

    status = status_report(shadow_db, as_of="2026-01-01T11:00:00Z")
    assert status["status"] == "ok"
    assert status["trade_count"] == len(state.trades)
    assert status["signal_count"] == len(state.signals)
    assert status["audit_snapshot_count"] > 0


def test_forward_shadow_status_marks_stale_data_unhealthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_OANDA_ORDERS", raising=False)
    settings = settings_for(tmp_path)
    market_db = tmp_path / "market.sqlite3"
    shadow_db = tmp_path / "shadow.sqlite3"
    write_market_cache(market_db, shadow_frame())
    state = run_shadow_once(settings, market_db, "2026-01-01T00:00:00Z")
    init_shadow_db(shadow_db, settings)
    write_shadow_state(shadow_db, state, settings, "2026-01-01T00:00:00Z")

    status = status_report(shadow_db, as_of="2026-01-02T12:00:00Z")

    assert status["status"] == "unhealthy"
    assert status["stale_data"]["is_stale"] is True


def test_weekly_report_metrics_calculate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_OANDA_ORDERS", raising=False)
    settings = settings_for(tmp_path)
    market_db = tmp_path / "market.sqlite3"
    shadow_db = tmp_path / "shadow.sqlite3"
    write_market_cache(market_db, shadow_frame())
    state = run_shadow_once(settings, market_db, "2026-01-01T00:00:00Z")
    init_shadow_db(shadow_db, settings)
    write_shadow_state(shadow_db, state, settings, "2026-01-01T00:00:00Z")

    report = weekly_report(shadow_db, tmp_path, "2026-01-02T00:00:00Z")

    assert report["classification"] == "research-only"
    assert report["trade_count"] == len(state.trades)
    assert "gross_pnl" in report
    assert "api_failures" in report
    assert "health" in report
    assert "runtime_environment" in report["health"]


def test_forward_shadow_parity_against_donchian_historical_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_OANDA_ORDERS", raising=False)
    monkeypatch.delenv("ALLOW_LIVE_TRADING", raising=False)
    settings = settings_for(tmp_path)
    market_db = tmp_path / "market.sqlite3"
    ohlcv = shadow_frame()
    ohlcv.iloc[24, ohlcv.columns.get_loc("close")] = 101.0
    write_market_cache(market_db, ohlcv)

    shadow = run_shadow_once(settings, market_db, "2026-01-01T00:00:00Z")
    features = build_research_features(ohlcv)
    signals = donchian_signals(ohlcv, features, lookback=20, htf_filter=False)
    historical = run_donchian_backtest(
        "raw_donchian_fixed_2r",
        ohlcv,
        features,
        signals,
        settings,
        exit_mode="FIXED",
        initial_equity=10000.0,
        max_one_position=True,
    )

    assert len(shadow.trades) == historical.total_trades
    assert sum(trade.net_pnl for trade in shadow.trades) == pytest.approx(historical.total_net_pnl)


def test_forward_shadow_runner_has_no_oanda_order_path() -> None:
    source = Path("scripts/forward_shadow_donchian.py").read_text(encoding="utf-8")

    assert "OandaBroker" not in source
    assert ".submit_order(" not in source


def test_missing_or_malformed_data_fails_safely() -> None:
    frame = shadow_frame().drop(columns=["close"])

    with pytest.raises(RuntimeError, match="missing"):
        validate_ohlcv(frame)


def test_build_weekly_report_handles_empty_inputs() -> None:
    empty = pd.DataFrame()
    now = pd.Timestamp("2026-01-08T00:00:00Z")

    report = build_weekly_report(empty, empty, empty, empty, empty, now - pd.Timedelta(days=7), now)

    assert report["trade_count"] == 0
    assert report["paper_readiness"] == "failed"
    assert report["live_readiness"] == "failed"
