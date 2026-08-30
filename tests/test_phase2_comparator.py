"""Tests for Phase 2 — Live vs Backtest Comparator."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.paper_trading.run_live_vs_backtest_comparator import (
    D4_BASELINE,
    assess_alignment,
    compute_metrics,
    detect_drift,
    generate_report,
    load_trades_from_db,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_trade(
    direction: str = "BUY",
    r_multiple: float = 2.0,
    net_pnl: float = 44.0,
    exit_reason: str = "take_profit",
    entry_price: float = 100.0,
    stop_loss: float = 98.0,
    take_profit: float = 104.0,
) -> dict:
    return {
        "entry_time": "2026-07-01T01:00:00+00:00",
        "exit_time": "2026-07-01T03:00:00+00:00",
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": take_profit if "profit" in exit_reason else stop_loss,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "units": 1.0,
        "risk_amount": 2.0,
        "r_multiple": r_multiple,
        "net_pnl": net_pnl,
        "spread_cost": 0.50,
        "slippage_cost": 0.30,
        "exit_reason": exit_reason,
    }


def seed_paper_db(db_path: Path, trades: list[dict]):
    """Create a paper_trading.sqlite3 with the given trades."""
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_time TEXT,
                exit_time TEXT,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                units INTEGER NOT NULL,
                risk_amount REAL,
                r_multiple REAL,
                net_pnl REAL,
                spread_cost REAL,
                slippage_cost REAL,
                exit_reason TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        for t in trades:
            conn.execute("""
                INSERT INTO trades
                    (entry_time, exit_time, direction, entry_price, exit_price,
                     stop_loss, take_profit, units, risk_amount, r_multiple,
                     net_pnl, spread_cost, slippage_cost, exit_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                t.get("entry_time", ""),
                t.get("exit_time", ""),
                t["direction"],
                t["entry_price"],
                t.get("exit_price", 0),
                t["stop_loss"],
                t["take_profit"],
                int(t.get("units", 1)),
                t.get("risk_amount"),
                t.get("r_multiple"),
                t.get("net_pnl"),
                t.get("spread_cost"),
                t.get("slippage_cost"),
                t["exit_reason"],
            ))
        conn.commit()


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    """Test that compute_metrics produces correct aggregate stats."""

    def test_empty_trades(self):
        """No trades should return zero totals."""
        m = compute_metrics([])
        assert m["total_trades"] == 0

    def test_single_win(self):
        """A single winning trade should show WR=1.0."""
        m = compute_metrics([make_trade(r_multiple=2.0, exit_reason="take_profit")])
        assert m["total_trades"] == 1
        assert m["win_rate"] == 1.0
        assert m["mean_r"] == 2.0
        assert m["profit_factor"] > 0

    def test_single_loss(self):
        """A single losing trade should show WR=0.0."""
        m = compute_metrics([make_trade(r_multiple=-1.0, exit_reason="stop_loss", net_pnl=-22.0)])
        assert m["total_trades"] == 1
        assert m["win_rate"] == 0.0
        assert m["mean_r"] == -1.0

    def test_mixed_trades(self):
        """Mix of wins and losses produces correct stats."""
        trades = [
            make_trade(r_multiple=2.0, exit_reason="take_profit", net_pnl=44.0),
            make_trade(r_multiple=-1.0, exit_reason="stop_loss", net_pnl=-22.0),
            make_trade(r_multiple=2.0, exit_reason="take_profit", net_pnl=44.0),
            make_trade(r_multiple=-1.0, exit_reason="stop_loss", net_pnl=-22.0),
            make_trade(r_multiple=2.0, exit_reason="take_profit", net_pnl=44.0),
        ]
        m = compute_metrics(trades)
        assert m["total_trades"] == 5
        assert m["win_rate"] == pytest.approx(0.6)
        assert m["mean_r"] == pytest.approx(0.8)
        assert m["n_wins"] == 3
        assert m["n_losses"] == 2

    def test_losing_streak(self):
        """Consecutive losses should be counted."""
        trades = [
            make_trade(r_multiple=-1.0, exit_reason="stop_loss", net_pnl=-22.0),
            make_trade(r_multiple=-1.0, exit_reason="stop_loss", net_pnl=-22.0),
            make_trade(r_multiple=-1.0, exit_reason="stop_loss", net_pnl=-22.0),
            make_trade(r_multiple=2.0, exit_reason="take_profit", net_pnl=44.0),
        ]
        m = compute_metrics(trades)
        assert m["worst_losing_streak"] == 3

    def test_win_streak(self):
        """Consecutive wins should be counted."""
        trades = [
            make_trade(r_multiple=2.0, exit_reason="take_profit", net_pnl=44.0),
            make_trade(r_multiple=2.0, exit_reason="take_profit", net_pnl=44.0),
            make_trade(r_multiple=-1.0, exit_reason="stop_loss", net_pnl=-22.0),
            make_trade(r_multiple=2.0, exit_reason="take_profit", net_pnl=44.0),
        ]
        m = compute_metrics(trades)
        assert m["best_win_streak"] == 2

    def test_exit_reasons_counted(self):
        """Exit reasons should be tallied correctly."""
        trades = [
            make_trade(exit_reason="take_profit"),
            make_trade(exit_reason="stop_loss"),
            make_trade(exit_reason="take_profit"),
        ]
        m = compute_metrics(trades)
        assert m["exit_reasons"]["take_profit"] == 2
        assert m["exit_reasons"]["stop_loss"] == 1

    def test_profit_factor_when_no_losses(self):
        """All winning trades should produce a PF."""
        trades = [make_trade(r_multiple=2.0, exit_reason="take_profit") for _ in range(3)]
        m = compute_metrics(trades)
        assert m["profit_factor"] > 0  # should be some positive number

    def test_profit_factor_when_no_wins(self):
        """All losses should produce PF=0."""
        trades = [make_trade(r_multiple=-1.0, exit_reason="stop_loss", net_pnl=-22.0) for _ in range(3)]
        m = compute_metrics(trades)
        assert m["profit_factor"] == 0


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

