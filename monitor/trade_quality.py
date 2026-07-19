"""Trade quality scoring for AURUM-1.

Provides quantitative metrics for each trade beyond R-multiple:
  - Composite Trade Quality Score (0-100)
  - MAE/MFE analysis (adverse/favorable excursion vs risk)
  - Session profiling
  - Direction analysis

All functions accept a list of trade dicts (as produced by PaperBroker's
_trade_history) or rows from paper_trading.sqlite3.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TradeQualityMetrics:
    """Quality assessment for a single trade."""
    trade_id: int | str
    quality_score: float          # 0-100 composite
    entry_quality: float          # 0-100
    exit_quality: float           # 0-100
    r_efficiency: float           # actual R / 2.0 (since D4 targets 2R)
    duration_efficiency: float    # score based on how fast R was captured
    session: str                  # ASIAN / LONDON / NY / OVERLAP
    direction: str                # BUY / SELL
    r_multiple: float
    net_pnl: float
    exit_reason: str
    duration_hours: float
    mae_pct: float | None         # max adverse excursion as % of risk
    mfe_pct: float | None         # max favorable excursion as % of risk


@dataclass
class QualityReport:
    """Aggregate quality report across a set of trades."""
    n_trades: int
    avg_quality_score: float
    quality_by_direction: dict[str, float]
    quality_by_session: dict[str, float]
    quality_by_exit_reason: dict[str, float]
    best_trade: TradeQualityMetrics | None
    worst_trade: TradeQualityMetrics | None
    entries: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Session classification
# ---------------------------------------------------------------------------

def classify_session(timestamp: datetime) -> str:
    """Classify a UTC timestamp into XAU/USD market session.

    XAU/USD sessions (UTC):
      - ASIAN:     00:00-08:00 (widest spreads)
      - LONDON:    08:00-13:00
      - OVERLAP:   13:00-16:00 (tightest spreads)
      - NY:        16:00-22:00
      - CLOSED:    22:00-24:00
    """
    hour = timestamp.hour
    if 0 <= hour < 8:
        return "ASIAN"
    if 8 <= hour < 13:
        return "LONDON"
    if 13 <= hour < 16:
        return "OVERLAP"
    if 16 <= hour < 22:
        return "NY"
    return "CLOSED"


# ---------------------------------------------------------------------------
# Quality sub-scores
# ---------------------------------------------------------------------------

def _entry_quality(trade: dict[str, Any]) -> float:
    """Score entry execution quality (0-100).

    Deductions for:
      - Slippage: each pip of adverse slippage reduces score
      - Gap entry: if intended vs actual entry differ significantly
    """
    intended = float(trade.get("intended_entry", trade.get("entry", 0)))
    actual = float(trade.get("actual_entry", trade.get("entry", 0)))
    entry_slip = abs(actual - intended)

    # Perfect fill = 100; each pip of slip costs 20 points
    slip_penalty = min(entry_slip * 2000.0, 80.0)
    return max(0.0, 100.0 - slip_penalty)


def _exit_quality(trade: dict[str, Any]) -> float:
    """Score exit execution quality (0-100).

    TP hit = 100 (clean win)
    SL hit = 50 (clean loss, expected)
    SL gap = 0 (worst case: gapped past SL)
    Manual close = 75
    """
    reason = str(trade.get("reason", ""))
    if reason == "take_profit":
        base = 100.0
    elif reason == "stop_loss":
        base = 50.0
    elif reason == "stop_loss_gap":
        base = 0.0
    else:
        base = 75.0  # manual / other

    # Further deduct for exit slippage
    intended_exit = float(trade.get("intended_exit", trade.get("exit", 0)))
    actual_exit = float(trade.get("actual_exit", trade.get("exit", 0)))
    exit_slip = abs(actual_exit - intended_exit)
    slip_penalty = min(exit_slip * 2000.0, 50.0)

    return max(0.0, base - slip_penalty)


def _r_efficiency(trade: dict[str, Any]) -> float:
    """Score how close actual R came to target 2R (0-100).

    D4 aims for 2R. Scores:
      - Exactly 2R = 100
      - Exactly -1R = 50 (expected loss)
      - Better than 2R: bonus capped at 100
      - Worse than -1R: scales down toward 0
    """
    r = float(trade.get("r_multiple", trade.get("r", 0)))
    target_r = 2.0

    if r >= target_r:
        # Hit or exceeded target
        return min(100.0, 50.0 + (r / target_r) * 50.0)
    elif r > 0:
        # Partial win
        return (r / target_r) * 100.0
    elif r == 0:
        return 0.0
    else:
        # Loss: -1R is "expected loss" = 50
        expected_loss = -1.0
        return max(0.0, 50.0 + (r - expected_loss) / abs(expected_loss) * 50.0)


def _duration_efficiency(trade: dict[str, Any]) -> float:
    """Score how efficiently R was captured per hour (0-100).

    Fast winners = high score. Slow losses = moderate penalty.
    A 2R win in < 2 hours = 100. A 2R win in 24h = 50.
    """
    r = float(trade.get("r_multiple", trade.get("r", 0)))
    open_time = trade.get("open_time", "")
    closed_at = trade.get("closed_at", "")

    if not open_time or not closed_at:
        return 50.0

    try:
        ot = datetime.fromisoformat(str(open_time).replace("Z", "+00:00"))
        ct = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
        hours = max(0.001, (ct - ot).total_seconds() / 3600.0)
    except (ValueError, TypeError):
        return 50.0

    if r <= 0:
        # Loss: penalize slow losses (less efficient to tie up capital)
        return max(0.0, 100.0 - hours * 2.0)
    else:
        # Win: reward fast R capture
        r_per_hour = abs(r) / hours
        ideal = 2.0  # 2R in 1 hour = ideal
        score = min(100.0, (r_per_hour / ideal) * 100.0)
        return max(0.0, score)


def _mae_mfe(trade: dict[str, Any], ohlcv: Any = None) -> tuple[float | None, float | None]:
    """Compute MAE/MFE as percentages of risk.

    Without OHLC data per-trade, uses SL/TP distances as a proxy:
      MAE = risk_distance (how far price went against)
      MFE = min(R_target * risk_distance, actual excursion)

    With full OHLC, this would trace intra-trade price extremes.
    """
    risk_amount = float(trade.get("risk_amount", trade.get("risk_amt", 0)))
    if risk_amount <= 0:
        return None, None

    r = float(trade.get("r_multiple", trade.get("r", 0)))
    pnl = float(trade.get("net_pnl", trade.get("pnl", 0)))

    # MFE: best-case excursion as % of risk
    # For winners, MFE >= actual PnL (price went further in our favor before reversing)
    # Conservative estimate: assume MFE = 1.2x actual PnL for winners, 0 for losers
    if r > 0:
        mfe_pct = min(300.0, abs(pnl) / risk_amount * 120.0)
    else:
        mfe_pct = 0.0

    # MAE: worst-case adverse excursion as % of risk
    # Conservative: for losers it's at least the loss, for winners it happened intra-trade
    if r < 0:
        mae_pct = min(300.0, abs(pnl) / risk_amount * 100.0)
    else:
        # Winners still had adverse movement intra-trade
        # Estimate: 30-60% of risk as typical intra-trade adverse excursion
        mae_pct = 30.0 + (1.0 - min(1.0, abs(r) / 2.0)) * 30.0

    return round(mae_pct, 1), round(mfe_pct, 1)


# ---------------------------------------------------------------------------
# Per-trade quality scoring
# ---------------------------------------------------------------------------

def score_trade(trade: dict[str, Any]) -> TradeQualityMetrics:
    """Compute comprehensive quality metrics for one trade."""
    entry_q = _entry_quality(trade)
    exit_q = _exit_quality(trade)
    r_eff = _r_efficiency(trade)
    dur_eff = _duration_efficiency(trade)

    # Composite: weighted average
    composite = (
        entry_q * 0.15 +
        exit_q * 0.25 +
        r_eff * 0.40 +
        dur_eff * 0.20
    )

    r = float(trade.get("r_multiple", trade.get("r", 0)))
    pnl = float(trade.get("net_pnl", trade.get("pnl", 0)))
    reason = str(trade.get("reason", "unknown"))
    direction = str(trade.get("direction", "?"))

    # Duration
    open_time = trade.get("open_time", "")
    closed_at = trade.get("closed_at", "")
    try:
        ot = datetime.fromisoformat(str(open_time).replace("Z", "+00:00")) if open_time else None
        ct = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00")) if closed_at else None
        dur_h = (ct - ot).total_seconds() / 3600.0 if ot and ct else 0.0
    except (ValueError, TypeError):
        ot = None
        dur_h = 0.0

    session = classify_session(ot) if ot else "UNKNOWN"
    trade_id = trade.get("position_id", trade.get("id", 0))
    mae, mfe = _mae_mfe(trade)

    return TradeQualityMetrics(
        trade_id=trade_id,
        quality_score=round(composite, 1),
        entry_quality=round(entry_q, 1),
        exit_quality=round(exit_q, 1),
        r_efficiency=round(r_eff, 1),
        duration_efficiency=round(dur_eff, 1),
        session=session,
        direction=direction,
        r_multiple=round(r, 4),
        net_pnl=round(pnl, 2),
        exit_reason=reason,
        duration_hours=round(dur_h, 1),
        mae_pct=mae,
        mfe_pct=mfe,
    )


# ---------------------------------------------------------------------------
# Aggregate report
# ---------------------------------------------------------------------------

def generate_quality_report(trades: list[dict[str, Any]]) -> QualityReport:
    """Generate an aggregate quality report from a list of trades."""
    if not trades:
        return QualityReport(n_trades=0, avg_quality_score=0.0,
                             quality_by_direction={}, quality_by_session={},
                             quality_by_exit_reason={},
                             best_trade=None, worst_trade=None)

    scored = [score_trade(t) for t in trades]
    scores = [s.quality_score for s in scored]

    # By direction
    by_dir: dict[str, list[float]] = {}
    for s in scored:
        by_dir.setdefault(s.direction, []).append(s.quality_score)

    # By session
    by_sess: dict[str, list[float]] = {}
    for s in scored:
        by_sess.setdefault(s.session, []).append(s.quality_score)

    # By exit reason
    by_reason: dict[str, list[float]] = {}
    for s in scored:
        by_reason.setdefault(s.exit_reason, []).append(s.quality_score)

    # Best and worst
    best = max(scored, key=lambda s: s.quality_score)
    worst = min(scored, key=lambda s: s.quality_score)

    return QualityReport(
        n_trades=len(scored),
        avg_quality_score=round(sum(scores) / len(scores), 1),
        quality_by_direction={k: round(sum(v) / len(v), 1) for k, v in by_dir.items()},
        quality_by_session={k: round(sum(v) / len(v), 1) for k, v in by_sess.items()},
        quality_by_exit_reason={k: round(sum(v) / len(v), 1) for k, v in by_reason.items()},
        best_trade=best,
        worst_trade=worst,
        entries={
            "score_distribution": _score_buckets(scores),
            "avg_r": round(sum(s.r_multiple for s in scored) / len(scored), 4),
            "win_rate": round(sum(1 for s in scored if s.r_multiple > 0) / len(scored) * 100, 1),
            "avg_duration_hours": round(sum(s.duration_hours for s in scored) / len(scored), 1),
            "avg_mae_pct": round(sum(s.mae_pct for s in scored if s.mae_pct is not None) / max(sum(1 for s in scored if s.mae_pct is not None), 1), 1),
            "avg_mfe_pct": round(sum(s.mfe_pct for s in scored if s.mfe_pct is not None) / max(sum(1 for s in scored if s.mfe_pct is not None), 1), 1),
        }
    )


def _score_buckets(scores: list[float]) -> dict[str, int]:
    """Bucket quality scores into categories."""
    buckets: dict[str, int] = {
        "Excellent (80+)": 0,
        "Good (60-80)": 0,
        "Fair (40-60)": 0,
        "Poor (20-40)": 0,
        "Bad (<20)": 0,
    }
    for s in scores:
        if s >= 80:
            buckets["Excellent (80+)"] += 1
        elif s >= 60:
            buckets["Good (60-80)"] += 1
        elif s >= 40:
            buckets["Fair (40-60)"] += 1
        elif s >= 20:
            buckets["Poor (20-40)"] += 1
        else:
            buckets["Bad (<20)"] += 1
    return buckets


def format_quality_report(report: QualityReport) -> str:
    """Format a quality report as a printable string."""
    if report.n_trades == 0:
        return "No trades to analyze."

    lines = [
        f"{'=' * 70}",
        f"  TRADE QUALITY REPORT ({report.n_trades} trades)",
        f"{'=' * 70}",
        f"  Average Quality Score:   {report.avg_quality_score}/100",
        f"  Average R:               {report.entries['avg_r']:.4f}",
        f"  Win Rate:                {report.entries['win_rate']:.1f}%",
        f"  Average Duration:        {report.entries['avg_duration_hours']:.1f}h",
        f"  Avg MAE (adverse):       {report.entries['avg_mae_pct']:.1f}% of risk",
        f"  Avg MFE (favorable):     {report.entries['avg_mfe_pct']:.1f}% of risk",
        "",
        "  --- Score Distribution ---",
    ]
    for bucket, count in sorted(report.entries["score_distribution"].items()):
        bar = "#" * count
        lines.append(f"    {bucket:20s}: {count:2d}  {bar}")

    lines.extend(["", "  --- By Direction ---"])
    for direction, score in sorted(report.quality_by_direction.items()):
        lines.append(f"    {direction:6s}: {score:.1f}/100")

    lines.extend(["", "  --- By Session ---"])
    for session, score in sorted(report.quality_by_session.items()):
        lines.append(f"    {session:8s}: {score:.1f}/100")

    lines.extend(["", "  --- By Exit Reason ---"])
    for reason, score in sorted(report.quality_by_exit_reason.items()):
        lines.append(f"    {reason:15s}: {score:.1f}/100")

    if report.best_trade:
        b = report.best_trade
        lines.extend([
            "",
            f"  Best Trade:    #{b.trade_id}  {b.direction}  Score={b.quality_score}  R={b.r_multiple:+.4f}  Session={b.session}",
            f"    Entry={b.entry_quality}  Exit={b.exit_quality}  R-eff={b.r_efficiency}  Dur-eff={b.duration_efficiency}",
        ])

    if report.worst_trade:
        w = report.worst_trade
        lines.extend([
            "",
            f"  Worst Trade:   #{w.trade_id}  {w.direction}  Score={w.quality_score}  R={w.r_multiple:+.4f}  Session={w.session}",
            f"    Entry={w.entry_quality}  Exit={w.exit_quality}  R-eff={w.r_efficiency}  Dur-eff={w.duration_efficiency}",
        ])

    lines.append(f"{'=' * 70}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB adapter: load trades from paper_trading.sqlite3
# ---------------------------------------------------------------------------

def load_trades_from_db(db_path: str) -> list[dict[str, Any]]:
    """Load trade history from paper_trading.sqlite3 and convert to dict format."""
    import sqlite3
    from contextlib import closing

    trades: list[dict[str, Any]] = []
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("""
            SELECT entry_time, exit_time, direction, entry_price, exit_price,
                   stop_loss, take_profit, units, r_multiple, net_pnl,
                   spread_cost, slippage_cost, exit_reason
            FROM trades ORDER BY id
        """).fetchall()

        for row in rows:
            entry_t, exit_t, direction, entry_p, exit_p, sl, tp, units, r, pnl, spread, slip, reason = row
            trades.append({
                "direction": direction,
                "entry": float(entry_p or 0),
                "actual_entry": float(entry_p or 0),
                "exit": float(exit_p or 0),
                "actual_exit": float(exit_p or 0),
                "stop_loss": float(sl or 0),
                "take_profit": float(tp or 0),
                "units": float(units or 1),
                "r_multiple": float(r or 0),
                "r": float(r or 0),
                "net_pnl": float(pnl or 0),
                "pnl": float(pnl or 0),
                "spread_cost": float(spread or 0),
                "exit_slippage_cost": float(slip or 0),
                "total_slippage_cost": float(slip or 0),
                "risk_amount": abs(float(pnl or 0) / float(r or 1)) if r and float(r) != 0 else 0,
                "reason": str(reason or "unknown"),
                "open_time": str(entry_t or ""),
                "closed_at": str(exit_t or ""),
            })
    return trades


def print_quality_report_from_db(db_path: str) -> None:
    """Load trades from DB, generate quality report, and print it."""
    trades = load_trades_from_db(db_path)
    report = generate_quality_report(trades)
    print(format_quality_report(report))


__all__ = [
    "TradeQualityMetrics", "QualityReport",
    "score_trade", "generate_quality_report", "format_quality_report",
    "load_trades_from_db", "print_quality_report_from_db",
    "classify_session",
]
