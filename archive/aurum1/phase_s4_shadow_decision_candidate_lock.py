"""Phase S4 shadow decision candidate lock for AURUM-1.

This module creates a diagnostic-only candidate decision report. It compares
the current raw Donchian shadow behavior against fixed candidate TAKE/HOLD
rules from Phase S3 and locks one candidate for further shadow observation.
It does not modify execution, timers, strategy thresholds, SELL execution, or
the current AURUM service.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from aurum1.reports.phase_s1_forward_shadow_failure_audit import (
    DEFAULT_REPORT_DIR,
    DEFAULT_SHADOW_DB,
    load_shadow_data,
    normalize_signals,
    normalize_trades,
    safe_float,
    write_csv,
)
from aurum1.reports.phase_s3_candidate_filter_shadow_replay import run_phase_s3_replay


ROOT = Path(__file__).resolve().parents[2]
MIN_LOCK_SAMPLE = 20

CANDIDATES = [
    {
        "candidate_name": "D1",
        "s3_variant": "VOL_NOT_HIGH_AND_NOT_LONDON_FIXED_1R",
        "description": "BUY only; TAKE if volatility_regime != high and session != london; fixed_1R.",
        "filter_rule": "direction == BUY AND volatility_regime != high AND session != london",
        "exit_model": "fixed_1r",
    },
    {
        "candidate_name": "D2",
        "s3_variant": "VOL_NOT_HIGH_AND_NOT_LONDON_FIXED_2R",
        "description": "BUY only; TAKE if volatility_regime != high and session != london; fixed_2R comparison.",
        "filter_rule": "direction == BUY AND volatility_regime != high AND session != london",
        "exit_model": "fixed_2r",
    },
    {
        "candidate_name": "D3",
        "s3_variant": "NOT_LONDON_FIXED_1R",
        "description": "BUY only; TAKE if session != london; fixed_1R broader comparison.",
        "filter_rule": "direction == BUY AND session != london",
        "exit_model": "fixed_1r",
    },
    {
        "candidate_name": "D4",
        "s3_variant": "NORMAL_AND_NOT_LONDON_FIXED_1R",
        "description": "BUY only; TAKE if volatility_regime == normal and session != london; fixed_1R stricter comparison.",
        "filter_rule": "direction == BUY AND volatility_regime in normal/medium AND session != london",
        "exit_model": "fixed_1r",
    },
]

LOCK_DECISIONS = {
    "LOCK_SHADOW_CANDIDATE_D1_SAMPLE_LIMITED",
    "LOCK_SHADOW_CANDIDATE_D2_SAMPLE_LIMITED",
    "LOCK_SHADOW_CANDIDATE_D3_SAMPLE_LIMITED",
    "LOCK_SHADOW_CANDIDATE_D4_SAMPLE_LIMITED",
    "NEED_MORE_FORWARD_SAMPLE",
    "NO_CANDIDATE_LOCKED",
    "INVESTIGATE_SHORT_SIDE",
}

DECISION_FIELDS = [
    "timestamp",
    "instrument",
    "direction",
    "raw_signal_id",
    "baseline_current_decision",
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

METRIC_FIELDS = [
    "candidate_name",
    "description",
    "filter_rule",
    "exit_model",
    "raw_signal_count",
    "take_count",
    "hold_count",
    "trade_retention_percent",
    "win_rate",
    "avg_r",
    "median_r",
    "net_r",
    "profit_factor",
    "max_drawdown_r",
    "max_consecutive_losses",
    "removed_losers_vs_baseline",
    "removed_winners_vs_baseline",
    "net_filter_improvement_r_vs_baseline",
    "lock_score",
    "sample_limited",
]

DIRECTION_FIELDS = [
    "metric",
    "buy_count",
    "sell_count",
    "unknown_count",
    "assessment",
    "config_direction",
    "config_strategy",
    "strategy_implies_long_only",
    "sell_generation_status",
    "notes",
]

DRAWDOWN_FIELDS = [
    "section",
    "candidate_name",
    "rank",
    "raw_signal_id",
    "timestamp",
    "session",
    "weekday",
    "volatility_regime",
    "simulated_r",
    "cumulative_r",
    "drawdown_r",
    "max_drawdown_r",
    "max_consecutive_losses",
    "note",
]


def run_phase_s4_lock(
    shadow_db: Path | str = DEFAULT_SHADOW_DB,
    report_dir: Path | str = DEFAULT_REPORT_DIR,
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Run Phase S4 and write all requested artifacts."""

    db_path = resolve_path(shadow_db)
    output_dir = resolve_path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ensure_s3_inputs(db_path, output_dir)

    s3_decisions = load_audit_csv(output_dir / "phase_s3_replay_decisions.csv")
    s3_metrics = load_audit_csv(output_dir / "phase_s3_variant_metrics.csv")
    s3_direction = load_audit_csv(output_dir / "phase_s3_direction_audit.csv")
    shadow = load_shadow_data(db_path)
    signals = normalize_signals(shadow["signals"])
    trades = normalize_trades(shadow["trades"])
    config = shadow["config"]

    candidate_decisions = build_candidate_decisions(s3_decisions)
    candidate_metrics = build_candidate_metrics(candidate_decisions, s3_decisions, s3_metrics)
    direction_rows, direction_summary = build_direction_investigation(signals, trades, s3_direction, config)
    locked, decision, reason, warnings = choose_lock_candidate(candidate_metrics, direction_summary)
    drawdown_rows = build_drawdown_attribution(candidate_decisions, candidate_metrics, locked)

    summary = {
        "generated_at": utc_now_or_as_of(as_of),
        "phase": "S4",
        "name": "Shadow Decision Candidate Lock",
        "classification": "research-only",
        "shadow_db": str(db_path),
        "report_dir": str(output_dir),
        "locked_shadow_candidate": locked,
        "candidate_lock_decision": decision,
        "research_decision": decision,
        "research_decision_reason": reason,
        "warnings": warnings,
        "direction_investigation": direction_summary,
        "candidate_rules": CANDIDATES,
        "safety": {
            "orders_placed": False,
            "execution_logic_modified": False,
            "live_or_paper_behavior_modified": False,
            "strategy_thresholds_modified": False,
            "timers_modified": False,
            "sell_execution_enabled": False,
            "forward_shadow_runner_modified": False,
            "current_aurum_service_modified": False,
            "sqlite_read_mode": "query_only",
        },
    }

    paths = {
        "summary_json": output_dir / "phase_s4_shadow_candidate_summary.json",
        "candidate_decisions_csv": output_dir / "phase_s4_candidate_decisions.csv",
        "candidate_metrics_csv": output_dir / "phase_s4_candidate_metrics.csv",
        "direction_investigation_csv": output_dir / "phase_s4_direction_investigation.csv",
        "drawdown_attribution_csv": output_dir / "phase_s4_drawdown_attribution.csv",
    }
    paths["summary_json"].write_text(json.dumps(summary, indent=2, sort_keys=True, default=json_default), encoding="utf-8")
    write_csv(paths["candidate_decisions_csv"], candidate_decisions, DECISION_FIELDS)
    write_csv(paths["candidate_metrics_csv"], candidate_metrics, METRIC_FIELDS)
    write_csv(paths["direction_investigation_csv"], direction_rows, DIRECTION_FIELDS)
    write_csv(paths["drawdown_attribution_csv"], drawdown_rows, DRAWDOWN_FIELDS)

    return {
        "summary": summary,
        "candidate_decisions": candidate_decisions,
        "candidate_metrics": candidate_metrics,
        "direction_investigation": direction_rows,
        "drawdown_attribution": drawdown_rows,
        "paths": {key: str(value) for key, value in paths.items()},
    }


