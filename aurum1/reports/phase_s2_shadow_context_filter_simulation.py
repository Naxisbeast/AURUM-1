"""Phase S2 shadow context-filter simulation for AURUM-1.

This module is diagnostic only. It reads the forward-shadow ledger plus Phase S1
audit CSVs, simulates fixed context filters and fixed exit variants, and writes
research artifacts. It does not import broker/execution code, submit orders,
change strategy thresholds, or mutate live/paper trading state.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from aurum1.reports.phase_s1_forward_shadow_failure_audit import (
    DEFAULT_REPORT_DIR,
    DEFAULT_SHADOW_DB,
    RESEARCH_DECISIONS as S1_RESEARCH_DECISIONS,
    load_shadow_data,
    normalize_candles,
    normalize_signals,
    normalize_trades,
    run_phase_s1_audit,
    safe_float,
    simulate_signal_exit,
    write_csv,
)


ROOT = Path(__file__).resolve().parents[2]
MIN_REASONABLE_TRADES = 5
NORMAL_VOLATILITY_LABELS = {"normal", "medium"}

S2_RESEARCH_DECISIONS = {
    "CONTINUE_COLLECTING_SAMPLE",
    "VOLATILITY_FILTER_PROMISING",
    "SESSION_FILTER_PROMISING",
    "EXIT_1_5R_PROMISING",
    "BUY_SIDE_FAILING",
    "SHORT_SIDE_MISSING",
    "NO_FILTER_HELPFUL",
}

VARIANT_FIELDS = [
    "variant",
    "description",
    "context_filter",
    "exit_mode",
    "trade_count",
    "win_count",
    "loss_count",
    "win_rate",
    "avg_r",
    "median_r",
    "net_r",
    "profit_factor",
    "max_drawdown_r",
    "max_consecutive_losses",
    "average_pnl",
    "net_pnl",
    "percent_trade_retention_vs_baseline",
    "notes",
]

SKIP_IMPACT_FIELDS = [
    "variant",
    "context_filter",
    "executed_losing_trades_removed",
    "executed_winning_trades_removed",
    "executed_other_trades_removed",
    "removed_net_r",
    "net_r_improvement",
    "trade_retention",
    "baseline_trade_count",
    "kept_trade_count",
]

EXIT_BY_CONTEXT_FIELDS = [
    "variant",
    "context_filter",
    "exit_name",
    "trade_count",
    "win_count",
    "loss_count",
    "win_rate",
    "avg_r",
    "median_r",
    "net_r",
    "profit_factor",
    "max_drawdown_r",
    "max_consecutive_losses",
    "average_pnl",
    "net_pnl",
    "percent_trade_retention_vs_baseline",
]

DIRECTION_FIELDS = [
    "metric",
    "buy_count",
    "sell_count",
    "unknown_count",
    "assessment",
    "notes",
]


def run_phase_s2_simulation(
    shadow_db: Path | str = DEFAULT_SHADOW_DB,
    report_dir: Path | str = DEFAULT_REPORT_DIR,
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Run Phase S2 and write all requested artifacts."""

    db_path = resolve_path(shadow_db)
    output_dir = resolve_path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ensure_s1_inputs(db_path, output_dir)
    trade_audit = load_audit_csv(output_dir / "phase_s1_trade_audit.csv")
    skipped_audit = load_audit_csv(output_dir / "phase_s1_skipped_signal_audit.csv")
    failure_breakdown = load_audit_csv(output_dir / "phase_s1_failure_mode_breakdown.csv")

    shadow = load_shadow_data(db_path)
    signals = normalize_signals(shadow["signals"])
    trades = normalize_trades(shadow["trades"])
    candles = normalize_candles(shadow["candles"])
    config = shadow["config"]

    executed = normalize_trade_audit(trade_audit)
    skipped = normalize_skipped_audit(skipped_audit)
    executed = attach_exit_simulations(executed, signals, candles)
    baseline_count = len(executed)

    variants = variant_definitions()
    variant_rows = build_variant_comparison(executed, variants, baseline_count)
    skip_rows = build_skip_impact(executed, variants, baseline_count)
    exit_context_rows = build_exit_by_context(executed, variants, baseline_count)
    direction_rows, direction_summary = build_direction_availability(signals, trades, executed, skipped, config)
    sell_variant = build_sell_variant(signals, candles, baseline_count)
    variant_rows.append(sell_variant)

    decision, reason = choose_research_decision(variant_rows, exit_context_rows, direction_summary, baseline_count)
    generated_at = utc_now_or_as_of(as_of)
    summary = {
        "generated_at": generated_at,
        "phase": "S2",
        "name": "Shadow Context Filter Simulation",
        "classification": "research-only",
        "shadow_db": str(db_path),
        "report_dir": str(output_dir),
        "baseline_trade_count": baseline_count,
        "variant_count": len(variant_rows),
        "research_decision": decision,
        "research_decision_reason": reason,
        "direction_availability": direction_summary,
        "s1_failure_mode_breakdown_loaded": not failure_breakdown.empty,
        "s1_decision_space": sorted(S1_RESEARCH_DECISIONS),
        "safety": {
            "orders_placed": False,
            "execution_logic_modified": False,
            "live_or_paper_behavior_modified": False,
            "strategy_thresholds_modified": False,
            "timers_modified": False,
            "forward_shadow_runner_modified": False,
            "sqlite_read_mode": "query_only",
        },
        "variant_notes": {
            "normal_volatility_only": "Matches volatility_regime in {'normal', 'medium'} to support both S1 label styles.",
            "exit_simulation": "Fixed 1R/1.5R/2R exits are replayed from logged shadow signals and candles.",
            "sell_candidate_scope": "SELL-only candidate uses SELL signals found in shadow_signals, including skipped candidates if present.",
        },
    }

    paths = {
        "summary_json": output_dir / "phase_s2_context_filter_summary.json",
        "variant_comparison_csv": output_dir / "phase_s2_variant_comparison.csv",
        "skip_impact_csv": output_dir / "phase_s2_skip_impact.csv",
        "exit_by_context_csv": output_dir / "phase_s2_exit_by_context.csv",
        "direction_availability_csv": output_dir / "phase_s2_direction_availability.csv",
    }
    paths["summary_json"].write_text(json.dumps(summary, indent=2, sort_keys=True, default=json_default), encoding="utf-8")
    write_csv(paths["variant_comparison_csv"], variant_rows, VARIANT_FIELDS)
    write_csv(paths["skip_impact_csv"], skip_rows, SKIP_IMPACT_FIELDS)
    write_csv(paths["exit_by_context_csv"], exit_context_rows, EXIT_BY_CONTEXT_FIELDS)
    write_csv(paths["direction_availability_csv"], direction_rows, DIRECTION_FIELDS)

    return {
        "summary": summary,
        "variant_comparison": variant_rows,
        "skip_impact": skip_rows,
        "exit_by_context": exit_context_rows,
        "direction_availability": direction_rows,
        "paths": {key: str(value) for key, value in paths.items()},
    }


