from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

import pytest

from dashboard.forward_shadow_dashboard import (
    build_status,
    connect_readonly_sqlite,
    is_read_query,
    list_weekly_reports,
    load_shadow_snapshot,
    load_weekly_report,
)


def make_shadow_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE shadow_equity_curve (
                timestamp TEXT PRIMARY KEY,
                equity REAL NOT NULL,
                drawdown REAL NOT NULL
            );
            CREATE TABLE shadow_trades (
                signal_time TEXT PRIMARY KEY,
                entry_time TEXT NOT NULL,
                exit_time TEXT NOT NULL,
                strategy TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                units REAL NOT NULL,
                risk_amount REAL NOT NULL,
                spread_estimate REAL NOT NULL,
                entry_slippage_estimate REAL NOT NULL,
                exit_slippage_estimate REAL NOT NULL,
                exit_price REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                gross_pnl REAL NOT NULL,
                net_pnl REAL NOT NULL,
                r_multiple REAL NOT NULL,
                holding_bars INTEGER NOT NULL
            );
            CREATE TABLE shadow_signals (
                signal_time TEXT PRIMARY KEY,
                entry_time TEXT NOT NULL,
                strategy TEXT NOT NULL,
                direction TEXT NOT NULL,
                status TEXT NOT NULL,
                skip_reason TEXT,
                entry_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                atr REAL NOT NULL,
                units REAL NOT NULL,
                risk_amount REAL NOT NULL,
                target_risk_amount REAL NOT NULL,
                spread_estimate REAL NOT NULL,
                slippage_estimate REAL NOT NULL,
                exit_time TEXT,
                exit_reason TEXT
            );
            CREATE TABLE shadow_candles (
                timestamp TEXT PRIMARY KEY,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                signal_decision TEXT NOT NULL,
                notes TEXT NOT NULL
            );
            CREATE TABLE shadow_events (
                id INTEGER PRIMARY KEY,
                event_time TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT NOT NULL
            );
            CREATE TABLE shadow_run_log (
                run_at TEXT PRIMARY KEY,
                strategy TEXT NOT NULL,
                signal_count INTEGER NOT NULL,
                trade_count INTEGER NOT NULL,
                skipped_count INTEGER NOT NULL,
                notes TEXT NOT NULL
            );
            """
        )
        conn.execute("INSERT INTO shadow_equity_curve VALUES (?, ?, ?)", ("2026-01-01T00:00:00+00:00", 10000.0, 0.0))
        conn.execute("INSERT INTO shadow_candles VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("2026-01-01T00:00:00+00:00", 1, 2, 0.5, 1.5, 1, "no_signal", ""))
        conn.execute(
            "INSERT INTO shadow_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:15:00+00:00",
                "raw_donchian_fixed_2r",
                "BUY",
                "skipped",
                "open_position_skip",
                1.0,
                0.9,
                1.2,
                0.1,
                1.0,
                25.0,
                25.0,
                0.03,
                0.01,
                None,
                None,
            ),
        )
        conn.execute("INSERT INTO shadow_events VALUES (?, ?, ?, ?, ?, ?)", (1, "2026-01-01T00:10:00+00:00", "heartbeat", "INFO", "ok", "{}"))
        conn.execute("INSERT INTO shadow_run_log VALUES (?, ?, ?, ?, ?, ?)", ("2026-01-01T00:20:00+00:00", "raw_donchian_fixed_2r", 1, 0, 1, "{}"))


def test_dashboard_read_helpers_open_sqlite_readonly(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.sqlite3"
    make_shadow_db(db_path)

    conn, error = connect_readonly_sqlite(db_path)

    assert error is None
    assert conn is not None
    with conn:
        assert conn.execute("SELECT COUNT(*) FROM shadow_equity_curve").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE blocked (id INTEGER)")


def test_dashboard_missing_db_is_graceful(tmp_path: Path) -> None:
    conn, error = connect_readonly_sqlite(tmp_path / "missing.sqlite3")

    assert conn is None
    assert error is not None
    assert "Missing database" in error


def test_dashboard_snapshot_and_status_parse_shadow_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.sqlite3"
    make_shadow_db(db_path)

    snapshot = load_shadow_snapshot(db_path)
    status = build_status(snapshot)

    assert status["service_mode"] == "research-only"
    assert status["strategy"] == "raw_donchian_fixed_2r"
    assert status["latest_equity"] == 10000.0
    assert status["signal_count"] == 1
    assert status["skipped_count"] == 1


def test_weekly_json_parsing_uses_latest_report(tmp_path: Path) -> None:
    older = tmp_path / "donchian_shadow_weekly_20260101_000000.json"
    latest = tmp_path / "donchian_shadow_weekly_20260108_000000.json"
    older.write_text(json.dumps({"net_pnl": 1.0}), encoding="utf-8")
    latest.write_text(json.dumps({"net_pnl": 2.0, "health": {"status": "ok"}}), encoding="utf-8")
    os.utime(older, (1, 1))
    os.utime(latest, (2, 2))

    reports = list_weekly_reports(tmp_path)
    parsed, error = load_weekly_report(reports[0])

    assert reports[0] == latest
    assert error is None
    assert parsed["net_pnl"] == 2.0
    assert parsed["health"]["status"] == "ok"


def test_dashboard_accepts_only_read_queries() -> None:
    assert is_read_query("SELECT * FROM shadow_trades")
    assert is_read_query("WITH latest AS (SELECT 1) SELECT * FROM latest")
    assert not is_read_query("CREATE TABLE x (id INTEGER)")


def test_no_write_sql_statements_exist_in_dashboard_code() -> None:
    source = Path("dashboard/forward_shadow_dashboard.py").read_text(encoding="utf-8")

    for token in ["INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE"]:
        assert re.search(rf"\b{token}\b", source, flags=re.IGNORECASE) is None