def resolve_path(path: Path | str) -> Path:
    candidate = Path(path)
    return ROOT / candidate if not candidate.is_absolute() else candidate


def ensure_s3_inputs(shadow_db: Path, report_dir: Path) -> None:
    required = [
        report_dir / "phase_s3_replay_decisions.csv",
        report_dir / "phase_s3_variant_metrics.csv",
        report_dir / "phase_s3_direction_audit.csv",
    ]
    if all(path.exists() for path in required):
        return
    run_phase_s3_replay(shadow_db, report_dir)


def load_audit_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def build_candidate_decisions(s3_decisions: pd.DataFrame) -> list[dict[str, Any]]:
    if s3_decisions.empty:
        return []
    baseline = s3_decisions[s3_decisions["variant"].astype(str).eq("BASELINE_CURRENT")].copy()
    baseline_decision = {
        str(row["raw_signal_id"]): str(row.get("decision") or "HOLD")
        for _, row in baseline.iterrows()
    }
    rows: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        subset = s3_decisions[s3_decisions["variant"].astype(str).eq(candidate["s3_variant"])].copy()
        for _, row in subset.iterrows():
            direction = str(row.get("direction", "")).upper()
            original_decision = str(row.get("decision", ""))
            if direction != "BUY":
                candidate_decision = "HOLD"
                blocked_reason = "non_buy_direction"
            else:
                candidate_decision = original_decision
                blocked_reason = row.get("blocked_reason", "")
            rows.append(
                {
                    "timestamp": row.get("timestamp", ""),
                    "instrument": row.get("instrument", ""),
                    "direction": direction,
                    "raw_signal_id": row.get("raw_signal_id", ""),
                    "baseline_current_decision": baseline_decision.get(str(row.get("raw_signal_id", "")), ""),
                    "candidate_name": candidate["candidate_name"],
                    "candidate_decision": candidate_decision,
                    "blocked_reason": blocked_reason,
                    "volatility_regime": row.get("volatility_regime", ""),
                    "session": row.get("session", ""),
                    "weekday": row.get("weekday", ""),
                    "exit_model": candidate["exit_model"],
                    "simulated_r": row.get("simulated_r", ""),
                    "simulated_outcome": row.get("simulated_outcome", ""),
                    "bars_held": row.get("bars_held", ""),
                }
            )
    return rows