class TestDetectDrift:
    """Test that drift detection correctly flags anomalies."""

    def test_no_drift_with_good_metrics(self):
        """Metrics close to baseline should produce no flags."""
        live = {
            "total_trades": 100,
            "win_rate": 0.37,
            "profit_factor": 1.14,
            "mean_r": 0.10,
            "avg_trades_per_day": 1.5,
            "worst_losing_streak": 10,
            "exit_reasons": {"take_profit": 37, "stop_loss": 63},
        }
        flags = detect_drift(live, D4_BASELINE)
        assert len(flags) == 0

    def test_low_win_rate_flags_warning(self):
        """Win rate below threshold should flag."""
        live = {
            "total_trades": 50,
            "win_rate": 0.20,
            "profit_factor": 0.70,
            "mean_r": -0.50,
            "avg_trades_per_day": 1.0,
            "worst_losing_streak": 10,
            "exit_reasons": {"take_profit": 10, "stop_loss": 40},
        }
        flags = detect_drift(live, D4_BASELINE)
        wr_flags = [f for f in flags if f["metric"] == "win_rate"]
        assert len(wr_flags) >= 1
        assert wr_flags[0]["severity"] == "critical"

    def test_low_profit_factor_flags(self):
        """PF below 0.90 should flag."""
        live = {
            "total_trades": 20,
            "win_rate": 0.30,
            "profit_factor": 0.85,
            "mean_r": -0.10,
            "avg_trades_per_day": 1.0,
            "worst_losing_streak": 10,
            "exit_reasons": {"take_profit": 6, "stop_loss": 14},
        }
        flags = detect_drift(live, D4_BASELINE)
        pf_flags = [f for f in flags if f["metric"] == "profit_factor"]
        assert len(pf_flags) >= 1

    def test_zero_trades_no_flags(self):
        """No trades should produce no flags."""
        live = {"total_trades": 0}
        flags = detect_drift(live, D4_BASELINE)
        assert len(flags) == 0

    def test_high_take_profit_rate_no_flag(self):
        """High TP rate within expected range is fine."""
        live = {
            "total_trades": 50,
            "win_rate": 0.42,  # just below 0.44 high threshold
            "profit_factor": 1.40,
            "mean_r": 0.50,
            "avg_trades_per_day": 1.5,
            "worst_losing_streak": 5,
            "exit_reasons": {"take_profit": 30, "stop_loss": 20},
        }
        flags = detect_drift(live, D4_BASELINE)
        assert len(flags) == 0

    def test_losing_streak_flags(self):
        """Losing streak exceeding baseline should flag."""
        live = {
            "total_trades": 50,
            "win_rate": 0.37,
            "profit_factor": 1.14,
            "mean_r": 0.10,
            "avg_trades_per_day": 1.5,
            "worst_losing_streak": 45,
            "exit_reasons": {"take_profit": 19, "stop_loss": 31},
        }
        flags = detect_drift(live, D4_BASELINE)
        streak_flags = [f for f in flags if f["metric"] == "losing_streak"]
        assert len(streak_flags) == 1


# ---------------------------------------------------------------------------
# R-distribution alignment (KS test)
# ---------------------------------------------------------------------------

class TestAssessAlignment:
    """Test statistical alignment assessment."""

    def test_insufficient_live_data(self):
        """Fewer than 5 live trades cannot be assessed."""
        result = assess_alignment([1.0, 2.0], [1.0] * 1000)
        assert result["assessment"] == "insufficient_data"

    def test_sufficient_data_runs_ks(self):
        """With enough data, a KS test should be attempted."""
        # Identical distributions — should be consistent when scipy is available
        backtest = [2.0] * 500 + [-1.0] * 500
        live = [2.0] * 6 + [-1.0] * 4
        result = assess_alignment(live, backtest)
        assert result["assessment"] in ("consistent", "scipy_not_available")
        assert result["live_samples"] == 10
        assert result["backtest_samples"] == 1000


