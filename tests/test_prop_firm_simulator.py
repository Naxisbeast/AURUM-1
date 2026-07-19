"""Tests for prop firm challenge simulator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from monitor.prop_firm_simulator import (
    FTMO_STANDARD,
    THE5ERS_HIGH_STAKES,
    FUNDING_PIPS_STANDARD,
    ChallengeState,
    apply_trade,
    init_challenge,
    simulate_from_trades,
    simulate_all_challenges,
    format_challenge_result,
)


class TestInitChallenge:
    """Challenge initialisation."""

    def test_initial_equity_matches_account_size(self):
        state = init_challenge(FTMO_STANDARD)
        assert state.current_equity == FTMO_STANDARD.account_size
        assert state.peak_equity == FTMO_STANDARD.account_size

    def test_initial_state_not_passed_or_failed(self):
        state = init_challenge(FTMO_STANDARD)
        assert not state.passed
        assert not state.failed

    def test_initial_progress_is_zero(self):
        state = init_challenge(FTMO_STANDARD)
        assert state.progress_pct == 0.0


class TestApplyTrade:
    """Applying trades to challenge state."""

    def test_winning_trade_increases_equity(self):
        state = init_challenge(FTMO_STANDARD)
        state = apply_trade(state, 500.0)
        assert state.current_equity == 100500.0
        assert state.trades == 1

    def test_losing_trade_decreases_equity(self):
        state = init_challenge(FTMO_STANDARD)
        state = apply_trade(state, -300.0)
        assert state.current_equity == 99700.0

    def test_peak_equity_tracks_highest(self):
        state = init_challenge(FTMO_STANDARD)
        state = apply_trade(state, 500.0)
        state = apply_trade(state, -200.0)
        assert state.peak_equity == 100500.0

    def test_daily_loss_reset(self):
        """Each new day resets daily start equity."""
        state = init_challenge(FTMO_STANDARD)
        day1 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        day2 = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
        state = apply_trade(state, 500.0, day1)
        state = apply_trade(state, 500.0, day2)
        assert state.trading_days == 2

    def test_fails_on_daily_loss_violation(self):
        state = init_challenge(THE5ERS_HIGH_STAKES)  # max 4% daily loss
        # 5% loss in one day on $100k = -$5,000
        state = apply_trade(state, -5000.0)
        assert state.failed
        assert "Daily loss" in (state.fail_reason or "")

    def test_fails_on_total_drawdown(self):
        state = init_challenge(FTMO_STANDARD)  # max 10% drawdown, max 5% daily
        # $11,000 loss = 11% DD across 3 days to stay under daily loss limits
        day1 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        day2 = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
        day3 = datetime(2026, 1, 3, 10, 0, tzinfo=UTC)
        state = apply_trade(state, -4000.0, day1)   # 4% DD
        state = apply_trade(state, -3000.0, day2)   # 7% DD
        state = apply_trade(state, -4000.0, day3)   # 11% DD > 10% limit
        assert state.failed
        assert "drawdown" in (state.fail_reason or "").lower()

    def test_passes_on_target_met(self):
        state = init_challenge(FTMO_STANDARD)  # 10% target, min 10 days
        # Need +$10,000 on $100k over 10+ days, any number of trades
        for day in range(12):
            d = datetime(2026, 1, day + 1, 10, 0, tzinfo=UTC)
            state = apply_trade(state, 1000.0, d)
        assert state.passed
        assert not state.failed

    def test_no_change_after_pass(self):
        state = init_challenge(FTMO_STANDARD)
        for day in range(12):
            d = datetime(2026, 1, day + 1, 10, 0, tzinfo=UTC)
            state = apply_trade(state, 1000.0, d)
        assert state.passed
        # Further trades don't change pass/fail
        state = apply_trade(state, -5000.0)
        assert state.passed

    def test_no_change_after_fail(self):
        state = init_challenge(FTMO_STANDARD)
        state = apply_trade(state, -12000.0)
        assert state.failed
        state = apply_trade(state, 10000.0)
        assert state.failed


class TestSimulateFromTrades:
    """Simulating from a list of trade dicts."""

    def test_empty_trades(self):
        state = simulate_from_trades(FTMO_STANDARD, [])
        assert state.trades == 0
        assert not state.passed
        assert not state.failed

    def test_winning_streak(self):
        trades = [{"net_pnl": 500.0} for _ in range(5)]
        state = simulate_from_trades(FTMO_STANDARD, trades)
        assert state.trades == 5
        assert state.current_equity == 102500.0

    def test_stops_on_failure(self):
        trades = [{"net_pnl": -15000.0}, {"net_pnl": 50000.0}]
        state = simulate_from_trades(FTMO_STANDARD, trades)
        assert state.failed
        assert state.trades == 1  # stopped after first trade

    def test_pnl_from_net_pnl_fallback(self):
        trades = [{"pnl": 500.0}]
        state = simulate_from_trades(FTMO_STANDARD, trades)
        assert state.current_equity == 100500.0


class TestSimulateAll:
    """Running all challenges."""

    def test_all_challenges_simulated(self):
        results = simulate_all_challenges([])
        assert len(results) == 5
        for r in results:
            assert not r.passed
            assert not r.failed

    def test_ftmo_aggressive_allows_more_drawdown(self):
        from monitor.prop_firm_simulator import FTMO_AGGRESSIVE
        state = init_challenge(FTMO_AGGRESSIVE)
        # 15% DD = 15% of 100k = $15k, split across 2 days to avoid daily kill
        day1 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        day2 = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
        state = apply_trade(state, -8000.0, day1)
        state = apply_trade(state, -7000.0, day2)
        assert not state.failed  # 15% DD within 20% limit


class TestFormatReport:
    """Formatting results."""

    def test_contains_status(self):
        state = init_challenge(FTMO_STANDARD)
        text = format_challenge_result(state)
        assert "IN PROGRESS" in text
        assert "FTMO" in text

    def test_passed_shows_profit_share(self):
        state = init_challenge(FTMO_STANDARD)  # min 10 days, no min trades
        for day in range(12):
            d = datetime(2026, 1, day + 1, 10, 0, tzinfo=UTC)
            state = apply_trade(state, 1000.0, d)
        text = format_challenge_result(state)
        assert "PASSED" in text
        assert "Profit share" in text