def build_candidate_metrics(candidate_decisions: list[dict[str, Any]], s3_decisions: pd.DataFrame, s3_metrics: pd.DataFrame) -> list[dict[str, Any]]:
    if s3_metrics.empty or not candidate_decisions:
        return []
    baseline_take = s3_decisions[
        s3_decisions["variant"].astype(str).eq("BASELINE_CURRENT")
        & s3_decisions["decision"].astype(str).eq("TAKE")
    ].copy()
    baseline_take_ids = {str(row.get("raw_signal_id")) for _, row in baseline_take.iterrows()}
    baseline_r_by_id = {
        str(row.get("raw_signal_id")): safe_float(row.get("simulated_r"))
        for _, row in baseline_take.iterrows()
    }
    baseline_net_r = sum(value for value in baseline_r_by_id.values() if value is not None)
    baseline_loser_ids = {signal_id for signal_id, value in baseline_r_by_id.items() if value is not None and value <= 0.0}
    baseline_winner_ids = {signal_id for signal_id, value in baseline_r_by_id.items() if value is not None and value > 0.0}

    rows: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        subset = [row for row in candidate_decisions if row["candidate_name"] == candidate["candidate_name"]]
        if not subset:
            row = empty_metric(candidate)
        else:
            take_rows = [item for item in subset if item["candidate_decision"] == "TAKE"]
            r_values = [value for value in (safe_float(item.get("simulated_r")) for item in take_rows) if value is not None]
            take_ids = {str(item.get("raw_signal_id")) for item in take_rows}
            removed = baseline_take_ids.difference(take_ids)
            row = {
                "candidate_name": candidate["candidate_name"],
                "description": candidate["description"],
                "filter_rule": candidate["filter_rule"],
                "exit_model": candidate["exit_model"],
                "raw_signal_count": len(subset),
                "take_count": len(take_rows),
                "hold_count": len(subset) - len(take_rows),
                "trade_retention_percent": pct(len(take_rows), len(subset)),
                "win_rate": pct(sum(1 for value in r_values if value > 0.0), len(r_values)),
                "avg_r": mean(r_values),
                "median_r": median(r_values),
                "net_r": sum(r_values),
                "profit_factor": profit_factor(r_values),
                "max_drawdown_r": max_drawdown_r(r_values),
                "max_consecutive_losses": max_consecutive_losses(r_values),
                "removed_losers_vs_baseline": len(removed.intersection(baseline_loser_ids)),
                "removed_winners_vs_baseline": len(removed.intersection(baseline_winner_ids)),
                "net_filter_improvement_r_vs_baseline": sum(r_values) - baseline_net_r,
                "sample_limited": len(take_rows) < MIN_LOCK_SAMPLE,
            }
        row["lock_score"] = candidate_lock_score(row)
        rows.append(row)
    return rows


def empty_metric(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_name": candidate["candidate_name"],
        "description": candidate["description"],
        "filter_rule": candidate["filter_rule"],
        "exit_model": candidate["exit_model"],
        "raw_signal_count": 0,
        "take_count": 0,
        "hold_count": 0,
        "trade_retention_percent": 0.0,
        "win_rate": 0.0,
        "avg_r": 0.0,
        "median_r": 0.0,
        "net_r": 0.0,
        "profit_factor": 0.0,
        "max_drawdown_r": 0.0,
        "max_consecutive_losses": 0,
        "removed_losers_vs_baseline": 0,
        "removed_winners_vs_baseline": 0,
        "net_filter_improvement_r_vs_baseline": 0.0,
        "sample_limited": True,
    }


