"""Tests for evidence collection tracker."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from monitor.evidence import (
    EvidenceCollector,
    RISK_REVIEW_TRADES,
    STRATEGY_REVIEW_TRADES,
    DSR_REVIEW_TRADES,
)


def _make_trade_db(db_path: Path, n_trades: int, start_time: datetime | None = None) -> None:
    """Create a test paper_trading.sqlite3 with N trades."""
    import sqlite3
    from contextlib import closing

    start = start_time or (datetime.now(UTC) - timedelta(days=n_trades))
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_time TEXT, exit_time TEXT, direction TEXT,
                entry_price REAL, exit_price REAL, stop_loss REAL,
                take_profit REAL, units REAL, r_multiple REAL,
                net_pnl REAL, exit_reason TEXT, spread_cost REAL,
                slippage_cost REAL
            )
        """)
        for i in range(n_trades):
            t = start + timedelta(hours=i * 6)
            direction = "BUY" if i % 2 == 0 else "SELL"
            is_win = i % 3 != 0  # ~66% win rate
            r = 2.0 if is_win else -1.0
            pnl = 50.0 if is_win else -25.0
            conn.execute("""
                INSERT INTO trades (entry_time, exit_time, direction, entry_price,
                    exit_price, stop_loss, take_profit, units, r_multiple,
                    net_pnl, exit_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                t.isoformat(), (t + timedelta(hours=4)).isoformat(),
                direction, 2000.0, 2050.0 if is_win else 1975.0,
                1980.0, 2100.0, 1, r, pnl,
                "take_profit" if is_win else "stop_loss",
            ))
        conn.commit()


class TestEvidenceCollector:
    """Evidence collection tests."""

    def _make_collector(self, n_trades: int = 10) -> EvidenceCollector:
        tmp = Path(tempfile.mkdtemp())
        db_path = tmp / "aurum1" / "data" / "paper_trading.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _make_trade_db(db_path, n_trades)
        # Create a collector pointing at the temp dir
        collector = EvidenceCollector(tmp)
        collector.paper_db = db_path
        return collector

    def test_initial_state(self):
        collector = EvidenceCollector(Path("."))
        report = collector.generate_report()
        assert report.total_trades >= 0
        assert report.risk_setting > 0  # reads from actual settings.yaml

    def test_trade_count(self):
        collector = self._make_collector(30)
        report = collector.generate_report()
        assert report.total_trades == 30

    def test_trades_to_50_gate(self):
        collector = self._make_collector(30)
        report = collector.generate_report()
        assert report.trades_remaining_to_50 == 20
        assert not report.risk_review_due

    def test_trades_to_100_gate(self):
        collector = self._make_collector(30)
        report = collector.generate_report()
        assert report.trades_remaining_to_100 == 70
        assert not report.strategy_review_due

    def test_trades_to_200_gate(self):
        collector = self._make_collector(30)
        report = collector.generate_report()
        assert report.trades_remaining_to_200 == 170
        assert not report.dsr_review_due

    def test_risk_review_triggers(self):
        collector = self._make_collector(50)
        report = collector.generate_report()
        assert report.risk_review_due

    def test_strategy_review_triggers(self):
        collector = self._make_collector(100)
        report = collector.generate_report()
        assert report.strategy_review_due

    def test_dsr_review_triggers_at_200(self):
        collector = self._make_collector(200)
        report = collector.generate_report()
        assert report.dsr_review_due
        assert report.trades_remaining_to_200 == 0

    def test_200_gate_constant(self):
        assert DSR_REVIEW_TRADES == 200

    def test_report_formatted(self):
        collector = self._make_collector(5)
        report = collector.generate_report()
        text = collector.format_report(report)
        assert "EVIDENCE COLLECTION REPORT" in text
        assert "0.35%" in text  # risk setting
        assert "DECISION GATES" in text

    def test_health_field_names_match_writer(self):
        """uptime and candle-age must read the keys the health file writes.

        Regression guard: evidence.py read `uptime_hours` and
        `latest_candle_age_minutes`, but the health file writes `uptime_seconds`
        and `market_latest_candle_age_minutes` — both silently yielded 0/None,
        so uptime always showed 0 and stale-data detection never fired.
        """
        import json
        tmp = Path(tempfile.mkdtemp())
        (tmp / "run").mkdir(parents=True, exist_ok=True)
        (tmp / "aurum1" / "data").mkdir(parents=True, exist_ok=True)
        (tmp / "run" / "d4_paper_trader_health.json").write_text(json.dumps({
            "equity": 12017.73,
            "peak_equity": 12017.73,
            "drawdown_pct": 0.0,
            "uptime_seconds": 828000.0,          # 230h
            "market_latest_candle_age_minutes": 5.0,
            "daily_pnl": 0.0,
        }))
        collector = EvidenceCollector(tmp)
        collector.paper_db = tmp / "aurum1" / "data" / "paper_trading.sqlite3"
        report = collector.generate_report()
        assert report.uptime_hours > 200, "uptime_hours should be derived from uptime_seconds"
        assert report.stale_data is False  # 5 min < 120 threshold
        assert report.current_equity == 12017.73

    def test_stale_data_detected_from_health(self):
        """When the candle age exceeds the threshold, stale_data must be True."""
        import json
        tmp = Path(tempfile.mkdtemp())
        (tmp / "run").mkdir(parents=True, exist_ok=True)
        (tmp / "aurum1" / "data").mkdir(parents=True, exist_ok=True)
        (tmp / "run" / "d4_paper_trader_health.json").write_text(json.dumps({
            "equity": 10000.0, "peak_equity": 10000.0, "drawdown_pct": 0.0,
            "uptime_seconds": 3600.0,
            "market_latest_candle_age_minutes": 300.0,  # 5h > 120 min
            "daily_pnl": 0.0,
        }))
        collector = EvidenceCollector(tmp)
        collector.paper_db = tmp / "aurum1" / "data" / "paper_trading.sqlite3"
        report = collector.generate_report()
        assert report.stale_data is True

    def test_trades_since_deploy(self):
        tmp = Path(tempfile.mkdtemp())
        db_path = tmp / "aurum1" / "data" / "paper_trading.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # 10 trades before deploy
        deploy = datetime.now(UTC) - timedelta(hours=1)
        _make_trade_db(db_path, 10, start_time=deploy - timedelta(days=5))
        # 5 trades after deploy
        _make_trade_db(db_path, 5, start_time=deploy + timedelta(hours=1))

        collector = EvidenceCollector(tmp, deploy_time=deploy)
        collector.paper_db = db_path
        report = collector.generate_report()
        assert report.total_trades == 15
        assert report.trades_at_new_risk >= 0

    def test_lifetime_stats(self):
        collector = self._make_collector(20)
        report = collector.generate_report()
        assert abs(report.lifetime_pnl) > 0
        assert report.lifetime_win_rate > 0

    def test_quality_distribution_present(self):
        collector = self._make_collector(10)
        report = collector.generate_report()
        assert len(report.quality_distribution) > 0

    def test_r_distribution_present(self):
        collector = self._make_collector(10)
        report = collector.generate_report()
        assert len(report.r_distribution) > 0

    def test_format_report_empty(self):
        collector = EvidenceCollector(Path("."))
        text = collector.format_report()
        assert "EVIDENCE COLLECTION REPORT" in text