def resolve_path(path: Path | str) -> Path:
    candidate = Path(path)
    return ROOT / candidate if not candidate.is_absolute() else candidate


def ensure_s1_inputs(shadow_db: Path, report_dir: Path) -> None:
    required = [
        report_dir / "phase_s1_trade_audit.csv",
        report_dir / "phase_s1_skipped_signal_audit.csv",
    ]
    if all(path.exists() for path in required):
        return
    run_phase_s1_audit(shadow_db, report_dir)


def load_audit_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def normalize_trade_audit(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    result = result[result.get("outcome", "").astype(str).isin(["win", "loss", "expired"])].copy()
    for column in ("realized_r", "realized_pnl", "entry", "stop", "target"):
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    for column in ("signal_time", "entry_time", "exit_time", "direction", "session_label", "weekday", "volatility_regime"):
        if column not in result:
            result[column] = ""
    result["direction"] = result["direction"].fillna("UNKNOWN").astype(str).str.upper()
    result["session_label"] = result["session_label"].fillna("unknown").astype(str).str.lower()
    result["weekday"] = result["weekday"].fillna("unknown").astype(str)
    result["volatility_regime"] = result["volatility_regime"].fillna("unknown").astype(str).str.lower()
    result = result.sort_values("entry_time").reset_index(drop=True)
    return result


def normalize_skipped_audit(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    if "direction" not in result:
        result["direction"] = "UNKNOWN"
    result["direction"] = result["direction"].fillna("UNKNOWN").astype(str).str.upper()
    return result


def attach_exit_simulations(executed: pd.DataFrame, signals: pd.DataFrame, candles: pd.DataFrame) -> pd.DataFrame:
    if executed.empty:
        return executed
    result = executed.copy()
    signal_by_time = {str(row.get("signal_time")): row for _, row in signals.iterrows()} if not signals.empty else {}
    for exit_name, target_r in (("fixed_1r", 1.0), ("fixed_1_5r", 1.5), ("fixed_2r", 2.0)):
        r_values: list[float | None] = []
        pnl_values: list[float | None] = []
        reasons: list[str] = []
        for _, trade in result.iterrows():
            signal = signal_by_time.get(str(trade.get("signal_time") or ""))
            if signal is None:
                r_values.append(None)
                pnl_values.append(None)
                reasons.append("missing_signal")
                continue
            sim = simulate_signal_exit(signal, candles, target_r=target_r, exit_mode="fixed", close_at_end=True)
            r_values.append(sim.r_multiple)
            pnl_values.append(sim.pnl)
            reasons.append(sim.exit_reason)
        result[f"{exit_name}_r"] = r_values
        result[f"{exit_name}_pnl"] = pnl_values
        result[f"{exit_name}_exit_reason"] = reasons
    return result


def variant_definitions() -> list[dict[str, Any]]:
    return [
        {
            "variant": "A",
            "description": "current raw_donchian_fixed_2r baseline",
            "context_filter": "baseline",
            "exit_mode": "current_realized",
            "predicate": lambda row: True,
        },
        {
            "variant": "B",
            "description": "exclude high volatility",
            "context_filter": "volatility_regime != high",
            "exit_mode": "current_realized",
            "predicate": lambda row: str(row.get("volatility_regime", "")).lower() != "high",
        },
        {
            "variant": "C",
            "description": "normal volatility only",
            "context_filter": "volatility_regime in normal/medium",
            "exit_mode": "current_realized",
            "predicate": lambda row: str(row.get("volatility_regime", "")).lower() in NORMAL_VOLATILITY_LABELS,
        },
        {
            "variant": "D",
            "description": "exclude London session",
            "context_filter": "session_label != london",
            "exit_mode": "current_realized",
            "predicate": lambda row: str(row.get("session_label", "")).lower() != "london",
        },
        {
            "variant": "E",
            "description": "exclude Thursday",
            "context_filter": "weekday != Thursday",
            "exit_mode": "current_realized",
            "predicate": lambda row: str(row.get("weekday", "")).lower() != "thursday",
        },
        {
            "variant": "F",
            "description": "exclude high volatility + exclude London",
            "context_filter": "volatility_regime != high AND session_label != london",
            "exit_mode": "current_realized",
            "predicate": lambda row: str(row.get("volatility_regime", "")).lower() != "high"
            and str(row.get("session_label", "")).lower() != "london",
        },
        {
            "variant": "G",
            "description": "exclude high volatility + fixed_1_5r exit simulation",
            "context_filter": "volatility_regime != high",
            "exit_mode": "fixed_1_5r",
            "predicate": lambda row: str(row.get("volatility_regime", "")).lower() != "high",
        },
        {
            "variant": "H",
            "description": "exclude London + fixed_1_5r exit simulation",
            "context_filter": "session_label != london",
            "exit_mode": "fixed_1_5r",
            "predicate": lambda row: str(row.get("session_label", "")).lower() != "london",
        },
        {
            "variant": "I",
            "description": "normal volatility only + fixed_1_5r exit simulation",
            "context_filter": "volatility_regime in normal/medium",
            "exit_mode": "fixed_1_5r",
            "predicate": lambda row: str(row.get("volatility_regime", "")).lower() in NORMAL_VOLATILITY_LABELS,
        },
        {
            "variant": "J",
            "description": "BUY-only current baseline",
            "context_filter": "direction == BUY",
            "exit_mode": "current_realized",
            "predicate": lambda row: str(row.get("direction", "")).upper() == "BUY",
        },
    ]


def build_variant_comparison(executed: pd.DataFrame, variants: list[dict[str, Any]], baseline_count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variant in variants:
        subset = filter_frame(executed, variant["predicate"])
        rows.append(
            {
                "variant": variant["variant"],
                "description": variant["description"],
                "context_filter": variant["context_filter"],
                "exit_mode": variant["exit_mode"],
                **metrics_for_subset(subset, baseline_count, variant["exit_mode"]),
                "notes": "Diagnostic-only variant; no execution behavior changed.",
            }
        )
    return rows


def build_skip_impact(executed: pd.DataFrame, variants: list[dict[str, Any]], baseline_count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    baseline_net_r = net_r_for_subset(executed, "current_realized")
    for variant in variants:
        subset = filter_frame(executed, variant["predicate"])
        kept_signals = set(subset["signal_time"].astype(str).tolist()) if not subset.empty and "signal_time" in subset else set()
        removed = executed[~executed["signal_time"].astype(str).isin(kept_signals)].copy() if not executed.empty else pd.DataFrame()
        removed_r = numeric_list(removed.get("realized_r", []))
        kept_net_r = net_r_for_subset(subset, "current_realized")
        rows.append(
            {
                "variant": variant["variant"],
                "context_filter": variant["context_filter"],
                "executed_losing_trades_removed": sum(1 for value in removed_r if value <= 0.0),
                "executed_winning_trades_removed": sum(1 for value in removed_r if value > 0.0),
                "executed_other_trades_removed": max(0, len(removed) - len(removed_r)),
                "removed_net_r": sum(removed_r),
                "net_r_improvement": kept_net_r - baseline_net_r,
                "trade_retention": pct(len(subset), baseline_count),
                "baseline_trade_count": baseline_count,
                "kept_trade_count": len(subset),
            }
        )
    return rows


def build_exit_by_context(executed: pd.DataFrame, variants: list[dict[str, Any]], baseline_count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variant in variants:
        subset = filter_frame(executed, variant["predicate"])
        for exit_name in ("fixed_1r", "fixed_1_5r", "fixed_2r"):
            rows.append(
                {
                    "variant": variant["variant"],
                    "context_filter": variant["context_filter"],
                    "exit_name": exit_name,
                    **metrics_for_subset(subset, baseline_count, exit_name),
                }
            )
    return rows


def build_sell_variant(signals: pd.DataFrame, candles: pd.DataFrame, baseline_count: int) -> dict[str, Any]:
    if signals.empty or "direction" not in signals:
        return empty_sell_variant("no SELL candidates found", baseline_count)
    sell_signals = signals[signals["direction"].astype(str).str.upper().eq("SELL")].copy()
    if sell_signals.empty:
        return empty_sell_variant("no SELL candidates found", baseline_count)
    rows: list[dict[str, Any]] = []
    for _, signal in sell_signals.iterrows():
        sim = simulate_signal_exit(signal, candles, target_r=2.0, exit_mode="fixed", close_at_end=True)
        rows.append({"simulated_r": sim.r_multiple, "simulated_pnl": sim.pnl})
    frame = pd.DataFrame(rows)
    frame["realized_r"] = frame["simulated_r"]
    frame["realized_pnl"] = frame["simulated_pnl"]
    return {
        "variant": "K",
        "description": "SELL-only candidate behavior",
        "context_filter": "direction == SELL",
        "exit_mode": "fixed_2r_simulated",
        **metrics_for_subset(frame, baseline_count, "current_realized"),
        "notes": "SELL candidates found in shadow signal data and simulated diagnostically.",
    }


def empty_sell_variant(note: str, baseline_count: int) -> dict[str, Any]:
    return {
        "variant": "K",
        "description": "SELL-only candidate behavior",
        "context_filter": "direction == SELL",
        "exit_mode": "fixed_2r_simulated",
        **empty_metrics(baseline_count),
        "notes": note,
    }


def build_direction_availability(
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    executed: pd.DataFrame,
    skipped: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    signal_counts = direction_counts(signals.get("direction", []))
    trade_counts = direction_counts(executed.get("direction", []))
    skipped_counts = direction_counts(skipped.get("direction", []))
    db_trade_counts = direction_counts(trades.get("direction", []))
    assessment = direction_assessment(signal_counts, trade_counts, skipped_counts, config)
    rows = [
        direction_row("signals", signal_counts, assessment, "Rows from shadow_signals."),
        direction_row("executed_trades", trade_counts, assessment, "Rows from S1 trade audit."),
        direction_row("shadow_trades_table", db_trade_counts, assessment, "Rows from shadow_trades table."),
        direction_row("skipped_signals", skipped_counts, assessment, "Rows from S1 skipped signal audit."),
    ]
    summary = {
        "buy_signals": signal_counts["BUY"],
        "sell_signals": signal_counts["SELL"],
        "buy_trades": trade_counts["BUY"],
        "sell_trades": trade_counts["SELL"],
        "buy_skipped": skipped_counts["BUY"],
        "sell_skipped": skipped_counts["SELL"],
        "assessment": assessment,
        "configured_direction": str(config.get("direction", "unknown")),
    }
    return rows, summary


def direction_row(metric: str, counts: Counter[str], assessment: str, notes: str) -> dict[str, Any]:
    return {
        "metric": metric,
        "buy_count": counts["BUY"],
        "sell_count": counts["SELL"],
        "unknown_count": counts["UNKNOWN"],
        "assessment": assessment,
        "notes": notes,
    }


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


def direction_assessment(
    signal_counts: Counter[str],
    trade_counts: Counter[str],
    skipped_counts: Counter[str],
    config: dict[str, Any],
) -> str:
    configured = str(config.get("direction", "")).upper()
    if "BUY_ONLY" in configured and signal_counts["SELL"] == 0 and trade_counts["SELL"] == 0 and skipped_counts["SELL"] == 0:
        return "SELL side disabled"
    if signal_counts["SELL"] == 0 and trade_counts["SELL"] == 0 and skipped_counts["SELL"] == 0:
        return "SELL side absent"
    if signal_counts["SELL"] > 0 and trade_counts["SELL"] == 0:
        return "SELL candidates triggered but not executed"
    return "SELL side available"


def choose_research_decision(
    variant_rows: list[dict[str, Any]],
    exit_rows: list[dict[str, Any]],
    direction_summary: dict[str, Any],
    baseline_count: int,
) -> tuple[str, str]:
    by_variant = {str(row["variant"]): row for row in variant_rows}
    baseline = by_variant.get("A", empty_sell_variant("missing baseline", baseline_count))
    baseline_avg = float(baseline.get("avg_r") or 0.0)
    baseline_pf = float(baseline.get("profit_factor") or 0.0)

    if is_promising(by_variant.get("B"), baseline_avg, baseline_pf) or is_promising(by_variant.get("C"), baseline_avg, baseline_pf) or is_promising(by_variant.get("G"), baseline_avg, baseline_pf) or is_promising(by_variant.get("I"), baseline_avg, baseline_pf):
        return "VOLATILITY_FILTER_PROMISING", "Volatility-filtered variants improved PF/avgR while retaining a reasonable trade count."

    if is_promising(by_variant.get("D"), baseline_avg, baseline_pf) or is_promising(by_variant.get("F"), baseline_avg, baseline_pf) or is_promising(by_variant.get("H"), baseline_avg, baseline_pf):
        return "SESSION_FILTER_PROMISING", "London/session-filtered variants improved PF/avgR while retaining a reasonable trade count."

    baseline_exit_2r = exit_metric(exit_rows, "A", "fixed_2r")
    baseline_exit_15r = exit_metric(exit_rows, "A", "fixed_1_5r")
    if baseline_exit_2r and baseline_exit_15r:
        if material_exit_improvement(baseline_exit_15r, baseline_exit_2r):
            return "EXIT_1_5R_PROMISING", "Fixed 1.5R materially improved PF/avgR over fixed 2R in baseline context."

    if baseline_count < MIN_REASONABLE_TRADES or all(int(row.get("trade_count") or 0) < MIN_REASONABLE_TRADES for row in variant_rows if row.get("variant") != "K"):
        return "CONTINUE_COLLECTING_SAMPLE", "All variants remain sample-limited for a reliable decision."

    if "absent" in str(direction_summary.get("assessment", "")).lower() and float(baseline.get("avg_r") or 0.0) < -0.25:
        return "SHORT_SIDE_MISSING", "BUY-only behavior is weak and no SELL-side candidates are available for comparison."

    if str(direction_summary.get("assessment", "")).lower().startswith("sell side disabled") and float(baseline.get("avg_r") or 0.0) < -0.25 and float(baseline.get("profit_factor") or 0.0) < 0.75:
        return "BUY_SIDE_FAILING", "BUY-only forward-shadow behavior is weak and SELL is disabled by the candidate scope."

    return "NO_FILTER_HELPFUL", "No tested non-optimized filter or fixed exit produced a material improvement."


def is_promising(row: dict[str, Any] | None, baseline_avg: float, baseline_pf: float) -> bool:
    if not row:
        return False
    trade_count = int(row.get("trade_count") or 0)
    retention = float(row.get("percent_trade_retention_vs_baseline") or 0.0)
    avg_r = float(row.get("avg_r") or 0.0)
    pf = float(row.get("profit_factor") or 0.0)
    if trade_count < MIN_REASONABLE_TRADES or retention < 0.35:
        return False
    return (avg_r - baseline_avg >= 0.20 and pf >= baseline_pf) or (pf - baseline_pf >= 0.30 and avg_r >= baseline_avg)


def material_exit_improvement(candidate: dict[str, Any], baseline: dict[str, Any]) -> bool:
    candidate_count = int(candidate.get("trade_count") or 0)
    if candidate_count < MIN_REASONABLE_TRADES:
        return False
    avg_delta = float(candidate.get("avg_r") or 0.0) - float(baseline.get("avg_r") or 0.0)
    pf_delta = float(candidate.get("profit_factor") or 0.0) - float(baseline.get("profit_factor") or 0.0)
    return avg_delta >= 0.20 or pf_delta >= 0.30


def exit_metric(rows: list[dict[str, Any]], variant: str, exit_name: str) -> dict[str, Any] | None:
    return next((row for row in rows if row.get("variant") == variant and row.get("exit_name") == exit_name), None)


def filter_frame(frame: pd.DataFrame, predicate: Callable[[pd.Series], bool]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    mask = frame.apply(predicate, axis=1)
    return frame[mask].copy().reset_index(drop=True)


def metrics_for_subset(frame: pd.DataFrame, baseline_count: int, exit_mode: str) -> dict[str, Any]:
    if frame.empty:
        return empty_metrics(baseline_count)
    if exit_mode == "fixed_1r":
        r_column, pnl_column = "fixed_1r_r", "fixed_1r_pnl"
    elif exit_mode == "fixed_1_5r":
        r_column, pnl_column = "fixed_1_5r_r", "fixed_1_5r_pnl"
    elif exit_mode == "fixed_2r":
        r_column, pnl_column = "fixed_2r_r", "fixed_2r_pnl"
    else:
        r_column, pnl_column = "realized_r", "realized_pnl"

    r_values = numeric_list(frame.get(r_column, []))
    pnl_values = numeric_list(frame.get(pnl_column, []))
    wins = [value for value in r_values if value > 0.0]
    losses = [value for value in r_values if value <= 0.0]
    return {
        "trade_count": len(r_values),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": pct(len(wins), len(r_values)),
        "avg_r": mean(r_values),
        "median_r": median(r_values),
        "net_r": sum(r_values),
        "profit_factor": profit_factor(r_values),
        "max_drawdown_r": max_drawdown_r(r_values),
        "max_consecutive_losses": max_consecutive_losses(r_values),
        "average_pnl": mean(pnl_values),
        "net_pnl": sum(pnl_values) if pnl_values else 0.0,
        "percent_trade_retention_vs_baseline": pct(len(r_values), baseline_count),
    }


def empty_metrics(baseline_count: int) -> dict[str, Any]:
    return {
        "trade_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0.0,
        "avg_r": 0.0,
        "median_r": 0.0,
        "net_r": 0.0,
        "profit_factor": 0.0,
        "max_drawdown_r": 0.0,
        "max_consecutive_losses": 0,
        "average_pnl": 0.0,
        "net_pnl": 0.0,
        "percent_trade_retention_vs_baseline": pct(0, baseline_count),
    }


def net_r_for_subset(frame: pd.DataFrame, exit_mode: str) -> float:
    return float(metrics_for_subset(frame, len(frame), exit_mode)["net_r"])


def numeric_list(values: Any) -> list[float]:
    output: list[float] = []
    for value in list(values):
        number = safe_float(value)
        if number is not None:
            output.append(number)
    return output


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


def print_phase_s2_report(result: dict[str, Any]) -> None:
    summary = result["summary"]
    print("AURUM-1 Phase S2 Shadow Context Filter Simulation")
    print("=" * 80)
    print("Variant comparison table")
    print(f"{'var':<5}{'description':<48}{'trades':>7}{'avgR':>9}{'PF':>8}{'win':>8}{'retain':>9}")
    for row in result["variant_comparison"]:
        print(
            f"{row['variant']:<5}{str(row['description'])[:47]:<48}"
            f"{int(row['trade_count']):>7}{float(row['avg_r']):>9.3f}"
            f"{float(row['profit_factor']):>8.2f}{float(row['win_rate']):>8.2%}"
            f"{float(row['percent_trade_retention_vs_baseline']):>9.2%}"
        )
    print()
    print("Skip impact table")
    print(f"{'var':<5}{'filter':<48}{'loss rm':>8}{'win rm':>8}{'dNetR':>9}{'retain':>9}")
    for row in result["skip_impact"]:
        print(
            f"{row['variant']:<5}{str(row['context_filter'])[:47]:<48}"
            f"{int(row['executed_losing_trades_removed']):>8}{int(row['executed_winning_trades_removed']):>8}"
            f"{float(row['net_r_improvement']):>9.3f}{float(row['trade_retention']):>9.2%}"
        )
    print()
    print("Exit by context table")
    print(f"{'var':<5}{'exit':<14}{'trades':>7}{'avgR':>9}{'PF':>8}{'netR':>9}")
    for row in result["exit_by_context"]:
        print(
            f"{row['variant']:<5}{row['exit_name']:<14}{int(row['trade_count']):>7}"
            f"{float(row['avg_r']):>9.3f}{float(row['profit_factor']):>8.2f}{float(row['net_r']):>9.3f}"
        )
    print()
    print("Direction availability audit")
    for row in result["direction_availability"]:
        print(
            f"  {row['metric']:<20} BUY={int(row['buy_count']):>4} "
            f"SELL={int(row['sell_count']):>4} UNKNOWN={int(row['unknown_count']):>4} "
            f"{row['assessment']}"
        )
    print()
    print(f"Research decision: {summary['research_decision']}")
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
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return str(value)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AURUM-1 Phase S2 shadow context-filter simulation.")
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--as-of", default=None, help="Optional UTC timestamp to stamp the summary.")
    return parser.parse_args(argv)