def candidate_lock_score(row: dict[str, Any]) -> float:
    take_count = float(row.get("take_count") or 0.0)
    pf = float(row.get("profit_factor") or 0.0)
    avg_r = float(row.get("avg_r") or 0.0)
    net_r = float(row.get("net_r") or 0.0)
    max_dd = abs(float(row.get("max_drawdown_r") or 0.0))
    retention = float(row.get("trade_retention_percent") or 0.0)
    d1_preference = 0.35 if row.get("candidate_name") == "D1" else 0.0
    sample_balance = min(take_count, MIN_LOCK_SAMPLE) / MIN_LOCK_SAMPLE
    return (pf * 1.2) + (avg_r * 2.0) + (net_r * 0.08) - (max_dd * 0.04) + (retention * 0.5) + sample_balance + d1_preference


def build_direction_investigation(
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    s3_direction: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config_direction = str(config.get("direction", "unknown"))
    config_strategy = str(config.get("strategy", "unknown"))
    long_only = "BUY_ONLY" in config_direction.upper() or "long" in config_strategy.lower() or "raw_donchian" in config_strategy.lower()
    signal_counts = direction_counts(signals.get("direction", []))
    trade_counts = direction_counts(trades.get("direction", []))
    assessment = sell_generation_status(signal_counts, long_only)

    if not s3_direction.empty:
        s3_skipped = s3_direction[s3_direction["metric"].astype(str).eq("skipped_signals")]
        skipped_counts = Counter(
            {
                "BUY": int(safe_float(s3_skipped.iloc[0].get("buy_count")) or 0) if not s3_skipped.empty else 0,
                "SELL": int(safe_float(s3_skipped.iloc[0].get("sell_count")) or 0) if not s3_skipped.empty else 0,
                "UNKNOWN": int(safe_float(s3_skipped.iloc[0].get("unknown_count")) or 0) if not s3_skipped.empty else 0,
            }
        )
    else:
        skipped_counts = Counter({"BUY": 0, "SELL": 0, "UNKNOWN": 0})

    rows = [
        direction_row("raw_signals", signal_counts, assessment, config_direction, config_strategy, long_only),
        direction_row("executed_trades", trade_counts, assessment, config_direction, config_strategy, long_only),
        direction_row("skipped_signals", skipped_counts, assessment, config_direction, config_strategy, long_only),
    ]
    return rows, {
        "sell_signals_exist": signal_counts["SELL"] > 0,
        "buy_raw_signals": signal_counts["BUY"],
        "sell_raw_signals": signal_counts["SELL"],
        "buy_trades": trade_counts["BUY"],
        "sell_trades": trade_counts["SELL"],
        "buy_skipped": skipped_counts["BUY"],
        "sell_skipped": skipped_counts["SELL"],
        "config_direction": config_direction,
        "config_strategy": config_strategy,
        "strategy_implies_long_only": long_only,
        "sell_generation_status": assessment,
    }


def sell_generation_status(signal_counts: Counter[str], long_only: bool) -> str:
    if signal_counts["SELL"] > 0:
        return "SELL_GENERATION_PRESENT"
    if long_only:
        return "SELL_GENERATION_DISABLED_BY_LONG_ONLY_CONFIG"
    return "SELL_GENERATION_ABSENT_OR_NOT_IMPLEMENTED"


def direction_row(
    metric: str,
    counts: Counter[str],
    assessment: str,
    config_direction: str,
    config_strategy: str,
    long_only: bool,
) -> dict[str, Any]:
    if counts["SELL"] == 0 and long_only:
        notes = "No SELL signals found; config/strategy indicates BUY-only or long-only behavior."
    elif counts["SELL"] == 0:
        notes = "No SELL signals found; investigate whether SELL generation is absent or not implemented."
    else:
        notes = "SELL signals exist in diagnostic data."
    return {
        "metric": metric,
        "buy_count": counts["BUY"],
        "sell_count": counts["SELL"],
        "unknown_count": counts["UNKNOWN"],
        "assessment": "SHORT_SIDE_MISSING" if counts["SELL"] == 0 else "SELL_SIDE_PRESENT",
        "config_direction": config_direction,
        "config_strategy": config_strategy,
        "strategy_implies_long_only": long_only,
        "sell_generation_status": assessment,
        "notes": notes,
    }


def build_drawdown_attribution(
    candidate_decisions: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    locked: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric in metrics:
        rows.append(
            {
                "section": "candidate_drawdown",
                "candidate_name": metric["candidate_name"],
                "rank": "",
                "raw_signal_id": "",
                "timestamp": "",
                "session": "",
                "weekday": "",
                "volatility_regime": "",
                "simulated_r": "",
                "cumulative_r": "",
                "drawdown_r": "",
                "max_drawdown_r": metric["max_drawdown_r"],
                "max_consecutive_losses": metric["max_consecutive_losses"],
                "note": "Candidate-level drawdown from TAKE sequence.",
            }
        )
    if not locked:
        return rows
    locked_rows = [
        row
        for row in candidate_decisions
        if row["candidate_name"] == locked["candidate_name"] and row["candidate_decision"] == "TAKE"
    ]
    worst = sorted(drawdown_curve(locked_rows), key=lambda row: float(row["drawdown_r"]))[:10]
    metric = next((row for row in metrics if row["candidate_name"] == locked["candidate_name"]), {})
    for rank, row in enumerate(worst, start=1):
        rows.append(
            {
                "section": "locked_candidate_worst_drawdown_points",
                "candidate_name": locked["candidate_name"],
                "rank": rank,
                "raw_signal_id": row["raw_signal_id"],
                "timestamp": row["timestamp"],
                "session": row["session"],
                "weekday": row["weekday"],
                "volatility_regime": row["volatility_regime"],
                "simulated_r": row["simulated_r"],
                "cumulative_r": row["cumulative_r"],
                "drawdown_r": row["drawdown_r"],
                "max_drawdown_r": metric.get("max_drawdown_r", ""),
                "max_consecutive_losses": metric.get("max_consecutive_losses", ""),
                "note": "Worst drawdown points for locked candidate.",
            }
        )
    return rows


def choose_lock_candidate(
    metrics: list[dict[str, Any]],
    direction_summary: dict[str, Any],
) -> tuple[dict[str, Any] | None, str, str, list[str]]:
    warnings: list[str] = []
    if not bool(direction_summary.get("sell_signals_exist")):
        warnings.append("SHORT_SIDE_MISSING")

    viable = [
        row
        for row in metrics
        if int(row.get("take_count") or 0) > 0
        and float(row.get("profit_factor") or 0.0) > 1.0
        and float(row.get("avg_r") or 0.0) > 0.0
    ]
    if not viable:
        if not bool(direction_summary.get("sell_signals_exist")) and int(direction_summary.get("buy_raw_signals") or 0) == 0:
            return None, "INVESTIGATE_SHORT_SIDE", "No BUY or SELL candidate signal stream is available.", warnings
        return None, "NO_CANDIDATE_LOCKED", "No S4 candidate has positive avgR and PF above 1.0.", warnings

    if all(int(row.get("take_count") or 0) < MIN_LOCK_SAMPLE for row in viable):
        warnings.append("SAMPLE_LIMITED")

    d1 = next((row for row in viable if row["candidate_name"] == "D1"), None)
    best_by_score = max(viable, key=lambda row: float(row.get("lock_score") or 0.0))
    if d1 and d1_is_balanced(d1, best_by_score):
        locked = d1
    else:
        locked = best_by_score

    suffix = "_SAMPLE_LIMITED"
    decision = f"LOCK_SHADOW_CANDIDATE_{locked['candidate_name']}{suffix}"
    reason = (
        f"{locked['candidate_name']} selected for shadow-forward observation: "
        f"PF={float(locked['profit_factor']):.2f}, avgR={float(locked['avg_r']):.3f}, "
        f"take_count={int(locked['take_count'])}, maxDD={float(locked['max_drawdown_r']):.3f}R."
    )
    return locked, decision, reason, warnings


def d1_is_balanced(d1: dict[str, Any], best: dict[str, Any]) -> bool:
    if d1["candidate_name"] == best["candidate_name"]:
        return True
    if int(d1.get("take_count") or 0) <= 0:
        return False
    d1_pf = float(d1.get("profit_factor") or 0.0)
    best_pf = float(best.get("profit_factor") or 0.0)
    d1_avg = float(d1.get("avg_r") or 0.0)
    best_avg = float(best.get("avg_r") or 0.0)
    d1_take = int(d1.get("take_count") or 0)
    best_take = int(best.get("take_count") or 0)
    d1_dd = abs(float(d1.get("max_drawdown_r") or 0.0))
    best_dd = abs(float(best.get("max_drawdown_r") or 0.0))
    return (
        d1_pf >= 1.0
        and d1_avg > 0.0
        and d1_take >= max(1, int(best_take * 0.6))
        and d1_pf >= best_pf * 0.75
        and d1_avg >= best_avg * 0.55
        and d1_dd <= max(best_dd * 1.25, best_dd + 2.0)
    )


def drawdown_curve(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    cumulative = 0.0
    peak = 0.0
    for row in rows:
        r_value = safe_float(row.get("simulated_r"))
        if r_value is None:
            continue
        cumulative += r_value
        peak = max(peak, cumulative)
        output.append(
            {
                **row,
                "simulated_r": r_value,
                "cumulative_r": cumulative,
                "drawdown_r": cumulative - peak,
            }
        )
    return output


def direction_counts(values: Any) -> Counter[str]:
    counter: Counter[str] = Counter()
    for value in list(values):
        direction = str(value or "UNKNOWN").upper()
        if direction not in {"BUY", "SELL"}:
            direction = "UNKNOWN"
        counter[direction] += 1
    counter.setdefault("BUY", 0)
    counter.setdefault("SELL", 0)
    counter.setdefault("UNKNOWN", 0)
    return counter


def pct(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def profit_factor(values: list[float]) -> float:
    gross_win = sum(value for value in values if value > 0.0)
    gross_loss = abs(sum(value for value in values if value <= 0.0))
    if gross_loss == 0.0:
        return 10.0 if gross_win > 0.0 else 0.0
    return gross_win / gross_loss


def max_drawdown_r(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def max_consecutive_losses(values: list[float]) -> int:
    longest = 0
    current = 0
    for value in values:
        if value <= 0.0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def print_phase_s4_report(result: dict[str, Any]) -> None:
    summary = result["summary"]
    print("AURUM-1 Phase S4 Shadow Decision Candidate Lock")
    print("=" * 84)
    print("Candidate comparison table")
    print(f"{'candidate':<10}{'exit':<10}{'take':>6}{'hold':>6}{'avgR':>9}{'PF':>8}{'netR':>9}{'maxDD':>9}")
    for row in result["candidate_metrics"]:
        print(
            f"{row['candidate_name']:<10}{row['exit_model']:<10}"
            f"{int(row['take_count']):>6}{int(row['hold_count']):>6}"
            f"{float(row['avg_r']):>9.3f}{float(row['profit_factor']):>8.2f}"
            f"{float(row['net_r']):>9.3f}{float(row['max_drawdown_r']):>9.3f}"
        )
    print()
    print("Locked shadow candidate")
    locked = summary.get("locked_shadow_candidate")
    if locked:
        print(
            f"  {locked['candidate_name']} {locked['exit_model']} "
            f"take={locked['take_count']} avgR={float(locked['avg_r']):.3f} PF={float(locked['profit_factor']):.2f}"
        )
    else:
        print("  none")
    print()
    print("Direction investigation")
    for row in result["direction_investigation"]:
        print(
            f"  {row['metric']:<16} BUY={int(row['buy_count']):>4} "
            f"SELL={int(row['sell_count']):>4} UNKNOWN={int(row['unknown_count']):>4} "
            f"{row['sell_generation_status']}"
        )
    print()
    print("Drawdown attribution")
    worst = min(result["candidate_metrics"], key=lambda row: float(row.get("max_drawdown_r") or 0.0), default=None)
    if worst:
        print(
            f"  Worst candidate DD: {worst['candidate_name']} "
            f"maxDD={float(worst['max_drawdown_r']):.3f}R maxLossStreak={worst['max_consecutive_losses']}"
        )
    print()
    print(f"Research decision: {summary['research_decision']}")
    if summary.get("warnings"):
        print(f"Warnings: {', '.join(summary['warnings'])}")
    print(f"Reason: {summary['research_decision_reason']}")
    print("Outputs:")
    for value in result["paths"].values():
        print(f"  {value}")


def utc_now_or_as_of(as_of: str | None) -> str:
    if not as_of:
        return datetime.now(UTC).isoformat()
    ts = pd.Timestamp(as_of)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").isoformat()


def json_default(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return str(value)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AURUM-1 Phase S4 shadow decision candidate lock.")
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--as-of", default=None, help="Optional UTC timestamp to stamp the summary.")
    return parser.parse_args(argv)
