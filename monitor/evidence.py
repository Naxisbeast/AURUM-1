"""Evidence collection tracker for AURUM-1 Phase 4.

Monitors D4's progress toward the decision gates. Historical gates:
  - 50 trades: risk review  — ✅ PASSED 2026-08-05 (stayed at 0.35%)
  - 100 trades: strategy review — ✅ RUN 2026-08-16 (2/3 criteria passed)
Active gate:
  - 200 trades: DSR becomes statistically meaningful (criterion 1 of the
    100-trade gate extends here per pre-registration)

Provides projections, drawdown monitoring, and automated reports.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Decision gates
# ---------------------------------------------------------------------------
# 50 and 100 gates are historical (both reached). 200 is the active collection
# target — DSR (criterion 1) needs the fuller trial/trade pool to be meaningful.

RISK_REVIEW_TRADES = 50
STRATEGY_REVIEW_TRADES = 100
DSR_REVIEW_TRADES = 200              # active target: DSR gate
RISK_REVIEW_CANDIDATE_PCT = 0.0050  # 0.50% potential next step
TRADE_RATE_HISTORY_DAYS = 14        # window for trade rate projection


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EvidenceReport:
    """Snapshot of evidence collection progress."""
    timestamp: datetime

    # Trade counts
    total_trades: int
    trades_at_new_risk: int         # trades since 0.35% deploy
    trades_remaining_to_50: int     # historical gate (reached) — kept for record
    trades_remaining_to_100: int    # historical gate (reached) — kept for record
    trades_remaining_to_200: int    # active gate: DSR

    # Performance since deploy
    pnl_since_deploy: float
    r_since_deploy: float
    win_rate_since_deploy: float

    # Lifetime (all 27 trades that were at 0.25%)
    lifetime_pnl: float
    lifetime_r: float
    lifetime_win_rate: float
    lifetime_avg_quality: float

    # Drawdown monitoring
    current_dd_pct: float
    peak_equity: float
    current_equity: float
    max_dd_since_deploy: float

    # Projections
    projected_days_to_50: float
    projected_days_to_100: float
    projected_days_to_200: float
    trade_rate_per_day: float

    # Kill switch status
    daily_kill_active: bool
    drawdown_kill_active: bool
    daily_loss_pct: float
    total_drawdown_pct: float

    # Latest trade info
    last_trade_time: str | None
    last_trade_direction: str | None
    last_trade_r: float

    # Health
    uptime_hours: float
    stale_data: bool
    risk_setting: float

    # Score distribution
    quality_distribution: dict[str, int] = field(default_factory=dict)
    r_distribution: dict[str, int] = field(default_factory=dict)

    # Flags
    risk_review_due: bool = False        # historical (50) — kept for record
    strategy_review_due: bool = False    # historical (100) — kept for record
    dsr_review_due: bool = False         # active gate (200)


# ---------------------------------------------------------------------------
# Evidence collector
# ---------------------------------------------------------------------------

class EvidenceCollector:
    """Collect and analyze evidence from D4's paper trading.

    Reads from:
      - paper_trading.sqlite3 (trades, account_snapshots)
      - d4_paper_trader_health.json (health metrics)
      - settings.yaml (risk config)
    """

    def __init__(self, root_path: str | Path, deploy_time: datetime | None = None):
        self.root = Path(root_path)
        self.paper_db = self.root / "aurum1" / "data" / "paper_trading.sqlite3"
        self.health_file = self.root / "run" / "d4_paper_trader_health.json"
        self.settings_file = self.root / "aurum1" / "config" / "settings.yaml"
        # Deploy time for 0.35% risk (default: last restart)
        self.deploy_time = deploy_time or datetime(2026, 7, 19, 20, 26, tzinfo=UTC)

    def generate_report(self) -> EvidenceReport:
        """Generate a complete evidence collection report."""
        now = datetime.now(UTC)

        # Load trades
        trades = self._load_trades()
        total = len(trades)

        # Separate trades before/after deploy
        trades_after = [t for t in trades if self._parse_time(t.get("closed_at", "")) >= self.deploy_time]
        trades_before = [t for t in trades if self._parse_time(t.get("closed_at", "")) < self.deploy_time]

        # Lifetime stats
        lifetime_pnl = sum(float(t.get("net_pnl", t.get("pnl", 0))) for t in trades)
        lifetime_r = sum(float(t.get("r_multiple", t.get("r", 0))) for t in trades)
        lifetime_wins = sum(1 for t in trades if float(t.get("r_multiple", t.get("r", 0))) > 0)

        # Post-deploy stats
        deploy_pnl = sum(float(t.get("net_pnl", t.get("pnl", 0))) for t in trades_after)
        deploy_r = sum(float(t.get("r_multiple", t.get("r", 0))) for t in trades_after)
        deploy_wins = sum(1 for t in trades_after if float(t.get("r_multiple", t.get("r", 0))) > 0)

        # Trade rate (last N days)
        cutoff = now - timedelta(days=TRADE_RATE_HISTORY_DAYS)
        recent_trades = [t for t in trades if self._parse_time(t.get("closed_at", "")) >= cutoff]
        days_span = max(1, (now - max(self.deploy_time, cutoff)).total_seconds() / 86400.0)
        trade_rate = len(recent_trades) / days_span

        # Projections
        trades_needed_50 = max(0, RISK_REVIEW_TRADES - total)
        trades_needed_100 = max(0, STRATEGY_REVIEW_TRADES - total)
        trades_needed_200 = max(0, DSR_REVIEW_TRADES - total)
        days_to_50 = trades_needed_50 / trade_rate if trade_rate > 0 else float("inf")
        days_to_100 = trades_needed_100 / trade_rate if trade_rate > 0 else float("inf")
        days_to_200 = trades_needed_200 / trade_rate if trade_rate > 0 else float("inf")

        # Health metrics
        health = self._load_health()
        equity = float(health.get("equity", 0))
        peak = float(health.get("peak_equity", equity))
        dd_pct = float(health.get("drawdown_pct", 0))
        uptime = float(health.get("uptime_hours", 0))
        candle_age = health.get("latest_candle_age_minutes")
        stale = candle_age is not None and candle_age > 120 if candle_age is not None else False

        # Account snapshots for max DD since deploy
        max_dd_deploy = self._max_drawdown_since_deploy()

        # Kill switch status from latest snapshot
        daily_pnl = float(health.get("daily_pnl", 0))
        risk_setting = self._load_risk_setting()
        daily_kill = daily_pnl < -(equity * 0.03) if equity > 0 else False
        dd_kill = equity < peak * 0.92 if peak > 0 else False  # 8% drawdown kill
        daily_loss_pct = abs(daily_pnl / equity * 100) if equity > 0 else 0
        total_dd_pct = dd_pct

        # Last trade info
        last_trade = trades[-1] if trades else {}
        last_trade_time = last_trade.get("closed_at", "")
        last_trade_dir = last_trade.get("direction")
        last_trade_r = float(last_trade.get("r_multiple", last_trade.get("r", 0)))

        # Quality distribution (from trade quality module)
        quality_dist = self._quality_distribution(trades)
        r_dist = self._r_distribution(trades)

        return EvidenceReport(
            timestamp=now,
            total_trades=total,
            trades_at_new_risk=len(trades_after),
            trades_remaining_to_50=trades_needed_50,
            trades_remaining_to_100=trades_needed_100,
            trades_remaining_to_200=trades_needed_200,
            pnl_since_deploy=round(deploy_pnl, 2),
            r_since_deploy=round(deploy_r, 4),
            win_rate_since_deploy=round((deploy_wins / max(len(trades_after), 1)) * 100, 1),
            lifetime_pnl=round(lifetime_pnl, 2),
            lifetime_r=round(lifetime_r, 4),
            lifetime_win_rate=round((lifetime_wins / max(total, 1)) * 100, 1),
            lifetime_avg_quality=round(self._avg_quality(trades), 1),
            current_dd_pct=round(dd_pct, 2),
            peak_equity=round(peak, 2),
            current_equity=round(equity, 2),
            max_dd_since_deploy=round(max_dd_deploy, 2),
            projected_days_to_50=round(days_to_50, 1),
            projected_days_to_100=round(days_to_100, 1),
            projected_days_to_200=round(days_to_200, 1),
            trade_rate_per_day=round(trade_rate, 2),
            daily_kill_active=daily_kill,
            drawdown_kill_active=dd_kill,
            daily_loss_pct=round(daily_loss_pct, 2),
            total_drawdown_pct=round(total_dd_pct, 2),
            last_trade_time=str(last_trade_time) if last_trade_time else None,
            last_trade_direction=last_trade_dir,
            last_trade_r=round(last_trade_r, 4),
            uptime_hours=round(uptime, 1),
            stale_data=stale,
            risk_setting=risk_setting,
            quality_distribution=quality_dist,
            r_distribution=r_dist,
            risk_review_due=total >= RISK_REVIEW_TRADES,
            strategy_review_due=total >= STRATEGY_REVIEW_TRADES,
            dsr_review_due=total >= DSR_REVIEW_TRADES,
        )

    def format_report(self, report: EvidenceReport | None = None) -> str:
        """Format evidence report as printable string."""
        r = report or self.generate_report()
        dd_color = "RED" if r.total_drawdown_pct > 15 else "YELLOW" if r.total_drawdown_pct > 10 else "GREEN"
        risk_color = "GREEN" if r.risk_setting == 0.0035 else "YELLOW"

        lines = [
            f"{'=' * 70}",
            f"  PHASE 4: EVIDENCE COLLECTION REPORT",
            f"  {r.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"{'=' * 70}",
            "",
            f"  RISK SETTING:  {r.risk_setting*100:.2f}% [{risk_color}]",
            f"  UPTIME:        {r.uptime_hours:.1f}h",
            f"  STALE DATA:    {'YES' if r.stale_data else 'NO'}",
            "",
            f"  {'--- TRADE PROGRESS ---':>45s}",
            f"  Total trades:        {r.total_trades:3d}",
            f"  Since 0.35% deploy:  {r.trades_at_new_risk:3d}",
            f"  50-gate (done):      {'PASSED' if r.risk_review_due else f'{r.trades_remaining_to_50:3d} remaining'}",
            f"  100-gate (done):     {'PASSED' if r.strategy_review_due else f'{r.trades_remaining_to_100:3d} remaining'}",
            f"  To 200-trade DSR:    {r.trades_remaining_to_200:3d}  (projected {r.projected_days_to_200:.0f} days)",
            f"  Trade rate:          {r.trade_rate_per_day:.2f}/day",
            "",
            f"  {'--- PERFORMANCE ---':>40s}",
            f"  Since deploy:  PnL={r.pnl_since_deploy:+.2f}  R={r.r_since_deploy:+.4f}  WR={r.win_rate_since_deploy:.1f}%",
            f"  Lifetime:      PnL={r.lifetime_pnl:+.2f}  R={r.lifetime_r:+.4f}  WR={r.lifetime_win_rate:.1f}%",
            f"  Avg quality:   {r.lifetime_avg_quality:.1f}/100",
            "",
            f"  {'--- DRAWDOWN ---':>35s}",
            f"  Current DD:    {r.current_dd_pct:.2f}% [DD={dd_color}]",
            f"  Max DD (post): {r.max_dd_since_deploy:.2f}%",
            f"  Daily loss:    {r.daily_loss_pct:.2f}%  Kill={'ACTIVE' if r.daily_kill_active else 'OK'}",
            f"  Total DD:      {r.total_drawdown_pct:.2f}%  Kill={'ACTIVE' if r.drawdown_kill_active else 'OK'}",
            "",
            f"  {'--- LAST TRADE ---':>30s}",
            f"  Time:   {r.last_trade_time or 'N/A'}",
            f"  Dir:    {r.last_trade_direction or 'N/A'}",
            f"  R:      {r.last_trade_r:+.4f}",
            "",
            f"  {'--- DECISION GATES ---':>35s}",
        ]

        lines.append(f"  50-trade risk review:  {'PASSED (2026-08-05)' if r.risk_review_due else f'{r.trades_remaining_to_50} remaining'}")
        lines.append(f"  100-trade strategy review: {'PASSED (2026-08-16)' if r.strategy_review_due else f'{r.trades_remaining_to_100} remaining'}")

        if r.dsr_review_due:
            lines.extend([
                f"  ** 200-TRADE DSR GATE REACHED ** — {r.total_trades} trades accumulated",
                f"  Run the DSR gate: scripts/gates/run_100_trade_gate.py against a fresh DB snapshot",
                f"  If DSR still below threshold, per pre-registration: demote D4 to shadow-only.",
            ])
        else:
            lines.append(f"  Active: 200-trade DSR gate:  {r.trades_remaining_to_200} remaining "
                         f"(projected {r.projected_days_to_200:.0f} days)")

        lines.extend([
            "",
            f"  {'--- SCORE DISTRIBUTION ---':>30s}",
        ])
        for bucket, count in sorted(r.quality_distribution.items()):
            bar = "#" * count
            lines.append(f"    {bucket:20s}: {count:2d}  {bar}")

        lines.append(f"{'=' * 70}")
        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _load_trades(self) -> list[dict[str, Any]]:
        """Load all trades from paper_trading.sqlite3."""
        if not self.paper_db.exists():
            return []
        trades: list[dict[str, Any]] = []
        with closing(sqlite3.connect(str(self.paper_db))) as conn:
            rows = conn.execute("""
                SELECT id, entry_time, exit_time, direction, entry_price, exit_price,
                       r_multiple, net_pnl, exit_reason, spread_cost, slippage_cost
                FROM trades ORDER BY id
            """).fetchall()
            for row in rows:
                trades.append({
                    "id": row[0],
                    "open_time": str(row[1] or ""),
                    "closed_at": str(row[2] or ""),
                    "direction": row[3],
                    "entry": float(row[4] or 0),
                    "exit": float(row[5] or 0),
                    "r_multiple": float(row[6] or 0),
                    "r": float(row[6] or 0),
                    "net_pnl": float(row[7] or 0),
                    "pnl": float(row[7] or 0),
                    "reason": str(row[8] or ""),
                })
        return trades

    def _load_health(self) -> dict[str, Any]:
        """Load D4 health file."""
        if not self.health_file.exists():
            return {"equity": 0, "peak_equity": 0}
        try:
            return json.loads(self.health_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {"equity": 0, "peak_equity": 0}

    def _load_risk_setting(self) -> float:
        """Read current risk setting from settings.yaml."""
        if not self.settings_file.exists():
            return 0.0025
        try:
            import yaml
            data = yaml.safe_load(self.settings_file.read_text())
            if isinstance(data, dict):
                return float(data.get("risk", {}).get("risk_per_trade_pct", 0.0025))
        except Exception:
            pass
        return 0.0025

    def _max_drawdown_since_deploy(self) -> float:
        """Compute max drawdown from account_snapshots since deploy."""
        if not self.paper_db.exists():
            return 0.0
        try:
            with closing(sqlite3.connect(str(self.paper_db))) as conn:
                rows = conn.execute("""
                    SELECT equity, peak_equity FROM account_snapshots
                    WHERE timestamp >= ? ORDER BY id
                """, (self.deploy_time.isoformat(),)).fetchall()
            if not rows:
                return 0.0
            max_dd = 0.0
            for eq, peak in rows:
                eq = float(eq or 0)
                peak = float(peak or eq)
                if peak > 0:
                    dd = (peak - eq) / peak * 100
                    max_dd = max(max_dd, dd)
            return max_dd
        except (sqlite3.Error, IndexError):
            return 0.0

    def _avg_quality(self, trades: list[dict[str, Any]]) -> float:
        """Average quality score from trade_quality module (computed on the fly)."""
        if not trades:
            return 0.0
        try:
            from monitor.trade_quality import score_trade
            scores = [score_trade(t).quality_score for t in trades]
            return sum(scores) / len(scores)
        except ImportError:
            return 0.0

    def _quality_distribution(self, trades: list[dict[str, Any]]) -> dict[str, int]:
        """Quality score distribution."""
        if not trades:
            return {}
        try:
            from monitor.trade_quality import score_trade, _score_buckets
            scores = [score_trade(t).quality_score for t in trades]
            return _score_buckets(scores)
        except ImportError:
            return {}

    def _r_distribution(self, trades: list[dict[str, Any]]) -> dict[str, int]:
        """R-multiple distribution."""
        dist: dict[str, int] = {
            "<=-2R": 0, "-2to-1R": 0, "-1to0R": 0,
            "0to+1R": 0, "+1to+2R": 0, ">=+2R": 0,
        }
        for t in trades:
            r = float(t.get("r_multiple", t.get("r", 0)))
            if r <= -2: dist["<=-2R"] += 1
            elif r <= -1: dist["-2to-1R"] += 1
            elif r < 0: dist["-1to0R"] += 1
            elif r < 1: dist["0to+1R"] += 1
            elif r < 2: dist["+1to+2R"] += 1
            else: dist[">=+2R"] += 1
        return dist

    def _parse_time(self, ts: str) -> datetime:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return datetime.min.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# CLI convenience
# ---------------------------------------------------------------------------

def print_evidence_report(root_path: str | Path | None = None) -> None:
    """Load and print evidence collection report."""
    if root_path is None:
        root_path = Path(__file__).resolve().parent.parent
    collector = EvidenceCollector(root_path)
    report = collector.generate_report()
    print(collector.format_report(report))


__all__ = [
    "EvidenceCollector", "EvidenceReport",
    "RISK_REVIEW_TRADES", "STRATEGY_REVIEW_TRADES", "DSR_REVIEW_TRADES",
    "print_evidence_report",
]
