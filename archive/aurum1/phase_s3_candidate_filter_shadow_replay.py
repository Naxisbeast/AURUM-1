"""Phase S3 candidate-filter shadow replay for AURUM-1.

This module is diagnostic only. It replays the logged raw Donchian shadow signal
stream as candidate TAKE/HOLD decisions, applies fixed context filters, and
simulates fixed exits from logged candles. It does not import broker/execution
code, place orders, change thresholds, change timers, or mutate live/paper
trading behavior.
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
NORMAL_VOLATILITY_LABELS = {"normal", "medium"}
MIN_CONFIRMATION_TRADES = 20

S3_RESEARCH_DECISIONS = {
    "FILTER_REPLAY_PROMISING",
    "FILTER_REPLAY_PROMISING_SAMPLE_LIMITED",
    "VOLATILITY_FILTER_CONFIRMED",
    "SESSION_FILTER_CONFIRMED",
    "VOLATILITY_SESSION_FILTER_CONFIRMED",
    "SHORT_SIDE_MISSING",
    "CONTINUE_COLLECTING_SAMPLE",
    "NO_FILTER_REPLAY_EDGE",
}

DECISION_FIELDS = [
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

METRIC_FIELDS = [
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

DIRECTION_FIELDS = [
    "metric",
    "buy_count",
    "sell_count",
    "unknown_count",
    "assessment",
    "config_direction",
    "config_strategy",
    "strategy_implies_long_only",
    "notes",
]

DRAWDOWN_FIELDS = [
    "section",
    "variant",
    "simulated_exit_model",
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


def run_phase_s3_replay(
    shadow_db: Path | str = DEFAULT_SHADOW_DB,
    report_dir: Path | str = DEFAULT_REPORT_DIR,
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Run Phase S3 and write all requested artifacts."""

    db_path = resolve_path(shadow_db)
    output_dir = resolve_path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ensure_s1_inputs(db_path, output_dir)

    trade_audit = load_audit_csv(output_dir / "phase_s1_trade_audit.csv")
    skipped_audit = load_audit_csv(output_dir / "phase_s1_skipped_signal_audit.csv")
    s2_variants = load_audit_csv(output_dir / "phase_s2_variant_comparison.csv")
    s2_direction = load_audit_csv(output_dir / "phase_s2_direction_availability.csv")

    shadow = load_shadow_data(db_path)
    signals = normalize_signals(shadow["signals"])
    trades = normalize_trades(shadow["trades"])
    candles = normalize_candles(shadow["candles"])
    config = shadow["config"]

    contexts = build_context_map(signals, trade_audit, skipped_audit)
    baseline_outcomes = build_baseline_outcomes(trade_audit)
    signal_records = build_signal_records(signals, contexts, baseline_outcomes, config)
    decisions = build_replay_decisions(signal_records, candles)
    metrics = build_variant_metrics(decisions, baseline_outcomes)
    direction_rows, direction_summary = build_direction_audit(signals, trades, trade_audit, skipped_audit, config)
    drawdown_rows = build_drawdown_attribution(decisions, metrics)
    decision, reason, warnings = choose_research_decision(metrics, direction_summary)
    if direction_summary["sell_raw_signals"] == 0 and "SHORT_SIDE_MISSING" not in warnings:
        warnings.append("SHORT_SIDE_MISSING")

    best = best_variant(metrics)
    summary = {
        "generated_at": utc_now_or_as_of(as_of),
        "phase": "S3",
        "name": "Candidate Filter Shadow Replay",
        "classification": "research-only",
        "shadow_db": str(db_path),
        "report_dir": str(output_dir),
        "raw_signal_count": len(signal_records),
        "variant_count": len(metrics),
        "best_variant": best,
        "research_decision": decision,
        "research_decision_reason": reason,
        "warnings": warnings,
        "direction_audit": direction_summary,
        "s2_variant_comparison_loaded": not s2_variants.empty,
        "s2_direction_availability_loaded": not s2_direction.empty,
        "safety": {
            "orders_placed": False,
            "execution_logic_modified": False,
            "live_or_paper_behavior_modified": False,
            "strategy_thresholds_modified": False,
            "timers_modified": False,
            "sell_execution_enabled": False,
            "forward_shadow_runner_modified": False,
            "sqlite_read_mode": "query_only",
        },
        "replay_scope": {
            "baseline_current_behavior": "TAKE only closed trades already taken by current shadow behavior; HOLD current skips/open/unknown.",
            "candidate_filter_variants": "TAKE any raw shadow signal whose fixed context predicate passes; otherwise HOLD.",
            "normal_volatility_labels": sorted(NORMAL_VOLATILITY_LABELS),
            "exit_models": ["fixed_1r", "fixed_1_5r", "fixed_2r"],
        },
    }

    paths = {
        "summary_json": output_dir / "phase_s3_candidate_filter_summary.json",
        "replay_decisions_csv": output_dir / "phase_s3_replay_decisions.csv",
        "variant_metrics_csv": output_dir / "phase_s3_variant_metrics.csv",
        "direction_audit_csv": output_dir / "phase_s3_direction_audit.csv",
        "drawdown_attribution_csv": output_dir / "phase_s3_drawdown_attribution.csv",
    }
    paths["summary_json"].write_text(json.dumps(summary, indent=2, sort_keys=True, default=json_default), encoding="utf-8")
    write_csv(paths["replay_decisions_csv"], decisions, DECISION_FIELDS)
    write_csv(paths["variant_metrics_csv"], metrics, METRIC_FIELDS)
    write_csv(paths["direction_audit_csv"], direction_rows, DIRECTION_FIELDS)
    write_csv(paths["drawdown_attribution_csv"], drawdown_rows, DRAWDOWN_FIELDS)

    return {
        "summary": summary,
        "replay_decisions": decisions,
        "variant_metrics": metrics,
        "direction_audit": direction_rows,
        "drawdown_attribution": drawdown_rows,
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


def build_context_map(signals: pd.DataFrame, trade_audit: pd.DataFrame, skipped_audit: pd.DataFrame) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    if not trade_audit.empty:
        for _, row in trade_audit.iterrows():
            signal_id = str(row.get("signal_time") or "")
            if signal_id:
                contexts[signal_id] = {
                    "volatility_regime": normalized_text(row.get("volatility_regime"), "unknown"),
                    "session": normalized_text(row.get("session_label"), "unknown"),
                    "weekday": str(row.get("weekday") or "unknown"),
                    "instrument": str(row.get("instrument") or "XAU_USD"),
                    "direction": str(row.get("direction") or "UNKNOWN").upper(),
                }
    if not skipped_audit.empty:
        for _, row in skipped_audit.iterrows():
            signal_id = str(row.get("signal_time") or "")
            if signal_id:
                contexts[signal_id] = {
                    "volatility_regime": normalized_text(row.get("volatility_regime"), "unknown"),
                    "session": normalized_text(row.get("session", row.get("session_label")), "unknown"),
                    "weekday": str(row.get("weekday") or "unknown"),
                    "instrument": str(row.get("instrument") or "XAU_USD"),
                    "direction": str(row.get("direction") or "UNKNOWN").upper(),
                }

    if signals.empty:
        return contexts
    missing = [row for _, row in signals.iterrows() if str(row.get("signal_time") or "") not in contexts]
    atr_values = [safe_float(row.get("atr")) for row in missing]
    clean_atr = [value for value in atr_values if value is not None]
    low_q = float(pd.Series(clean_atr).quantile(0.33)) if len(clean_atr) >= 3 else None
    high_q = float(pd.Series(clean_atr).quantile(0.66)) if len(clean_atr) >= 3 else None
    for row in missing:
        signal_id = str(row.get("signal_time") or "")
        ts = pd.to_datetime(row.get("signal_time"), utc=True, errors="coerce")
        contexts[signal_id] = {
            "volatility_regime": atr_volatility_bucket(safe_float(row.get("atr")), low_q, high_q),
            "session": session_label(ts),
            "weekday": ts.day_name() if not pd.isna(ts) else "unknown",
            "instrument": "XAU_USD",
            "direction": str(row.get("direction") or "UNKNOWN").upper(),
        }
    return contexts


def build_baseline_outcomes(trade_audit: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if trade_audit.empty:
        return {}
    outcomes: dict[str, dict[str, Any]] = {}
    for _, row in trade_audit.iterrows():
        signal_id = str(row.get("signal_time") or "")
        r_value = safe_float(row.get("realized_r"))
        if not signal_id or r_value is None:
            continue
        outcomes[signal_id] = {
            "r": r_value,
            "outcome": "win" if r_value > 0.0 else "loss",
            "bars_held": safe_float(row.get("holding_bars")),
            "pnl": safe_float(row.get("realized_pnl")),
        }
    return outcomes


def build_signal_records(
    signals: pd.DataFrame,
    contexts: dict[str, dict[str, Any]],
    baseline_outcomes: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    instrument = str(config.get("instrument") or "XAU_USD")
    for _, signal in signals.iterrows():
        signal_id = str(signal.get("signal_time") or "")
        context = contexts.get(signal_id, {})
        records.append(
            {
                "raw_signal_id": signal_id,
                "timestamp": signal_id,
                "entry_time": str(signal.get("entry_time") or ""),
                "instrument": str(context.get("instrument") or instrument),
                "direction": str(signal.get("direction") or context.get("direction") or "UNKNOWN").upper(),
                "original_status": str(signal.get("status") or "unknown"),
                "skip_reason": str(signal.get("skip_reason") or ""),
                "volatility_regime": normalized_text(context.get("volatility_regime"), "unknown"),
                "session": normalized_text(context.get("session"), "unknown"),
                "weekday": str(context.get("weekday") or "unknown"),
                "entry": safe_float(signal.get("entry_price")),
                "stop": safe_float(signal.get("stop_loss")),
                "target": safe_float(signal.get("take_profit")),
                "signal": signal,
                "baseline": baseline_outcomes.get(signal_id),
            }
        )
    return sorted(records, key=lambda item: item["timestamp"])


def build_replay_decisions(signal_records: list[dict[str, Any]], candles: pd.DataFrame) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for definition in replay_definitions():
        for record in signal_records:
            if definition["mode"] == "current":
                decision, blocked_reason = current_decision(record)
                sim_r = record["baseline"]["r"] if decision == "TAKE" and record.get("baseline") else None
                sim_outcome = record["baseline"]["outcome"] if decision == "TAKE" and record.get("baseline") else ""
                bars_held = record["baseline"].get("bars_held") if decision == "TAKE" and record.get("baseline") else None
            elif definition["mode"] == "baseline_fixed":
                decision, blocked_reason = current_decision(record)
                sim = simulate_signal_exit(record["signal"], candles, target_r=definition["target_r"], exit_mode="fixed", close_at_end=True)
                sim_r = sim.r_multiple
                sim_outcome = sim.outcome
                bars_held = sim.holding_bars
            else:
                passes, blocked_reason = definition["predicate"](record)
                decision = "TAKE" if passes else "HOLD"
                sim = simulate_signal_exit(record["signal"], candles, target_r=definition["target_r"], exit_mode="fixed", close_at_end=True)
                sim_r = sim.r_multiple
                sim_outcome = sim.outcome
                bars_held = sim.holding_bars
            decisions.append(
                {
                    "variant": definition["variant"],
                    "variant_name": definition["variant_name"],
                    "context_filter": definition["context_filter"],
                    "timestamp": record["timestamp"],
                    "instrument": record["instrument"],
                    "direction": record["direction"],
                    "raw_signal_id": record["raw_signal_id"],
                    "original_status": record["original_status"],
                    "decision": decision,
                    "blocked_reason": "" if decision == "TAKE" else blocked_reason,
                    "volatility_regime": record["volatility_regime"],
                    "session": record["session"],
                    "weekday": record["weekday"],
                    "entry": record["entry"],
                    "stop": record["stop"],
                    "target": record["target"],
                    "simulated_exit_model": definition["exit_model"],
                    "simulated_r": sim_r,
                    "simulated_outcome": sim_outcome if decision == "TAKE" else sim_outcome,
                    "bars_held": bars_held,
                }
            )
    return decisions


def replay_definitions() -> list[dict[str, Any]]:
    base_contexts = [
        ("VOL_NOT_HIGH", "TAKE only if volatility_regime != high", "volatility_regime != high", not_high),
        ("NOT_LONDON", "TAKE only if session != london", "session != london", not_london),
        (
            "VOL_NOT_HIGH_AND_NOT_LONDON",
            "TAKE only if volatility_regime != high AND session != london",
            "volatility_regime != high AND session != london",
            not_high_and_not_london,
        ),
        ("NORMAL_ONLY", "TAKE only if volatility_regime == normal", "volatility_regime in normal/medium", normal_only),
        (
            "NORMAL_AND_NOT_LONDON",
            "TAKE only if volatility_regime == normal AND session != london",
            "volatility_regime in normal/medium AND session != london",
            normal_and_not_london,
        ),
    ]
    definitions: list[dict[str, Any]] = [
        {
            "variant": "BASELINE_CURRENT",
            "variant_name": "Baseline current behavior",
            "context_filter": "current shadow behavior",
            "exit_model": "current_realized",
            "target_r": 2.0,
            "mode": "current",
            "predicate": lambda record: (True, ""),
        }
    ]
    for exit_model, target_r in (("fixed_1r", 1.0), ("fixed_1_5r", 1.5), ("fixed_2r", 2.0)):
        definitions.append(
            {
                "variant": f"BASELINE_CURRENT_{exit_model.upper()}",
                "variant_name": f"Baseline current behavior / {exit_model}",
                "context_filter": "current shadow behavior",
                "exit_model": exit_model,
                "target_r": target_r,
                "mode": "baseline_fixed",
                "predicate": lambda record: (True, ""),
            }
        )
    for prefix, name, context_filter, predicate in base_contexts:
        for exit_model, target_r in (("fixed_1r", 1.0), ("fixed_1_5r", 1.5), ("fixed_2r", 2.0)):
            definitions.append(
                {
                    "variant": f"{prefix}_{exit_model.upper()}",
                    "variant_name": f"{name} / {exit_model}",
                    "context_filter": context_filter,
                    "exit_model": exit_model,
                    "target_r": target_r,
                    "mode": "candidate",
                    "predicate": predicate,
                }
            )
    return definitions


def current_decision(record: dict[str, Any]) -> tuple[str, str]:
    if record.get("baseline") is not None:
        return "TAKE", ""
    status = str(record.get("original_status") or "").lower()
    if status == "skipped":
        return "HOLD", str(record.get("skip_reason") or "current_shadow_skip")
    return "HOLD", "not_closed_current_trade"


def not_high(record: dict[str, Any]) -> tuple[bool, str]:
    return context_bool(str(record.get("volatility_regime")) != "high", "volatility_high")


def not_london(record: dict[str, Any]) -> tuple[bool, str]:
    return context_bool(str(record.get("session")) != "london", "session_london")


def not_high_and_not_london(record: dict[str, Any]) -> tuple[bool, str]:
    blocked = []
    if str(record.get("volatility_regime")) == "high":
        blocked.append("volatility_high")
    if str(record.get("session")) == "london":
        blocked.append("session_london")
    return (not blocked, "+".join(blocked))


def normal_only(record: dict[str, Any]) -> tuple[bool, str]:
    return context_bool(str(record.get("volatility_regime")) in NORMAL_VOLATILITY_LABELS, "volatility_not_normal")


def normal_and_not_london(record: dict[str, Any]) -> tuple[bool, str]:
    blocked = []
    if str(record.get("volatility_regime")) not in NORMAL_VOLATILITY_LABELS:
        blocked.append("volatility_not_normal")
    if str(record.get("session")) == "london":
        blocked.append("session_london")
    return (not blocked, "+".join(blocked))


def context_bool(value: bool, reason: str) -> tuple[bool, str]:
    return (True, "") if value else (False, reason)


def build_variant_metrics(decisions: list[dict[str, Any]], baseline_outcomes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    raw_signal_count = len({row["raw_signal_id"] for row in decisions})
    baseline_rows = [row for row in decisions if row["variant"] == "BASELINE_CURRENT" and row["decision"] == "TAKE"]
    baseline_net_r = sum(numeric_values(row.get("simulated_r") for row in baseline_rows))
    baseline_take_ids = {row["raw_signal_id"] for row in baseline_rows}
    baseline_loser_ids = {key for key, value in baseline_outcomes.items() if safe_float(value.get("r")) is not None and float(value["r"]) <= 0.0}
    baseline_winner_ids = {key for key, value in baseline_outcomes.items() if safe_float(value.get("r")) is not None and float(value["r"]) > 0.0}

    rows: list[dict[str, Any]] = []
    for variant in sorted({row["variant"] for row in decisions}, key=variant_sort_key):
        subset = [row for row in decisions if row["variant"] == variant]
        take_rows = [row for row in subset if row["decision"] == "TAKE"]
        r_values = numeric_values(row.get("simulated_r") for row in take_rows)
        take_ids = {row["raw_signal_id"] for row in take_rows}
        removed_baseline_ids = baseline_take_ids.difference(take_ids)
        rows.append(
            {
                "variant": variant,
                "variant_name": subset[0]["variant_name"] if subset else "",
                "context_filter": subset[0]["context_filter"] if subset else "",
                "simulated_exit_model": subset[0]["simulated_exit_model"] if subset else "",
                "raw_signal_count": raw_signal_count,
                "take_count": len(take_rows),
                "hold_count": len(subset) - len(take_rows),
                "trade_retention_percent": pct(len(take_rows), raw_signal_count),
                "win_count": sum(1 for value in r_values if value > 0.0),
                "loss_count": sum(1 for value in r_values if value <= 0.0),
                "win_rate": pct(sum(1 for value in r_values if value > 0.0), len(r_values)),
                "avg_r": mean(r_values),
                "median_r": median(r_values),
                "net_r": sum(r_values),
                "profit_factor": profit_factor(r_values),
                "max_drawdown_r": max_drawdown_r(r_values),
                "max_consecutive_losses": max_consecutive_losses(r_values),
                "removed_losers": len(removed_baseline_ids.intersection(baseline_loser_ids)),
                "removed_winners": len(removed_baseline_ids.intersection(baseline_winner_ids)),
                "net_filter_improvement_r": sum(r_values) - baseline_net_r,
            }
        )
    return rows


def build_direction_audit(
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    trade_audit: pd.DataFrame,
    skipped_audit: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config_direction = str(config.get("direction", "unknown"))
    config_strategy = str(config.get("strategy", "unknown"))
    long_only = "BUY_ONLY" in config_direction.upper() or "long" in config_strategy.lower()
    signal_counts = direction_counts(signals.get("direction", []))
    trade_counts = direction_counts(trade_audit.get("direction", []) if not trade_audit.empty else trades.get("direction", []))
    skipped_counts = direction_counts(skipped_audit.get("direction", []))
    assessment = "SHORT_SIDE_MISSING" if signal_counts["SELL"] == 0 else "SELL_SIDE_PRESENT"
    if signal_counts["SELL"] == 0 and long_only:
        notes = "No SELL raw signals found; config/strategy indicates long-only or BUY-only behavior."
    elif signal_counts["SELL"] == 0:
        notes = "No SELL raw signals found; investigate whether SELL side is disabled or absent."
    else:
        notes = "SELL raw signals exist in the shadow signal ledger."
    rows = [
        direction_row("raw_signals", signal_counts, assessment, config_direction, config_strategy, long_only, notes),
        direction_row("executed_trades", trade_counts, assessment, config_direction, config_strategy, long_only, "Closed S1 trade audit direction counts."),
        direction_row("skipped_signals", skipped_counts, assessment, config_direction, config_strategy, long_only, "S1 skipped signal direction counts."),
    ]
    return rows, {
        "buy_raw_signals": signal_counts["BUY"],
        "sell_raw_signals": signal_counts["SELL"],
        "buy_trades": trade_counts["BUY"],
        "sell_trades": trade_counts["SELL"],
        "buy_skipped": skipped_counts["BUY"],
        "sell_skipped": skipped_counts["SELL"],
        "assessment": assessment,
        "config_direction": config_direction,
        "config_strategy": config_strategy,
        "strategy_implies_long_only": long_only,
    }


def direction_row(
    metric: str,
    counts: Counter[str],
    assessment: str,
    config_direction: str,
    config_strategy: str,
    long_only: bool,
    notes: str,
) -> dict[str, Any]:
    return {
        "metric": metric,
        "buy_count": counts["BUY"],
        "sell_count": counts["SELL"],
        "unknown_count": counts["UNKNOWN"],
        "assessment": assessment,
        "config_direction": config_direction,
        "config_strategy": config_strategy,
        "strategy_implies_long_only": long_only,
        "notes": notes,
    }


def build_drawdown_attribution(decisions: list[dict[str, Any]], metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    metric_by_variant = {row["variant"]: row for row in metrics}
    for metric in metrics:
        rows.append(
            {
                "section": "variant_drawdown",
                "variant": metric["variant"],
                "simulated_exit_model": metric["simulated_exit_model"],
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
                "note": "Variant-level drawdown from TAKE sequence.",
            }
        )
    best = best_variant(metrics)
    if not best:
        return rows
    take_rows = [row for row in decisions if row["variant"] == best["variant"] and row["decision"] == "TAKE"]
    curve = drawdown_curve(take_rows)
    worst = sorted(curve, key=lambda row: float(row["drawdown_r"]))[:10]
    metric = metric_by_variant.get(best["variant"], {})
    for rank, row in enumerate(worst, start=1):
        rows.append(
            {
                "section": "best_variant_worst_drawdown_points",
                "variant": best["variant"],
                "simulated_exit_model": best.get("simulated_exit_model", ""),
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
                "note": "Worst drawdown points for the best replay variant.",
            }
        )
    return rows


def choose_research_decision(metrics: list[dict[str, Any]], direction_summary: dict[str, Any]) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    if direction_summary["sell_raw_signals"] == 0:
        warnings.append("SHORT_SIDE_MISSING")
    non_baseline = [row for row in metrics if row["variant"] != "BASELINE_CURRENT"]
    promising = [
        row
        for row in non_baseline
        if float(row.get("profit_factor") or 0.0) > 1.0 and float(row.get("avg_r") or 0.0) > 0.0
    ]
    if promising:
        best = max(promising, key=lambda row: (float(row["profit_factor"]), float(row["avg_r"]), float(row["net_r"])))
        if int(best.get("take_count") or 0) < MIN_CONFIRMATION_TRADES:
            return (
                "FILTER_REPLAY_PROMISING_SAMPLE_LIMITED",
                f"{best['variant']} has PF {float(best['profit_factor']):.2f} and avgR {float(best['avg_r']):.3f}, but only {int(best['take_count'])} TAKE decisions.",
                warnings,
            )
        if best["variant"].startswith(("VOL_NOT_HIGH_AND_NOT_LONDON", "NORMAL_AND_NOT_LONDON")):
            return "VOLATILITY_SESSION_FILTER_CONFIRMED", f"{best['variant']} confirms a combined volatility/session replay edge.", warnings
        if best["variant"].startswith(("VOL_NOT_HIGH", "NORMAL_ONLY")):
            return "VOLATILITY_FILTER_CONFIRMED", f"{best['variant']} confirms a volatility replay edge.", warnings
        if best["variant"].startswith("NOT_LONDON"):
            return "SESSION_FILTER_CONFIRMED", f"{best['variant']} confirms a session replay edge.", warnings
        return "FILTER_REPLAY_PROMISING", f"{best['variant']} is promising.", warnings

    if metrics and all(int(row.get("take_count") or 0) < MIN_CONFIRMATION_TRADES for row in metrics):
        return "CONTINUE_COLLECTING_SAMPLE", "All replay variants remain below the confirmation sample threshold.", warnings
    if direction_summary["sell_raw_signals"] == 0:
        return "SHORT_SIDE_MISSING", "No SELL raw signals exist in the shadow signal ledger.", warnings
    return "NO_FILTER_REPLAY_EDGE", "No candidate filter replay produced positive avgR with PF above 1.0.", warnings


def best_variant(metrics: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [row for row in metrics if row["variant"] != "BASELINE_CURRENT" and int(row.get("take_count") or 0) > 0]
    if not candidates:
        return None
    return max(candidates, key=lambda row: (float(row.get("profit_factor") or 0.0), float(row.get("avg_r") or 0.0), float(row.get("net_r") or 0.0)))


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


def normalized_text(value: Any, default: str) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    text = str(value).strip().lower()
    return text if text else default


def atr_volatility_bucket(value: float | None, low_q: float | None, high_q: float | None) -> str:
    if value is None or low_q is None or high_q is None:
        return "unknown"
    if value <= low_q:
        return "low"
    if value >= high_q:
        return "high"
    return "medium"


def session_label(ts: pd.Timestamp) -> str:
    if pd.isna(ts):
        return "unknown"
    hour = int(ts.hour)
    if 0 <= hour < 7:
        return "asia"
    if 7 <= hour < 12:
        return "london"
    if 12 <= hour < 16:
        return "london_ny_overlap"
    if 16 <= hour < 21:
        return "new_york"
    return "rollover"


def numeric_values(values: Any) -> list[float]:
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


def variant_sort_key(value: str) -> tuple[int, str]:
    if value == "BASELINE_CURRENT":
        return (0, value)
    if value.startswith("BASELINE_CURRENT_"):
        return (1, value)
    return (2, value)


def print_phase_s3_report(result: dict[str, Any]) -> None:
    summary = result["summary"]
    print("AURUM-1 Phase S3 Candidate Filter Shadow Replay")
    print("=" * 84)
    print("Candidate replay variant table")
    print(f"{'variant':<38}{'exit':<14}{'take':>6}{'hold':>6}{'avgR':>9}{'PF':>8}{'netR':>9}")
    for row in result["variant_metrics"]:
        print(
            f"{str(row['variant'])[:37]:<38}{row['simulated_exit_model']:<14}"
            f"{int(row['take_count']):>6}{int(row['hold_count']):>6}"
            f"{float(row['avg_r']):>9.3f}{float(row['profit_factor']):>8.2f}{float(row['net_r']):>9.3f}"
        )
    print()
    print("Best variant")
    best = summary.get("best_variant")
    if best:
        print(
            f"  {best['variant']} exit={best['simulated_exit_model']} "
            f"take={best['take_count']} avgR={float(best['avg_r']):.3f} PF={float(best['profit_factor']):.2f}"
        )
    else:
        print("  none")
    print()
    print("Direction audit")
    for row in result["direction_audit"]:
        print(
            f"  {row['metric']:<16} BUY={int(row['buy_count']):>4} "
            f"SELL={int(row['sell_count']):>4} UNKNOWN={int(row['unknown_count']):>4} "
            f"{row['assessment']}"
        )
    print()
    print("Drawdown attribution")
    variant_rows = [row for row in result["drawdown_attribution"] if row["section"] == "variant_drawdown"]
    worst = min(variant_rows, key=lambda row: float(row.get("max_drawdown_r") or 0.0), default=None)
    if worst:
        print(
            f"  Worst variant DD: {worst['variant']} {worst['simulated_exit_model']} "
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
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return str(value)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AURUM-1 Phase S3 candidate filter shadow replay.")
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--as-of", default=None, help="Optional UTC timestamp to stamp the summary.")
    return parser.parse_args(argv)
