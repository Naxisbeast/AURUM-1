from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from aurum1.reports.phase_s5_d1_shadow_forward_journal import (
    EXECUTION_STATUS,
    RESEARCH_DECISIONS,
    run_phase_s5_journal,
)


SIGNALS = [
    {
        "signal_time": "2026-01-05T00:00:00+00:00",
        "entry_time": "2026-01-05T00:15:00+00:00",
        "direction": "BUY",
        "volatility_regime": "normal",
        "session": "asia",
        "weekday": "Monday",
        "outcome": "tp",
    },
    {
        "signal_time": "2026-01-05T01:00:00+00:00",
        "entry_time": "2026-01-05T01:15:00+00:00",
        "direction": "BUY",
        "volatility_regime": "high",
        "session": "asia",
        "weekday": "Monday",
        "outcome": "flat",
    },
    {
        "signal_time": "2026-01-05T08:00:00+00:00",
        "entry_time": "2026-01-05T08:15:00+00:00",
        "direction": "BUY",
        "volatility_regime": "normal",
        "session": "london",
        "weekday": "Monday",
        "outcome": "flat",
    },
    {
        "signal_time": "2026-01-05T13:00:00+00:00",
        "entry_time": "2026-01-05T13:15:00+00:00",
        "direction": "SELL",
        "volatility_regime": "normal",
        "session": "new_york",
        "weekday": "Monday",
        "outcome": "flat",
    },
    {
        "signal_time": "2026-01-05T16:00:00+00:00",
        "entry_time": "2026-01-05T16:15:00+00:00",
        "direction": "BUY",
        "volatility_regime": "normal",
        "session": "new_york",
        "weekday": "Monday",
        "outcome": "sl",
    },
    {
        "signal_time": "2026-01-06T00:00:00+00:00",
        "entry_time": "2026-01-06T00:15:00+00:00",
        "direction": "BUY",
        "volatility_regime": "normal",
        "session": "asia",
        "weekday": "Tuesday",
        "outcome": "both",
    },
]


