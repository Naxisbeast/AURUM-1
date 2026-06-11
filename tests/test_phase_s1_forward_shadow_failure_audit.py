from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pandas as pd

from aurum1.reports.phase_s1_forward_shadow_failure_audit import (
    RESEARCH_DECISIONS,
    run_phase_s1_audit,
)


def create_shadow_db(path: Path) -> None:
    index = pd.date_range("2026-01-05T00:00:00Z", periods=40, freq="15min")
    candles = pd.DataFrame(
        {
            "open": [100.0] * len(index),
            "high": [100.2] * len(index),
            "low": [99.8] * len(index),
            "close": [100.0] * len(index),
            "volume": [1.0] * len(index),
            "signal_decision": ["no_signal"] * len(index),
            "notes": [""] * len(index),
        },
        index=index,
    )
    candles.iloc[22, candles.columns.get_loc("low")] = 98.8
    candles.iloc[22, candles.columns.get_loc("close")] = 99.0
    candles.iloc[26, candles.columns.get_loc("high")] = 102.2
    candles.iloc[26, candles.columns.get_loc("close")] = 102.0
    candles.iloc[30, candles.columns.get_loc("high")] = 102.2
    candles.iloc[30, candles.columns.get_loc("close")] = 102.0
    candles.iloc[34, candles.columns.get_loc("low")] = 98.7
    candles.iloc[34, candles.columns.get_loc("close")] = 99.0

    signals = [
        ("2026-01-05T05:00:00+00:00", "2026-01-05T05:15:00+00:00", "entered", None, 1.0, None, "stop_loss"),
        ("2026-01-05T06:00:00+00:00", "2026-01-05T06:15:00+00:00", "entered", None, 1.1, None, "take_profit"),
        ("2026-01-05T07:00:00+00:00", "2026-01-05T07:15:00+00:00", "skipped", "open_position_skip", 1.2, None, None),
        ("2026-01-05T08:00:00+00:00", "2026-01-05T08:15:00+00:00", "skipped", "open_position_skip", 1.3, None, None),
    ]
    trades = [
        ("2026-01-05T05:00:00+00:00", "2026-01-05T05:15:00+00:00", "2026-01-05T05:30:00+00:00", 99.0, "stop_loss", -100.0, -100.0, -1.0, 1),
        ("2026-01-05T06:00:00+00:00", "2026-01-05T06:15:00+00:00", "2026-01-05T06:30:00+00:00", 102.0, "take_profit", 200.0, 200.0, 2.0, 1),
    ]
    equity = [
        ("2026-01-05T05:15:00+00:00", 10000.0, 0.0),
        ("2026-01-05T05:30:00+00:00", 9900.0, -0.01),
        ("2026-01-05T06:30:00+00:00", 10100.0, 0.0),
        ("2026-01-05T08:30:00+00:00", 10100.0, 0.0),
    ]

    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE shadow_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
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
            CREATE TABLE shadow_equity_curve (
                timestamp TEXT PRIMARY KEY,
                equity REAL NOT NULL,
                drawdown REAL NOT NULL
            );
            """
        )
        conn.execute("INSERT INTO shadow_config VALUES (?, ?)", ("instrument", json.dumps("XAU_USD")))
        conn.executemany(
            """
            INSERT INTO shadow_signals VALUES (
                ?, ?, 'raw_donchian_fixed_2r', 'BUY', ?, ?, 100.0, 99.0, 102.0,
                ?, 100.0, 100.0, 100.0, 0.0, 0.0, ?, ?
            )
            """,
            signals,
        )
        conn.executemany(
            """
            INSERT INTO shadow_trades VALUES (
                ?, ?, ?, 'raw_donchian_fixed_2r', 'BUY', 100.0, 99.0, 102.0,
                100.0, 100.0, 0.0, 0.0, 0.0, ?, ?, ?, ?, ?, ?
            )
            """,
            trades,
        )
        conn.executemany("INSERT INTO shadow_equity_curve VALUES (?, ?, ?)", equity)
        conn.executemany(
            "INSERT INTO shadow_candles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    timestamp.isoformat(),
                    float(row.open),
                    float(row.high),
                    float(row.low),
                    float(row.close),
                    float(row.volume),
                    str(row.signal_decision),
                    str(row.notes),
                )
                for timestamp, row in candles.iterrows()
            ],
        )


def read_csv_rows(path: str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_phase_s1_audit_writes_outputs_and_core_diagnostics(tmp_path: Path) -> None:
    db_path = tmp_path / "donchian_shadow.sqlite3"
    report_dir = tmp_path / "forward_shadow"
    report_dir.mkdir()
    create_shadow_db(db_path)
    (report_dir / "donchian_shadow_weekly_20260105_120000.json").write_text(
        json.dumps({"net_pnl": 100.0, "trade_count": 2, "average_r": 0.5}),
        encoding="utf-8",
    )

    result = run_phase_s1_audit(db_path, report_dir, as_of="2026-01-06T00:00:00Z")

    for output_path in result["paths"].values():
        assert Path(output_path).exists()

    summary = result["summary"]
    assert summary["classification"] == "research-only"
    assert summary["safety"]["orders_placed"] is False
    assert summary["research_decision"] in RESEARCH_DECISIONS
    assert summary["latest_weekly_reports"]

    trades = read_csv_rows(result["paths"]["trade_audit_csv"])
    assert {row["outcome"] for row in trades} == {"win", "loss"}
    assert all(row["session_label"] for row in trades)
    assert all(row["weekday"] == "Monday" for row in trades)

    skipped = read_csv_rows(result["paths"]["skipped_signal_audit_csv"])
    assert {row["simulated_trade_outcome"] for row in skipped} == {"win", "loss"}
    assert sum(float(row["avoided_loss_r"] or 0.0) for row in skipped) > 0.0
    assert sum(float(row["missed_profit_r"] or 0.0) for row in skipped) > 0.0
    assert summary["skipped_signal_summary"]["skipping_logic_assessment"] == "hurting"

    breakdown = read_csv_rows(result["paths"]["failure_mode_breakdown_csv"])
    assert {"direction", "session", "weekday", "volatility_regime"}.issubset({row["group_type"] for row in breakdown})

    exits = read_csv_rows(result["paths"]["exit_comparison_csv"])
    assert {"fixed_1r", "fixed_1_5r", "fixed_2r", "trailing_stop", "next_structural_high_low"} == {
        row["exit_name"] for row in exits
    }

    drawdown = read_csv_rows(result["paths"]["drawdown_attribution_csv"])
    assert {"worst_trade", "worst_drawdown_window", "loss_cluster"}.issubset({row["section"] for row in drawdown})


def test_phase_s1_audit_module_does_not_import_execution_paths() -> None:
    source = Path("aurum1/reports/phase_s1_forward_shadow_failure_audit.py").read_text(encoding="utf-8")

    assert "OandaBroker" not in source
    assert "ExecutionEngine" not in source
    assert ".submit_order(" not in source