# ---------------------------------------------------------------------------
# DB loading
# ---------------------------------------------------------------------------

class TestLoadTradesFromDB:
    """Test loading trades from SQLite."""

    def test_empty_db(self):
        """No DB file should return empty list."""
        trades = load_trades_from_db("/tmp/nonexistent_db_xyz.sqlite3")
        assert trades == []

    def test_no_trades_table(self):
        """DB without trades table should return empty."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "empty.sqlite3"
            with closing(sqlite3.connect(str(db))) as conn:
                conn.execute("CREATE TABLE other (id INTEGER)")
                conn.commit()
            trades = load_trades_from_db(db)
            assert trades == []

    def test_loads_trades(self):
        """Trades should load correctly from DB."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "paper_test.sqlite3"
            seed_trades = [
                make_trade(r_multiple=2.0, exit_reason="take_profit"),
                make_trade(r_multiple=-1.0, exit_reason="stop_loss"),
            ]
            seed_paper_db(db, seed_trades)
            trades = load_trades_from_db(db)
            assert len(trades) == 2
            assert trades[0]["direction"] == "BUY"
            assert trades[1]["exit_reason"] == "stop_loss"


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

class TestGenerateReport:
    """Test end-to-end report generation."""

    def test_report_with_no_trades(self):
        """Empty trades should produce a valid report."""
        report = generate_report([], D4_BASELINE)
        assert report["overall_health"] == "ok"
        assert report["live"]["total_trades"] == 0
        assert report["baseline"]["total_trades"] == 8178

    def test_report_with_trades(self):
        """Some trades should produce a structured report."""
        trades = [
            make_trade(r_multiple=2.0, exit_reason="take_profit"),
            make_trade(r_multiple=-1.0, exit_reason="stop_loss"),
        ]
        report = generate_report(trades, D4_BASELINE)
        assert report["live"]["total_trades"] == 2
        assert report["live"]["win_rate"] == 0.5
        assert report["r_distribution_alignment"]["assessment"] == "no_live_trades_yet"  # no backtest_r passed
        assert "comparison" in report

    def test_report_keys(self):
        """Report should contain all required sections."""
        trades = [make_trade(r_multiple=2.0, exit_reason="take_profit")]
        report = generate_report(trades, D4_BASELINE)
        assert "generated_at" in report
        assert "overall_health" in report
        assert "live" in report
        assert "baseline" in report
        assert "flags" in report
        assert "comparison" in report

    def test_report_json_serializable(self):
        """Report must be JSON-serializable."""
        report = generate_report([], D4_BASELINE)
        # Remove r_values if present
        if "r_values" in report.get("live", {}):
            del report["live"]["r_values"]
        json_str = json.dumps(report, indent=2, default=str)
        parsed = json.loads(json_str)
        assert parsed["baseline"]["total_trades"] == 8178


# ---------------------------------------------------------------------------
# Integration: DB → metrics → report
# ---------------------------------------------------------------------------

class TestDBToReportFlow:
    """End-to-end test: seed DB, load trades, compute metrics, detect drift."""

    def test_full_flow_good_trades(self):
        """Trades matching baseline should not flag."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "paper.sqlite3"
            # 40% win rate at 2R = 1.33 PF, slightly above baseline
            trades = []
            for _ in range(40):
                trades.append(make_trade(r_multiple=2.0, exit_reason="take_profit", net_pnl=44.0))
            for _ in range(60):
                trades.append(make_trade(r_multiple=-1.0, exit_reason="stop_loss", net_pnl=-22.0))
            seed_paper_db(db, trades)

            loaded = load_trades_from_db(db)
            assert len(loaded) == 100

            report = generate_report(loaded, D4_BASELINE)
            assert report["live"]["total_trades"] == 100
            assert report["live"]["win_rate"] == pytest.approx(0.40)
            assert report["live"]["profit_factor"] == pytest.approx(80 / 60, rel=0.1)

    def test_full_flow_bad_trades(self):
        """Trades far below baseline should flag."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "paper.sqlite3"
            # 20% win rate
            trades = []
            for _ in range(20):
                trades.append(make_trade(r_multiple=2.0, exit_reason="take_profit", net_pnl=44.0))
            for _ in range(80):
                trades.append(make_trade(r_multiple=-1.0, exit_reason="stop_loss", net_pnl=-22.0))
            seed_paper_db(db, trades)

            loaded = load_trades_from_db(db)
            report = generate_report(loaded, D4_BASELINE)
            assert report["live"]["total_trades"] == 100
            flags = report["flags"]
            wr_flags = [f for f in flags if f["metric"] == "win_rate"]
            pf_flags = [f for f in flags if f["metric"] == "profit_factor"]
            assert len(wr_flags) >= 1
            assert len(pf_flags) >= 1
