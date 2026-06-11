from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pandas as pd

from aurum1.reports.phase_s2_shadow_context_filter_simulation import (
    S2_RESEARCH_DECISIONS,
    run_phase_s2_simulation,
)


def create_shadow_db(path: Path) -> None:
    index = pd.date_range("2026-01-05T00:00:00Z", periods=36, freq="15min")
    candles = pd.DataFrame(
        {
            "open": [100.0] * len(index),
            "high": [100.4] * len(index),
            "low": [99.6] * len(index),
            "close": [100.0] * len(index),
            "volume": [1.0] * len(index),
            "signal_decision": ["no_signal"] * len(index),
            "notes": [""] * len(index),
        },
        index=index,
    )
    for pos in (2, 6, 10, 14, 18, 22):
        candles.iloc[pos, candles.columns.get_loc("high")] = 101.6
        candles.iloc[pos + 1, candles.columns.get_loc("high")] = 102.2
        candles.iloc[pos + 1, candles.columns.get_loc("close")] = 102.0

    signals = []
    trades = []
    for idx, entry_pos in enumerate((1, 5, 9, 13, 17, 21), start=1):
        signal_time = index[entry_pos - 1].isoformat()
        entry_time = index[entry_pos].isoformat()
        exit_time = index[entry_pos + 2].isoformat()
        r_multiple = -1.0 if idx in {1, 2, 3, 6} else 2.0
        net_pnl = r_multiple * 100.0
        signals.append((signal_time, entry_time, None, "take_profit" if r_multiple > 0 else "stop_loss"))
        trades.append((signal_time, entry_time, exit_time, "take_profit" if r_multiple > 0 else "stop_loss", net_pnl, net_pnl, r_multiple))

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
                ("instrument", json.dumps("XAU_USD")),
            ],
        )
        conn.executemany(
            """
            INSERT INTO shadow_signals VALUES (
                ?, ?, 'raw_donchian_fixed_2r', 'BUY', 'entered', NULL,
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
                102.0, ?, ?, ?, ?, 2
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
    ]
    rows = [
        ("2026-01-05T00:00:00+00:00", "london", "Thursday", "high", -1.0),
        ("2026-01-05T01:00:00+00:00", "london", "Thursday", "high", -1.0),
        ("2026-01-05T02:00:00+00:00", "new_york", "Wednesday", "high", -1.0),
        ("2026-01-05T03:00:00+00:00", "asia", "Monday", "medium", 2.0),
        ("2026-01-05T04:00:00+00:00", "new_york", "Tuesday", "normal", 2.0),
        ("2026-01-05T05:00:00+00:00", "london", "Wednesday", "low", -1.0),
    ]
    with (report_dir / "phase_s1_trade_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=trade_fields)
        writer.writeheader()
        for idx, (signal_time, session, weekday, volatility, realized_r) in enumerate(rows, start=1):
            writer.writerow(
                {
                    "timestamp": signal_time,
                    "signal_time": signal_time,
                    "entry_time": pd.Timestamp(signal_time) + pd.Timedelta(minutes=15),
                    "exit_time": pd.Timestamp(signal_time) + pd.Timedelta(minutes=45),
                    "instrument": "XAU_USD",
                    "timeframe": "M15",
                    "direction": "BUY",
                    "entry": 100.0,
                    "stop": 99.0,
                    "target": 102.0,
                    "exit_price": 102.0 if realized_r > 0 else 99.0,
                    "realized_pnl": realized_r * 100.0,
                    "realized_r": realized_r,
                    "outcome": "win" if realized_r > 0 else "loss",
                    "session_label": session,
                    "weekday": weekday,
                    "volatility_regime": volatility,
                }
            )

    skipped_fields = ["signal_time", "direction", "skip_reason", "simulated_trade_outcome", "simulated_r"]
    with (report_dir / "phase_s1_skipped_signal_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=skipped_fields)
        writer.writeheader()
        writer.writerow(
            {
                "signal_time": "2026-01-05T06:00:00+00:00",
                "direction": "BUY",
                "skip_reason": "open_position_skip",
                "simulated_trade_outcome": "loss",
                "simulated_r": -1.0,
            }
        )
    with (report_dir / "phase_s1_failure_mode_breakdown.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["group_type", "group_value"])
        writer.writeheader()
        writer.writerow({"group_type": "volatility_regime", "group_value": "high"})


def read_csv_rows(path: str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_phase_s2_simulates_context_filters_without_execution_changes(tmp_path: Path) -> None:
    db_path = tmp_path / "donchian_shadow.sqlite3"
    report_dir = tmp_path / "forward_shadow"
    create_shadow_db(db_path)
    create_s1_csvs(report_dir)

    result = run_phase_s2_simulation(db_path, report_dir, as_of="2026-01-06T00:00:00Z")

    for output_path in result["paths"].values():
        assert Path(output_path).exists()

    summary = result["summary"]
    assert summary["classification"] == "research-only"
    assert summary["safety"]["orders_placed"] is False
    assert summary["research_decision"] in S2_RESEARCH_DECISIONS

    variants = {row["variant"]: row for row in read_csv_rows(result["paths"]["variant_comparison_csv"])}
    assert variants["A"]["trade_count"] == "6"
    assert variants["B"]["trade_count"] == "3"
    assert float(variants["B"]["avg_r"]) > float(variants["A"]["avg_r"])
    assert variants["K"]["notes"] == "no SELL candidates found"

    skip = {row["variant"]: row for row in read_csv_rows(result["paths"]["skip_impact_csv"])}
    assert skip["B"]["executed_losing_trades_removed"] == "3"
    assert skip["B"]["executed_winning_trades_removed"] == "0"

    exit_rows = read_csv_rows(result["paths"]["exit_by_context_csv"])
    assert {"fixed_1r", "fixed_1_5r", "fixed_2r"}.issubset({row["exit_name"] for row in exit_rows})

    direction_rows = read_csv_rows(result["paths"]["direction_availability_csv"])
    assert direction_rows[0]["assessment"] == "SELL side disabled"


def test_phase_s2_module_does_not_import_execution_paths() -> None:
    source = Path("aurum1/reports/phase_s2_shadow_context_filter_simulation.py").read_text(encoding="utf-8")

    assert "OandaBroker" not in source
    assert "ExecutionEngine" not in source
    assert ".submit_order(" not in source
