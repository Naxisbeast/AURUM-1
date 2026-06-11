from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pandas as pd

from aurum1.reports.phase_s3_candidate_filter_shadow_replay import (
    S3_RESEARCH_DECISIONS,
    run_phase_s3_replay,
)


def create_shadow_db(path: Path) -> None:
    index = pd.date_range("2026-01-05T00:00:00Z", periods=36, freq="15min")
    candles = pd.DataFrame(
        {
            "open": [100.0] * len(index),
            "high": [100.3] * len(index),
            "low": [99.7] * len(index),
            "close": [100.0] * len(index),
            "volume": [1.0] * len(index),
            "signal_decision": ["no_signal"] * len(index),
            "notes": [""] * len(index),
        },
        index=index,
    )
    # Outcomes after entry for six raw signals:
    # loss, loss, win, loss, win, win.
    outcomes = ["loss", "loss", "win", "loss", "win", "win"]
    entry_positions = (1, 5, 9, 13, 17, 21)
    for entry_pos, outcome in zip(entry_positions, outcomes, strict=True):
        if outcome == "win":
            candles.iloc[entry_pos + 1, candles.columns.get_loc("high")] = 102.2
            candles.iloc[entry_pos + 1, candles.columns.get_loc("close")] = 102.0
        else:
            candles.iloc[entry_pos + 1, candles.columns.get_loc("low")] = 98.8
            candles.iloc[entry_pos + 1, candles.columns.get_loc("close")] = 99.0

    signals = []
    trades = []
    for idx, (entry_pos, outcome) in enumerate(zip(entry_positions, outcomes, strict=True), start=1):
        signal_time = index[entry_pos - 1].isoformat()
        entry_time = index[entry_pos].isoformat()
        exit_time = index[entry_pos + 1].isoformat()
        status = "entered" if idx <= 4 else "skipped"
        skip_reason = None if status == "entered" else "open_position_skip"
        exit_reason = "take_profit" if outcome == "win" else "stop_loss"
        signals.append((signal_time, entry_time, status, skip_reason, exit_time if status == "entered" else None, exit_reason if status == "entered" else None))
        if status == "entered":
            r_multiple = 2.0 if outcome == "win" else -1.0
            pnl = r_multiple * 100.0
            trades.append((signal_time, entry_time, exit_time, 102.0 if outcome == "win" else 99.0, exit_reason, pnl, pnl, r_multiple))

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
        conn.executemany(
            "INSERT INTO shadow_config VALUES (?, ?)",
            [
                ("direction", json.dumps("BUY_ONLY")),
                ("strategy", json.dumps("raw_donchian_fixed_2r")),
                ("instrument", json.dumps("XAU_USD")),
            ],
        )
        conn.executemany(
            """
            INSERT INTO shadow_signals VALUES (
                ?, ?, 'raw_donchian_fixed_2r', 'BUY', ?, ?,
                100.0, 99.0, 102.0, 1.0, 100.0, 100.0, 100.0, 0.0, 0.0, ?, ?
            )
            """,
            signals,
        )
        conn.executemany(
            """
            INSERT INTO shadow_trades VALUES (
                ?, ?, ?, 'raw_donchian_fixed_2r', 'BUY',
                100.0, 99.0, 102.0, 100.0, 100.0, 0.0, 0.0, 0.0,
                ?, ?, ?, ?, ?, 1
            )
            """,
            trades,
        )
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
        conn.executemany(
            "INSERT INTO shadow_equity_curve VALUES (?, ?, ?)",
            [
                (index[0].isoformat(), 10000.0, 0.0),
                (index[-1].isoformat(), 10000.0, 0.0),
            ],
        )


