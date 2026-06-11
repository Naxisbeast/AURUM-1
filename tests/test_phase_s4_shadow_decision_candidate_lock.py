from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from aurum1.reports.phase_s4_shadow_decision_candidate_lock import (
    LOCK_DECISIONS,
    run_phase_s4_lock,
)


RAW_SIGNALS = [
    ("s1", "high", "london", "Thursday", -1.0),
    ("s2", "high", "london", "Thursday", -1.0),
    ("s3", "normal", "asia", "Monday", 1.0),
    ("s4", "normal", "new_york", "Tuesday", 1.0),
    ("s5", "low", "asia", "Wednesday", 1.0),
    ("s6", "normal", "london_ny_overlap", "Friday", 1.0),
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
        signals = []
        trades = []
        for index, (signal_id, _, _, _, r_value) in enumerate(RAW_SIGNALS):
            timestamp = f"2026-01-05T0{index}:00:00+00:00"
            entry_time = f"2026-01-05T0{index}:15:00+00:00"
            status = "entered" if index < 4 else "skipped"
            signals.append((signal_id, entry_time, status, None if status == "entered" else "open_position_skip"))
            if status == "entered":
                trades.append((signal_id, entry_time, f"2026-01-05T0{index}:30:00+00:00", "take_profit" if r_value > 0 else "stop_loss", r_value * 100.0, r_value))
        conn.executemany(
            """
            INSERT INTO shadow_signals VALUES (
                ?, ?, 'raw_donchian_fixed_2r', 'BUY', ?, ?,
                100.0, 99.0, 102.0, 1.0, 100.0, 100.0, 100.0, 0.0, 0.0, NULL, NULL
            )
            """,
            signals,
        )
        conn.executemany(
            """
            INSERT INTO shadow_trades VALUES (
                ?, ?, ?, 'raw_donchian_fixed_2r', 'BUY',
                100.0, 99.0, 102.0, 100.0, 100.0, 0.0, 0.0, 0.0,
                102.0, ?, ?, ?, ?, 1
            )
            """,
            [(signal_id, entry, exit_time, reason, pnl, pnl, r_value) for signal_id, entry, exit_time, reason, pnl, r_value in trades],
        )
        conn.execute(
            "INSERT INTO shadow_candles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("2026-01-05T00:00:00+00:00", 100.0, 100.0, 100.0, 100.0, 1.0, "no_signal", ""),
        )
        conn.execute("INSERT INTO shadow_equity_curve VALUES (?, ?, ?)", ("2026-01-05T00:00:00+00:00", 10000.0, 0.0))


def create_s3_outputs(report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    decision_fields = [
        "variant",
        "variant_name",
        "context_filter",
        "timestamp",
        "instrument",
        "direction",
        "raw_signal_id",
        "original_status",
        "decision",
        "blocked_reason",
        "volatility_regime",
        "session",
        "weekday",
        "entry",
        "stop",
        "target",
        "simulated_exit_model",
        "simulated_r",
        "simulated_outcome",
        "bars_held",
    ]
    variants = {
        "BASELINE_CURRENT": ("Baseline current behavior", "current shadow behavior", "current_realized"),
        "VOL_NOT_HIGH_AND_NOT_LONDON_FIXED_1R": ("D1 source", "volatility_regime != high AND session != london", "fixed_1r"),
        "VOL_NOT_HIGH_AND_NOT_LONDON_FIXED_2R": ("D2 source", "volatility_regime != high AND session != london", "fixed_2r"),
        "NOT_LONDON_FIXED_1R": ("D3 source", "session != london", "fixed_1r"),
        "NORMAL_AND_NOT_LONDON_FIXED_1R": ("D4 source", "volatility_regime in normal/medium AND session != london", "fixed_1r"),
    }
    rows = []
    for variant, (name, context, exit_model) in variants.items():
        for index, (signal_id, volatility, session, weekday, r_value) in enumerate(RAW_SIGNALS):
            baseline_take = variant == "BASELINE_CURRENT" and index < 4
            if variant == "BASELINE_CURRENT":
                take = baseline_take
                blocked = "" if take else "current_shadow_skip"
            elif variant.startswith("VOL_NOT_HIGH_AND_NOT_LONDON"):
                take = volatility != "high" and session != "london"
                blocked = "" if take else "volatility_high" if volatility == "high" else "session_london"
            elif variant == "NOT_LONDON_FIXED_1R":
                take = session != "london"
                blocked = "" if take else "session_london"
            else:
                take = volatility in {"normal", "medium"} and session != "london"
                blocked = "" if take else "volatility_not_normal" if volatility not in {"normal", "medium"} else "session_london"
            rows.append(
                {
                    "variant": variant,
                    "variant_name": name,
                    "context_filter": context,
                    "timestamp": f"2026-01-05T0{index}:00:00+00:00",
                    "instrument": "XAU_USD",
                    "direction": "BUY",
                    "raw_signal_id": signal_id,
                    "original_status": "entered" if index < 4 else "skipped",
                    "decision": "TAKE" if take else "HOLD",
                    "blocked_reason": blocked,
                    "volatility_regime": volatility,
                    "session": session,
                    "weekday": weekday,
                    "entry": 100.0,
                    "stop": 99.0,
                    "target": 102.0,
                    "simulated_exit_model": exit_model,
                    "simulated_r": r_value if take else "",
                    "simulated_outcome": "win" if take and r_value > 0 else "loss" if take else "",
                    "bars_held": 1 if take else "",
                }
            )
    with (report_dir / "phase_s3_replay_decisions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=decision_fields)
        writer.writeheader()
        writer.writerows(rows)

    metric_fields = [
        "variant",
        "variant_name",
        "context_filter",
        "simulated_exit_model",
        "raw_signal_count",
        "take_count",
        "hold_count",
        "trade_retention_percent",
        "win_count",
        "loss_count",
        "win_rate",
        "avg_r",
        "median_r",
        "net_r",
        "profit_factor",
        "max_drawdown_r",
        "max_consecutive_losses",
        "removed_losers",
        "removed_winners",
        "net_filter_improvement_r",
    ]
    metric_rows = [
        ("BASELINE_CURRENT", "Baseline", "current", "current_realized", 6, 4, 2, 4 / 6, 1, 3, 0.25, -0.25, -1.0, -1.0, 0.5, -2.0, 2, 0, 0, 0.0),
        ("VOL_NOT_HIGH_AND_NOT_LONDON_FIXED_1R", "D1 source", "vol_not_high_and_not_london", "fixed_1r", 6, 4, 2, 4 / 6, 4, 0, 1.0, 1.0, 1.0, 4.0, 10.0, 0.0, 0, 2, 0, 5.0),
        ("VOL_NOT_HIGH_AND_NOT_LONDON_FIXED_2R", "D2 source", "vol_not_high_and_not_london", "fixed_2r", 6, 4, 2, 4 / 6, 4, 0, 1.0, 0.8, 0.8, 3.2, 10.0, 0.0, 0, 2, 0, 4.2),
        ("NOT_LONDON_FIXED_1R", "D3 source", "not_london", "fixed_1r", 6, 4, 2, 4 / 6, 4, 0, 1.0, 0.9, 0.9, 3.6, 10.0, 0.0, 0, 2, 0, 4.6),
        ("NORMAL_AND_NOT_LONDON_FIXED_1R", "D4 source", "normal_and_not_london", "fixed_1r", 6, 3, 3, 3 / 6, 3, 0, 1.0, 0.9, 0.9, 2.7, 10.0, 0.0, 0, 2, 0, 3.7),
    ]
    with (report_dir / "phase_s3_variant_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=metric_fields)
        writer.writeheader()
        for row in metric_rows:
            writer.writerow(dict(zip(metric_fields, row, strict=True)))

    direction_fields = ["metric", "buy_count", "sell_count", "unknown_count", "assessment", "config_direction", "config_strategy", "strategy_implies_long_only", "notes"]
    with (report_dir / "phase_s3_direction_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=direction_fields)
        writer.writeheader()
        writer.writerow(
            {
                "metric": "raw_signals",
                "buy_count": 6,
                "sell_count": 0,
                "unknown_count": 0,
                "assessment": "SHORT_SIDE_MISSING",
                "config_direction": "BUY_ONLY",
                "config_strategy": "raw_donchian_fixed_2r",
                "strategy_implies_long_only": True,
                "notes": "No SELL raw signals found.",
            }
        )


def read_csv_rows(path: str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_phase_s4_locks_d1_shadow_candidate_without_execution_changes(tmp_path: Path) -> None:
    db_path = tmp_path / "donchian_shadow.sqlite3"
    report_dir = tmp_path / "forward_shadow"
    create_shadow_db(db_path)
    create_s3_outputs(report_dir)

    result = run_phase_s4_lock(db_path, report_dir, as_of="2026-01-06T00:00:00Z")

    for output_path in result["paths"].values():
        assert Path(output_path).exists()

    summary = result["summary"]
    assert summary["classification"] == "research-only"
    assert summary["safety"]["orders_placed"] is False
    assert summary["research_decision"] in LOCK_DECISIONS
    assert summary["locked_shadow_candidate"]["candidate_name"] == "D1"
    assert summary["research_decision"] == "LOCK_SHADOW_CANDIDATE_D1_SAMPLE_LIMITED"
    assert "SAMPLE_LIMITED" in summary["warnings"]
    assert "SHORT_SIDE_MISSING" in summary["warnings"]

    decisions = read_csv_rows(result["paths"]["candidate_decisions_csv"])
    d1_decisions = [row for row in decisions if row["candidate_name"] == "D1"]
    assert len(d1_decisions) == len(RAW_SIGNALS)
    assert {row["baseline_current_decision"] for row in d1_decisions}.issubset({"TAKE", "HOLD"})
    assert any(row["candidate_decision"] == "HOLD" and row["blocked_reason"] == "volatility_high" for row in d1_decisions)

    metrics = {row["candidate_name"]: row for row in read_csv_rows(result["paths"]["candidate_metrics_csv"])}
    assert metrics["D1"]["take_count"] == "4"
    assert metrics["D1"]["removed_losers_vs_baseline"] == "2"

    direction = read_csv_rows(result["paths"]["direction_investigation_csv"])
    assert direction[0]["sell_generation_status"] == "SELL_GENERATION_DISABLED_BY_LONG_ONLY_CONFIG"


def test_phase_s4_module_does_not_import_execution_paths() -> None:
    source = Path("aurum1/reports/phase_s4_shadow_decision_candidate_lock.py").read_text(encoding="utf-8")

    assert "OandaBroker" not in source
    assert "ExecutionEngine" not in source
    assert ".submit_order(" not in source