def create_shadow_db(path: Path) -> None:
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
                ?, ?, 'raw_donchian_fixed_2r', ?, 'raw_signal', NULL,
                100.0, 99.0, 102.0, 1.0, 100.0, 100.0, 100.0, 0.0, 0.0, NULL, NULL
            )
            """,
            [(row["signal_time"], row["entry_time"], row["direction"]) for row in SIGNALS],
        )
        conn.executemany(
            "INSERT INTO shadow_candles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["entry_time"],
                    100.0,
                    candle_high(row["outcome"]),
                    candle_low(row["outcome"]),
                    candle_close(row["outcome"]),
                    1.0,
                    "no_signal",
                    "",
                )
                for row in SIGNALS
            ],
        )


def candle_high(outcome: str) -> float:
    if outcome in {"tp", "both"}:
        return 101.1
    return 100.2


def candle_low(outcome: str) -> float:
    if outcome in {"sl", "both"}:
        return 98.9
    return 99.4


def candle_close(outcome: str) -> float:
    if outcome == "tp":
        return 101.0
    if outcome in {"sl", "both"}:
        return 99.0
    return 100.0


def create_s4_context(report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp",
        "raw_signal_id",
        "instrument",
        "timeframe",
        "direction",
        "candidate_name",
        "candidate_decision",
        "blocked_reason",
        "volatility_regime",
        "session",
        "weekday",
        "exit_model",
        "simulated_r",
        "simulated_outcome",
        "bars_held",
    ]
    with (report_dir / "phase_s4_candidate_decisions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in SIGNALS:
            writer.writerow(
                {
                    "timestamp": row["signal_time"],
                    "raw_signal_id": row["signal_time"],
                    "instrument": "XAU_USD",
                    "timeframe": "M15",
                    "direction": row["direction"],
                    "candidate_name": "D1",
                    "candidate_decision": "",
                    "blocked_reason": "",
                    "volatility_regime": row["volatility_regime"],
                    "session": row["session"],
                    "weekday": row["weekday"],
                    "exit_model": "fixed_1r",
                    "simulated_r": "",
                    "simulated_outcome": "",
                    "bars_held": "",
                }
            )


def read_csv_rows(path: str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_phase_s5_logs_d1_decisions_outcomes_and_duplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "donchian_shadow.sqlite3"
    report_dir = tmp_path / "forward_shadow"
    create_shadow_db(db_path)
    create_s4_context(report_dir)

    result = run_phase_s5_journal(db_path, report_dir, as_of="2026-01-07T00:00:00Z")

    for output_path in result["paths"].values():
        assert Path(output_path).exists()

    summary = result["summary"]
    assert summary["classification"] == "research-only"
    assert summary["research_decision"] in RESEARCH_DECISIONS
    assert summary["research_decision"] == "PASS_D1_SHADOW_JOURNAL_READY"
    assert summary["safety"]["orders_sent"] == "no"
    assert summary["safety"]["live_or_paper_behavior_modified"] is False
    assert summary["state"]["total_take"] == 3
    assert summary["state"]["total_hold"] == 3

    rows = {row["raw_signal_id"]: row for row in read_csv_rows(result["paths"]["journal_csv"])}
    assert len(rows) == len(SIGNALS)
    assert {row["execution_status"] for row in rows.values()} == {EXECUTION_STATUS}

    take_tp = rows["2026-01-05T00:00:00+00:00"]
    assert take_tp["d1_decision"] == "TAKE"
    assert take_tp["blocked_reason"] == "none"
    assert take_tp["target_1r"] == "101.0"
    assert take_tp["risk_distance"] == "1.0"
    assert take_tp["outcome_status"] == "tp_hit"
    assert take_tp["realized_r"] == "1.0"

    assert rows["2026-01-05T01:00:00+00:00"]["d1_decision"] == "HOLD"
    assert rows["2026-01-05T01:00:00+00:00"]["blocked_reason"] == "high_volatility"
    assert rows["2026-01-05T08:00:00+00:00"]["d1_decision"] == "HOLD"
    assert rows["2026-01-05T08:00:00+00:00"]["blocked_reason"] == "london_session"
    assert rows["2026-01-05T13:00:00+00:00"]["d1_decision"] == "HOLD"
    assert rows["2026-01-05T13:00:00+00:00"]["blocked_reason"] == "short_side_not_enabled"

    assert rows["2026-01-05T16:00:00+00:00"]["outcome_status"] == "sl_hit"
    assert rows["2026-01-05T16:00:00+00:00"]["realized_r"] == "-1.0"

    same_candle = rows["2026-01-06T00:00:00+00:00"]
    assert same_candle["d1_decision"] == "TAKE"
    assert same_candle["outcome_status"] == "sl_hit"
    assert same_candle["realized_r"] == "-1.0"

    duplicate_result = run_phase_s5_journal(db_path, report_dir, as_of="2026-01-07T00:15:00Z")
    duplicate_summary = duplicate_result["summary"]
    assert duplicate_summary["research_decision"] == "NO_NEW_SIGNALS"
    assert duplicate_summary["new_decisions_logged"] == 0
    assert duplicate_summary["duplicate_count"] == len(SIGNALS)
    assert duplicate_summary["state"]["total_duplicates_skipped"] == len(SIGNALS)
    assert len(read_csv_rows(duplicate_result["paths"]["journal_csv"])) == len(SIGNALS)


def test_phase_s5_dry_run_does_not_write_state_or_journal(tmp_path: Path) -> None:
    db_path = tmp_path / "donchian_shadow.sqlite3"
    report_dir = tmp_path / "forward_shadow"
    create_shadow_db(db_path)
    create_s4_context(report_dir)

    result = run_phase_s5_journal(db_path, report_dir, dry_run=True, as_of="2026-01-07T00:00:00Z")

    assert result["summary"]["dry_run"] is True
    assert result["summary"]["new_decisions_logged"] == len(SIGNALS)
    assert result["summary"]["take_count_new"] == 3
    assert result["summary"]["hold_count_new"] == 3
    assert not Path(result["paths"]["journal_csv"]).exists()
    assert not Path(result["paths"]["journal_jsonl"]).exists()
    assert not Path(result["paths"]["state_json"]).exists()
    assert not Path(result["paths"]["summary_json"]).exists()


def test_phase_s5_module_does_not_import_execution_paths() -> None:
    source = Path("aurum1/reports/phase_s5_d1_shadow_forward_journal.py").read_text(encoding="utf-8")

    assert "OandaBroker" not in source
    assert "ExecutionEngine" not in source
    assert "forward_shadow_donchian" not in source
    assert ".submit_order(" not in source
