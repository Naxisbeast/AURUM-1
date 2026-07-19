"""Prop firm challenge simulator for AURUM-1.

Tracks D4's paper trading performance against challenge rules from:
  - FTMO (standard + aggressive)
  - The5ers (high-stakes + bootcamp)
  - FundingPips (standard)

Each challenge has rules for:
  - Profit target (%)
  - Max daily loss (%)
  - Max total drawdown (%)
  - Minimum trading days
  - Minimum trades
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


# ---------------------------------------------------------------------------
# Challenge definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PropFirmChallenge:
    """Specification for a prop firm challenge."""
    name: str
    provider: str
    account_size: float       # starting equity ($)
    profit_target_pct: float  # e.g. 10.0 = 10%
    max_daily_loss_pct: float
    max_drawdown_pct: float   # relative to starting equity
    min_trading_days: int
    min_trades: int
    profit_split: float       # e.g. 0.80 = 80/20 split
    max_position_size_pct: float | None = None  # None = no limit


# Standard challenges
FTMO_STANDARD = PropFirmChallenge(
    name="FTMO Standard",
    provider="FTMO",
    account_size=100000.0,
    profit_target_pct=10.0,
    max_daily_loss_pct=5.0,
    max_drawdown_pct=10.0,
    min_trading_days=10,
    min_trades=0,
    profit_split=0.80,
)

FTMO_AGGRESSIVE = PropFirmChallenge(
    name="FTMO Aggressive",
    provider="FTMO",
    account_size=100000.0,
    profit_target_pct=20.0,
    max_daily_loss_pct=10.0,
    max_drawdown_pct=20.0,
    min_trading_days=10,
    min_trades=0,
    profit_split=0.80,
)

THE5ERS_HIGH_STAKES = PropFirmChallenge(
    name="The5ers High Stakes",
    provider="The5ers",
    account_size=100000.0,
    profit_target_pct=8.0,
    max_daily_loss_pct=4.0,
    max_drawdown_pct=8.0,
    min_trading_days=5,
    min_trades=20,
    profit_split=0.80,
)

THE5ERS_BOOTCAMP = PropFirmChallenge(
    name="The5ers Bootcamp",
    provider="The5ers",
    account_size=50000.0,
    profit_target_pct=10.0,
    max_daily_loss_pct=3.0,
    max_drawdown_pct=6.0,
    min_trading_days=10,
    min_trades=0,
    profit_split=0.50,
)

FUNDING_PIPS_STANDARD = PropFirmChallenge(
    name="FundingPips Standard",
    provider="FundingPips",
    account_size=100000.0,
    profit_target_pct=8.0,
    max_daily_loss_pct=4.0,
    max_drawdown_pct=8.0,
    min_trading_days=5,
    min_trades=0,
    profit_split=0.80,
    max_position_size_pct=2.0,
)

ALL_CHALLENGES: list[PropFirmChallenge] = [
    FTMO_STANDARD,
    FTMO_AGGRESSIVE,
    THE5ERS_HIGH_STAKES,
    THE5ERS_BOOTCAMP,
    FUNDING_PIPS_STANDARD,
]


# ---------------------------------------------------------------------------
# Simulation state
# ---------------------------------------------------------------------------

@dataclass
class ChallengeState:
    """Current state of a challenge simulation."""
    challenge: PropFirmChallenge
    current_equity: float
    peak_equity: float
    daily_start_equity: float
    trades: int = 0
    trading_days: int = 0
    current_streak: int = 0
    best_streak: int = 0
    passed: bool = False
    failed: bool = False
    fail_reason: str | None = None
    daily_pnl: float = 0.0
    _last_trade_date: datetime | None = None

    @property
    def total_pnl(self) -> float:
        return self.current_equity - self.challenge.account_size

    @property
    def total_return_pct(self) -> float:
        return (self.total_pnl / self.challenge.account_size) * 100.0

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.current_equity) / self.peak_equity * 100.0

    @property
    def daily_loss_pct(self) -> float:
        if self.daily_start_equity <= 0:
            return 0.0
        return (self.daily_start_equity - self.current_equity) / self.daily_start_equity * 100.0

    @property
    def profit_target_reached(self) -> bool:
        return self.total_return_pct >= self.challenge.profit_target_pct

    @property
    def daily_loss_violated(self) -> bool:
        return self.daily_loss_pct > self.challenge.max_daily_loss_pct

    @property
    def drawdown_violated(self) -> bool:
        return self.drawdown_pct > self.challenge.max_drawdown_pct

    @property
    def profit_if_passed(self) -> float:
        """Projected profit if challenge is passed (account funded)."""
        if not self.passed:
            return 0.0
        profit_share = self.total_pnl * self.challenge.profit_split
        return profit_share

    @property
    def progress_pct(self) -> float:
        """Progress toward profit target (0-100%)."""
        return min(100.0, (self.total_return_pct / self.challenge.profit_target_pct) * 100.0)


# ---------------------------------------------------------------------------
# Simulation logic
# ---------------------------------------------------------------------------

def init_challenge(challenge: PropFirmChallenge) -> ChallengeState:
    """Initialise a new challenge simulation."""
    initial = challenge.account_size
    return ChallengeState(
        challenge=challenge,
        current_equity=initial,
        peak_equity=initial,
        daily_start_equity=initial,
    )


def apply_trade(
    state: ChallengeState,
    pnl: float,
    trade_time: datetime | None = None,
) -> ChallengeState:
    """Apply a trade result to the challenge state and return updated state.

    Returns the updated state. The caller should check state.passed or
    state.failed after each trade.
    """
    if state.passed or state.failed:
        return state

    now = trade_time or datetime.now(UTC)

    # Track trading days (reset daily loss at start of each day)
    if state._last_trade_date is None or now.date() != state._last_trade_date.date():
        state.trading_days += 1
        state.daily_start_equity = state.current_equity
        state.daily_pnl = 0.0
    state._last_trade_date = now

    # Update equity
    state.current_equity += float(pnl)
    state.daily_pnl += float(pnl)
    state.trades += 1

    # Track peak
    if state.current_equity > state.peak_equity:
        state.peak_equity = state.current_equity

    # Check failure conditions
    if state.daily_loss_violated:
        state.failed = True
        state.fail_reason = (
            f"Daily loss exceeded: {state.daily_loss_pct:.2f}% "
            f"(max {state.challenge.max_daily_loss_pct}%)"
        )
        return state

    if state.drawdown_violated:
        state.failed = True
        state.fail_reason = (
            f"Max drawdown exceeded: {state.drawdown_pct:.2f}% "
            f"(max {state.challenge.max_drawdown_pct}%)"
        )
        return state

    # Check pass conditions
    days_ok = state.trading_days >= state.challenge.min_trading_days
    trades_ok = state.trades >= state.challenge.min_trades

    if state.profit_target_reached and days_ok and trades_ok:
        state.passed = True

    return state


def simulate_from_trades(
    challenge: PropFirmChallenge,
    trades: list[dict[str, Any]],
) -> ChallengeState:
    """Run a full challenge simulation from a list of trade dicts.

    Each trade dict must have at minimum:
      - 'pnl' or 'net_pnl' (float)
      - 'closed_at' or 'exit_time' (ISO datetime string, optional)
    """
    state = init_challenge(challenge)
    for t in trades:
        pnl = float(t.get("net_pnl", t.get("pnl", 0)))
        closed = t.get("closed_at", t.get("exit_time", ""))
        trade_time = None
        if closed:
            try:
                trade_time = datetime.fromisoformat(str(closed).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        state = apply_trade(state, pnl, trade_time)
        if state.failed:
            break
    return state


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def format_challenge_result(state: ChallengeState) -> str:
    """Format a single challenge result as a string."""
    c = state.challenge
    status = "PASSED" if state.passed else "FAILED" if state.failed else "IN PROGRESS"
    lines = [
        f'{"=" * 60}',
        f"  {c.provider} -- {c.name}  [{status}]",
        f'{"=" * 60}',
        f"  Account:       ${c.account_size:,.0f}",
        f"  Equity:        ${state.current_equity:,.2f}",
        f"  Return:        {state.total_return_pct:+.2f}%  (target: {c.profit_target_pct}%)",
        f"  Progress:      {state.progress_pct:.1f}%",
        f"  Max DD:        {state.drawdown_pct:.2f}%  (limit: {c.max_drawdown_pct}%)",
        f"  Worst daily:   {state.daily_loss_pct:.2f}%  (limit: {c.max_daily_loss_pct}%)",
        f"  Trades:        {state.trades}  (min: {c.min_trades})",
        f"  Trading days:  {state.trading_days}  (min: {c.min_trading_days})",
    ]
    if state.failed and state.fail_reason:
        lines.append(f"  Fail reason:   {state.fail_reason}")
    if state.passed:
        lines.append(f"  Profit share:  ${state.profit_if_passed:,.2f}")
    lines.append(f"{'=' * 60}")
    return "\n".join(lines)


def simulate_all_challenges(trades: list[dict[str, Any]]) -> list[ChallengeState]:
    """Run all standard challenges against a trade list."""
    results: list[ChallengeState] = []
    for challenge in ALL_CHALLENGES:
        state = simulate_from_trades(challenge, trades)
        results.append(state)
    return results


def print_all_challenge_results(trades: list[dict[str, Any]]) -> None:
    """Simulate and print results for all challenges."""
    for state in simulate_all_challenges(trades):
        print()
        print(format_challenge_result(state))


# ---------------------------------------------------------------------------
# DB adapter
# ---------------------------------------------------------------------------

def load_trades_from_db(db_path: str) -> list[dict[str, Any]]:
    """Load trade history from paper_trading.sqlite3 for challenge sim."""
    import sqlite3
    from contextlib import closing

    trades: list[dict[str, Any]] = []
    with closing(sqlite3.connect(str(db_path))) as conn:
        rows = conn.execute("""
            SELECT net_pnl, exit_time
            FROM trades
            WHERE exit_time IS NOT NULL AND exit_time != ''
            ORDER BY id
        """).fetchall()

        for row in rows:
            pnl, exit_time = row
            trades.append({
                "net_pnl": float(pnl or 0),
                "closed_at": str(exit_time or ""),
            })
    return trades


__all__ = [
    "PropFirmChallenge", "ChallengeState",
    "FTMO_STANDARD", "FTMO_AGGRESSIVE",
    "THE5ERS_HIGH_STAKES", "THE5ERS_BOOTCAMP",
    "FUNDING_PIPS_STANDARD", "ALL_CHALLENGES",
    "init_challenge", "apply_trade", "simulate_from_trades",
    "simulate_all_challenges", "format_challenge_result",
    "print_all_challenge_results", "load_trades_from_db",
]
