"""Tests for the trade quality scoring module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from monitor.trade_quality import (
    classify_session,
    score_trade,
    generate_quality_report,
    format_quality_report,
)


def _trade(
    direction: str = "BUY",
    entry: float = 100.0,
    exit_: float = 104.0,
    sl: float = 98.0,
    tp: float = 104.0,
    r: float = 2.0,
    pnl: float = 4.0,
    reason: str = "take_profit",
    open_time: str | None = None,
    closed_at: str | None = None,
    intended_entry: float | None = None,
    entry_slip: float = 0.0,
    intended_exit: float | None = None,
    exit_slip: float = 0.0,
) -> dict:
    """Helper to create a trade dict for testing."""
    if open_time is None:
        open_time = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    if closed_at is None:
        closed_at = datetime.now(UTC).isoformat()
    if intended_entry is None:
        intended_entry = entry
    if intended_exit is None:
        intended_exit = exit_

    return {
        "direction": direction,
        "entry": entry,
        "actual_entry": entry + entry_slip * direction_mul(direction, 1),
        "exit": exit_,
        "actual_exit": exit_ - exit_slip * direction_mul(direction, 1),
        "stop_loss": sl,
        "take_profit": tp,
        "units": 1.0,
        "r_multiple": r,
        "r": r,
        "net_pnl": pnl,
        "pnl": pnl,
        "spread_cost": 0.03,
        "total_slippage_cost": 0.0,
        "risk_amount": abs(entry - sl),
        "reason": reason,
        "open_time": open_time,
        "closed_at": closed_at,
        "intended_entry": intended_entry,
        "intended_exit": intended_exit,
    }


def direction_mul(direction: str, val: float) -> float:
    return val if direction == "BUY" else -val


class TestClassifySession:
    """Session classification by UTC hour."""

    def test_asian_session(self):
        dt = datetime(2026, 1, 1, 4, 0, tzinfo=UTC)
        assert classify_session(dt) == "ASIAN"

    def test_london_session(self):
        dt = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        assert classify_session(dt) == "LONDON"

    def test_overlap_session(self):
        dt = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
        assert classify_session(dt) == "OVERLAP"

    def test_ny_session(self):
        dt = datetime(2026, 1, 1, 18, 0, tzinfo=UTC)
        assert classify_session(dt) == "NY"

    def test_closed_session(self):
        dt = datetime(2026, 1, 1, 23, 0, tzinfo=UTC)
        assert classify_session(dt) == "CLOSED"


class TestScoreTrade:
    """Per-trade quality scoring."""

    def test_perfect_tp_win(self):
        # 1h trade at 2R = perfect on all dimensions
        now = datetime.now(UTC)
        t = _trade(direction="BUY", entry=100.0, exit_=104.0, r=2.0, reason="take_profit",
                   open_time=(now - timedelta(hours=1)).isoformat(),
                   closed_at=now.isoformat())
        s = score_trade(t)
        assert s.quality_score >= 95.0
        assert s.entry_quality == 100.0
        assert s.exit_quality == 100.0
        assert s.r_efficiency >= 95.0

    def test_clean_stop_loss(self):
        t = _trade(direction="SELL", entry=100.0, exit_=101.0, r=-1.0, reason="stop_loss")
        s = score_trade(t)
        assert s.exit_quality == 50.0  # clean SL = 50
        assert s.r_efficiency >= 45.0  # -1R = ~50 r-efficiency
        assert s.quality_score < 100.0

    def test_stop_loss_gap(self):
        t = _trade(direction="BUY", entry=100.0, exit_=97.0, r=-1.5, reason="stop_loss_gap")
        s = score_trade(t)
        assert s.exit_quality == 0.0  # gap = 0
        assert s.quality_score < 50.0

    def test_entry_slippage_penalty(self):
        t = _trade(direction="BUY", entry=100.0, entry_slip=0.05)
        s = score_trade(t)
        assert s.entry_quality < 100.0

    def test_exit_slippage_penalty(self):
        t = _trade(direction="SELL", exit_slip=0.05)
        s = score_trade(t)
        assert s.exit_quality < 100.0

    def test_zero_r_multiple(self):
        t = _trade(direction="BUY", r=0.0, reason="manual_close")
        s = score_trade(t)
        assert s.r_efficiency == 0.0

    def classifies_session(self):
        t = _trade(open_time=datetime(2026, 1, 1, 10, 0, tzinfo=UTC).isoformat())
        s = score_trade(t)
        assert s.session == "LONDON"


class TestQualityReport:
    """Aggregate quality report."""

    def test_empty_trades(self):
        report = generate_quality_report([])
        assert report.n_trades == 0
        assert report.avg_quality_score == 0.0

    def test_single_trade(self):
        t = _trade()
        report = generate_quality_report([t])
        assert report.n_trades == 1
        assert report.avg_quality_score > 0

    def test_multiple_trades(self):
        trades = [
            _trade(direction="BUY", r=2.0),
            _trade(direction="SELL", r=-1.0),
        ]
        report = generate_quality_report(trades)
        assert report.n_trades == 2
        assert "BUY" in report.quality_by_direction
        assert "SELL" in report.quality_by_direction

    def test_quality_by_session(self):
        t1 = _trade(open_time=datetime(2026, 1, 1, 10, 0, tzinfo=UTC).isoformat())
        t2 = _trade(open_time=datetime(2026, 1, 1, 4, 0, tzinfo=UTC).isoformat())
        report = generate_quality_report([t1, t2])
        assert "LONDON" in report.quality_by_session
        assert "ASIAN" in report.quality_by_session

    def test_best_and_worst_trade(self):
        good = _trade(direction="BUY", r=2.0, reason="take_profit")
        bad = _trade(direction="BUY", r=-1.5, reason="stop_loss_gap")
        report = generate_quality_report([good, bad])
        assert report.best_trade is not None
        assert report.worst_trade is not None
        assert report.best_trade.quality_score > report.worst_trade.quality_score

    def test_score_distribution(self):
        trades = [_trade() for _ in range(5)]
        report = generate_quality_report(trades)
        dist = report.entries.get("score_distribution", {})
        assert sum(dist.values()) == 5

    def test_format_report_contains_stats(self):
        t = _trade()
        report = generate_quality_report([t])
        text = format_quality_report(report)
        assert "TRADE QUALITY REPORT" in text
        assert str(report.n_trades) in text