def create_s1_csvs(report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    trade_fields = [
        "timestamp",
        "signal_time",
        "entry_time",
        "exit_time",
        "instrument",
        "timeframe",
        "direction",
        "entry",
        "stop",
        "target",
        "exit_price",
        "realized_pnl",
        "realized_r",
        "outcome",
        "session_label",
        "weekday",
        "volatility_regime",
        "holding_bars",
    ]
    rows = [
        ("2026-01-05T00:00:00+00:00", "london", "Thursday", "high", -1.0),
        ("2026-01-05T01:00:00+00:00", "london", "Thursday", "high", -1.0),
        ("2026-01-05T02:00:00+00:00", "asia", "Monday", "normal", 2.0),
        ("2026-01-05T03:00:00+00:00", "london", "Wednesday", "low", -1.0),
    ]
    with (report_dir / "phase_s1_trade_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=trade_fields)
        writer.writeheader()
        for signal_time, session, weekday, volatility, realized_r in rows:
            writer.writerow(
                {
                    "timestamp": signal_time,
                    "signal_time": signal_time,
                    "entry_time": pd.Timestamp(signal_time) + pd.Timedelta(minutes=15),
                    "exit_time": pd.Timestamp(signal_time) + pd.Timedelta(minutes=30),
                    "instrument": "XAU_USD",
                    "timeframe": "M15",
                    "direction": "BUY",
                    "entry": 100.0,
                    "stop": 99.0,
                    "target": 102.0,
                    "exit_price": 102.0 if realized_r > 0.0 else 99.0,
                    "realized_pnl": realized_r * 100.0,
                    "realized_r": realized_r,
                    "outcome": "win" if realized_r > 0.0 else "loss",
                    "session_label": session,
                    "weekday": weekday,
                    "volatility_regime": volatility,
                    "holding_bars": 1,
                }
            )

    skipped_fields = [
        "timestamp",
        "signal_time",
        "entry_time",
        "instrument",
        "direction",
        "skip_reason",
        "session",
        "weekday",
        "volatility_regime",
        "simulated_trade_outcome",
        "simulated_r",
    ]
    skipped_rows = [
        ("2026-01-05T04:00:00+00:00", "new_york", "Tuesday", "normal", "win", 2.0),
        ("2026-01-05T05:00:00+00:00", "asia", "Wednesday", "normal", "win", 2.0),
    ]
    with (report_dir / "phase_s1_skipped_signal_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=skipped_fields)
        writer.writeheader()
        for signal_time, session, weekday, volatility, outcome, r_value in skipped_rows:
            writer.writerow(
                {
                    "timestamp": signal_time,
                    "signal_time": signal_time,
                    "entry_time": pd.Timestamp(signal_time) + pd.Timedelta(minutes=15),
                    "instrument": "XAU_USD",
                    "direction": "BUY",
                    "skip_reason": "open_position_skip",
                    "session": session,
                    "weekday": weekday,
                    "volatility_regime": volatility,
                    "simulated_trade_outcome": outcome,
                    "simulated_r": r_value,
                }
            )


def read_csv_rows(path: str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_phase_s3_replays_raw_signal_take_hold_filters(tmp_path: Path) -> None:
    db_path = tmp_path / "donchian_shadow.sqlite3"
    report_dir = tmp_path / "forward_shadow"
    create_shadow_db(db_path)
    create_s1_csvs(report_dir)

    result = run_phase_s3_replay(db_path, report_dir, as_of="2026-01-06T00:00:00Z")

    for output_path in result["paths"].values():
        assert Path(output_path).exists()

    summary = result["summary"]
    assert summary["classification"] == "research-only"
    assert summary["safety"]["orders_placed"] is False
    assert summary["research_decision"] in S3_RESEARCH_DECISIONS
    assert "SHORT_SIDE_MISSING" in summary["warnings"]

    metrics = {row["variant"]: row for row in read_csv_rows(result["paths"]["variant_metrics_csv"])}
    assert metrics["BASELINE_CURRENT"]["raw_signal_count"] == "6"
    assert metrics["BASELINE_CURRENT"]["take_count"] == "4"
    assert metrics["VOL_NOT_HIGH_FIXED_2R"]["take_count"] == "4"
    assert float(metrics["VOL_NOT_HIGH_FIXED_2R"]["avg_r"]) > float(metrics["BASELINE_CURRENT"]["avg_r"])
    assert metrics["VOL_NOT_HIGH_FIXED_2R"]["removed_losers"] == "2"

    decisions = read_csv_rows(result["paths"]["replay_decisions_csv"])
    high_holds = [
        row
        for row in decisions
        if row["variant"] == "VOL_NOT_HIGH_FIXED_2R" and row["volatility_regime"] == "high"
    ]
    assert {row["decision"] for row in high_holds} == {"HOLD"}
    assert {row["blocked_reason"] for row in high_holds} == {"volatility_high"}

    direction = read_csv_rows(result["paths"]["direction_audit_csv"])
    assert direction[0]["assessment"] == "SHORT_SIDE_MISSING"


def test_phase_s3_module_does_not_import_execution_paths() -> None:
    source = Path("aurum1/reports/phase_s3_candidate_filter_shadow_replay.py").read_text(encoding="utf-8")

    assert "OandaBroker" not in source
    assert "ExecutionEngine" not in source
    assert ".submit_order(" not in source
