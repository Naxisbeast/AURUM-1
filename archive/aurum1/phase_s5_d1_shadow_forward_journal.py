"""Phase S5 D1 shadow forward journal for AURUM-1.

This module runs the locked D1 candidate as a diagnostic-only shadow journal
beside current AURUM behavior. It reads the forward-shadow ledger, emits D1
TAKE/HOLD decisions, updates fixed-1R outcomes from logged candles, and writes
only S5 journal/report artifacts. It does not modify execution, timers, SELL
execution, strategy thresholds, or the current raw Donchian service.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from aurum1.reports.phase_s1_forward_shadow_failure_audit import (
    DEFAULT_REPORT_DIR,
    DEFAULT_SHADOW_DB,
    load_shadow_data,
    normalize_candles,
    normalize_signals,
    safe_float,
    write_csv,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAX_HOLDING_CANDLES = 20
EXECUTION_STATUS = "diagnostic_shadow_only_no_order_sent"
LOGGER = logging.getLogger("aurum1.phase_s5_d1_shadow")

JOURNAL_FIELDS = [
    "logged_at_utc",
    "signal_timestamp",
    "raw_signal_id",
    "instrument",
    "timeframe",
    "direction",
    "d1_decision",
    "blocked_reason",
    "volatility_regime",
    "session",
    "weekday",
    "entry",
    "stop",
    "target_1r",
    "risk_distance",
    "exit_model",
    "outcome_status",
    "realized_r",
    "bars_held",
    "outcome_timestamp",
    "duplicate_skipped",
    "execution_status",
]

STATE_DEFAULT = {
    "last_processed_signal_id": None,
    "notified_or_logged_signal_ids": [],
    "total_logged": 0,
    "total_duplicates_skipped": 0,
    "total_take": 0,
    "total_hold": 0,
    "total_outcomes_updated": 0,
}

RESEARCH_DECISIONS = {
    "PASS_D1_SHADOW_JOURNAL_READY",
    "NO_NEW_SIGNALS",
    "NEEDS_MORE_DATA",
    "FAIL_D1_SHADOW_JOURNAL",
}


def run_phase_s5_journal(
    shadow_db: Path | str = DEFAULT_SHADOW_DB,
    report_dir: Path | str = DEFAULT_REPORT_DIR,
    *,
    update_outcomes: bool = True,
    dry_run: bool = False,
    max_holding_candles: int = DEFAULT_MAX_HOLDING_CANDLES,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Run the D1 shadow journal update."""

    db_path = resolve_path(shadow_db)
    output_dir = resolve_path(report_dir)
    paths = output_paths(output_dir)
    logged_at = utc_now_or_as_of(as_of)
    if not dry_run:
        setup_logging()

    shadow = load_shadow_data(db_path)
    signals = normalize_signals(shadow["signals"])
    candles = normalize_candles(shadow["candles"])
    config = shadow["config"]

    state = load_state(paths["state_json"])
    existing_rows = load_journal(paths["journal_csv"])
    contexts = build_context_map(signals, output_dir, config)

    latest_signal_ts = latest_signal_timestamp(signals)
    if signals.empty:
        summary = build_summary(
            db_path,
            output_dir,
            latest_signal_ts,
            [],
            existing_rows,
            state,
            duplicate_count=0,
            outcome_update_count=0,
            research_decision="NEEDS_MORE_DATA",
            dry_run=dry_run,
        )
        if not dry_run:
            write_outputs(paths, existing_rows, state, summary)
        return result_payload(summary, [], existing_rows, paths)

    state_ids = set(str(value) for value in state.get("notified_or_logged_signal_ids", []))
    existing_ids = {str(row.get("raw_signal_id")) for row in existing_rows}
    processed_ids = state_ids.union(existing_ids)

    duplicate_count = 0
    new_rows: list[dict[str, Any]] = []
    for _, signal in signals.sort_values("_signal_time").iterrows():
        signal_id = str(signal.get("signal_time") or "")
        if not signal_id:
            continue
        if signal_id in processed_ids:
            duplicate_count += 1
            continue
        context = contexts.get(signal_id, {})
        new_rows.append(build_decision_row(signal, context, config, candles, logged_at, max_holding_candles, update_outcomes))

    journal_rows = [*existing_rows, *new_rows]
    outcome_update_count = 0
    if update_outcomes:
        journal_rows, outcome_update_count = update_existing_outcomes(journal_rows, signals, candles, max_holding_candles)

    updated_state = update_state(state, journal_rows, duplicate_count, outcome_update_count)
    research_decision = "NO_NEW_SIGNALS" if not new_rows else "PASS_D1_SHADOW_JOURNAL_READY"
    summary = build_summary(
        db_path,
        output_dir,
        latest_signal_ts,
        new_rows,
        journal_rows,
        updated_state,
        duplicate_count=duplicate_count,
        outcome_update_count=outcome_update_count,
        research_decision=research_decision,
        dry_run=dry_run,
    )

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_outputs(paths, journal_rows, updated_state, summary)
        LOGGER.info(
            "phase_s5_d1_shadow_journal updated new_rows=%s duplicates=%s outcomes=%s decision=%s",
            len(new_rows),
            duplicate_count,
            outcome_update_count,
            research_decision,
        )

    return result_payload(summary, new_rows, journal_rows, paths)


def resolve_path(path: Path | str) -> Path:
    candidate = Path(path)
    return ROOT / candidate if not candidate.is_absolute() else candidate


def output_paths(report_dir: Path) -> dict[str, Path]:
    return {
        "journal_csv": report_dir / "phase_s5_d1_shadow_journal.csv",
        "journal_jsonl": report_dir / "phase_s5_d1_shadow_journal.jsonl",
        "state_json": report_dir / "phase_s5_d1_shadow_state.json",
        "summary_json": report_dir / "phase_s5_d1_shadow_summary.json",
        "log_file": ROOT / "logs" / "aurum1" / "phase_s5_d1_shadow.log",
    }


def setup_logging() -> None:
    log_dir = ROOT / "logs" / "aurum1"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "phase_s5_d1_shadow.log"
    if LOGGER.handlers:
        return
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    LOGGER.setLevel(logging.INFO)
    LOGGER.addHandler(handler)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(STATE_DEFAULT))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return json.loads(json.dumps(STATE_DEFAULT))
    state = json.loads(json.dumps(STATE_DEFAULT))
    state.update(raw if isinstance(raw, dict) else {})
    if not isinstance(state.get("notified_or_logged_signal_ids"), list):
        state["notified_or_logged_signal_ids"] = []
    return state


def load_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_outputs(paths: dict[str, Path], journal_rows: list[dict[str, Any]], state: dict[str, Any], summary: dict[str, Any]) -> None:
    paths["journal_csv"].parent.mkdir(parents=True, exist_ok=True)
    write_csv(paths["journal_csv"], journal_rows, JOURNAL_FIELDS)
    with paths["journal_jsonl"].open("w", encoding="utf-8") as handle:
        for row in journal_rows:
            handle.write(json.dumps(row, sort_keys=True, default=json_default) + "\n")
    paths["state_json"].write_text(json.dumps(state, indent=2, sort_keys=True, default=json_default), encoding="utf-8")
    paths["summary_json"].write_text(json.dumps(summary, indent=2, sort_keys=True, default=json_default), encoding="utf-8")


def build_context_map(signals: pd.DataFrame, report_dir: Path, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    s4_path = report_dir / "phase_s4_candidate_decisions.csv"
    if s4_path.exists():
        for row in load_csv_dicts(s4_path):
            if str(row.get("candidate_name")) != "D1":
                continue
            signal_id = str(row.get("raw_signal_id") or "")
            if signal_id:
                contexts[signal_id] = context_from_row(row, session_key="session")
    for csv_name, session_key in (("phase_s1_trade_audit.csv", "session_label"), ("phase_s1_skipped_signal_audit.csv", "session")):
        path = report_dir / csv_name
        if not path.exists():
            continue
        for row in load_csv_dicts(path):
            signal_id = str(row.get("signal_time") or "")
            if signal_id and signal_id not in contexts:
                contexts[signal_id] = context_from_row(row, session_key=session_key)

    missing = [row for _, row in signals.iterrows() if str(row.get("signal_time") or "") not in contexts]
    atr_values = [safe_float(row.get("atr")) for row in missing]
    clean_atr = [value for value in atr_values if value is not None]
    high_q = float(pd.Series(clean_atr).quantile(0.66)) if len(clean_atr) >= 3 else None
    instrument = str(config.get("instrument") or "XAU_USD")
    for row in missing:
        signal_id = str(row.get("signal_time") or "")
        ts = pd.to_datetime(signal_id, utc=True, errors="coerce")
        contexts[signal_id] = {
            "instrument": instrument,
            "timeframe": "M15",
            "direction": str(row.get("direction") or "UNKNOWN").upper(),
            "volatility_regime": fallback_volatility(safe_float(row.get("atr")), high_q),
            "session": session_label(ts),
            "weekday": ts.day_name() if not pd.isna(ts) else "",
        }
    return contexts


def load_csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def context_from_row(row: dict[str, Any], *, session_key: str) -> dict[str, Any]:
    return {
        "instrument": str(row.get("instrument") or "XAU_USD"),
        "timeframe": str(row.get("timeframe") or "M15"),
        "direction": str(row.get("direction") or "UNKNOWN").upper(),
        "volatility_regime": normalized_text(row.get("volatility_regime")),
        "session": normalized_text(row.get(session_key)),
        "weekday": str(row.get("weekday") or ""),
    }


def build_decision_row(
    signal: pd.Series,
    context: dict[str, Any],
    config: dict[str, Any],
    candles: pd.DataFrame,
    logged_at: str,
    max_holding_candles: int,
    update_outcomes: bool,
) -> dict[str, Any]:
    direction = str(signal.get("direction") or context.get("direction") or "UNKNOWN").upper()
    entry = safe_float(signal.get("entry_price"))
    stop = safe_float(signal.get("stop_loss"))
    risk_distance = entry - stop if entry is not None and stop is not None else None
    target_1r = entry + risk_distance if entry is not None and risk_distance is not None else None
    decision, blocked_reason = d1_decision(direction, context, entry, stop, risk_distance)
    if decision == "TAKE" and update_outcomes:
        outcome = simulate_fixed_1r_outcome(signal, candles, max_holding_candles)
    elif decision == "TAKE":
        outcome = {"outcome_status": "unresolved", "realized_r": None, "bars_held": None, "outcome_timestamp": ""}
    else:
        outcome = {"outcome_status": "unresolved", "realized_r": None, "bars_held": None, "outcome_timestamp": ""}
    return {
        "logged_at_utc": logged_at,
        "signal_timestamp": str(signal.get("signal_time") or ""),
        "raw_signal_id": str(signal.get("signal_time") or ""),
        "instrument": str(context.get("instrument") or config.get("instrument") or "XAU_USD"),
        "timeframe": str(context.get("timeframe") or "M15"),
        "direction": direction,
        "d1_decision": decision,
        "blocked_reason": blocked_reason,
        "volatility_regime": context.get("volatility_regime") or "",
        "session": context.get("session") or "",
        "weekday": context.get("weekday") or "",
        "entry": entry,
        "stop": stop,
        "target_1r": target_1r,
        "risk_distance": risk_distance,
        "exit_model": "fixed_1r",
        "outcome_status": outcome["outcome_status"],
        "realized_r": outcome["realized_r"],
        "bars_held": outcome["bars_held"],
        "outcome_timestamp": outcome["outcome_timestamp"],
        "duplicate_skipped": False,
        "execution_status": EXECUTION_STATUS,
    }


def d1_decision(
    direction: str,
    context: dict[str, Any],
    entry: float | None,
    stop: float | None,
    risk_distance: float | None,
) -> tuple[str, str]:
    if direction == "SELL":
        return "HOLD", "short_side_not_enabled"
    volatility = normalized_text(context.get("volatility_regime"))
    session = normalized_text(context.get("session"))
    if direction != "BUY" or entry is None or stop is None or risk_distance is None or risk_distance <= 0.0 or not volatility or not session:
        return "HOLD", "missing_required_fields"
    if volatility == "high":
        return "HOLD", "high_volatility"
    if session == "london":
        return "HOLD", "london_session"
    return "TAKE", "none"


def simulate_fixed_1r_outcome(signal: pd.Series, candles: pd.DataFrame, max_holding_candles: int) -> dict[str, Any]:
    entry_ts = pd.to_datetime(signal.get("entry_time"), utc=True, errors="coerce")
    entry = safe_float(signal.get("entry_price"))
    stop = safe_float(signal.get("stop_loss"))
    if pd.isna(entry_ts) or entry is None or stop is None or candles.empty:
        return {"outcome_status": "unresolved", "realized_r": None, "bars_held": None, "outcome_timestamp": ""}
    risk = entry - stop
    if risk <= 0.0:
        return {"outcome_status": "unresolved", "realized_r": None, "bars_held": None, "outcome_timestamp": ""}
    target = entry + risk
    future = candles[candles["_timestamp"] >= entry_ts].copy()
    if future.empty:
        return {"outcome_status": "unresolved", "realized_r": None, "bars_held": None, "outcome_timestamp": ""}
    capped = future.head(max(1, int(max_holding_candles)))
    for offset, (_, candle) in enumerate(capped.iterrows(), start=1):
        low = safe_float(candle.get("low"))
        high = safe_float(candle.get("high"))
        ts = candle["_timestamp"]
        # Same-candle policy: stop_first.
        if low is not None and low <= stop:
            return {"outcome_status": "sl_hit", "realized_r": -1.0, "bars_held": offset, "outcome_timestamp": ts.isoformat()}
        if high is not None and high >= target:
            return {"outcome_status": "tp_hit", "realized_r": 1.0, "bars_held": offset, "outcome_timestamp": ts.isoformat()}
    if len(future) >= max(1, int(max_holding_candles)):
        last = capped.iloc[-1]
        close = safe_float(last.get("close"))
        r_value = (close - entry) / risk if close is not None else None
        return {
            "outcome_status": "expired",
            "realized_r": r_value,
            "bars_held": len(capped),
            "outcome_timestamp": last["_timestamp"].isoformat(),
        }
    return {"outcome_status": "open", "realized_r": None, "bars_held": len(future), "outcome_timestamp": ""}


def update_existing_outcomes(
    journal_rows: list[dict[str, Any]],
    signals: pd.DataFrame,
    candles: pd.DataFrame,
    max_holding_candles: int,
) -> tuple[list[dict[str, Any]], int]:
    if not journal_rows:
        return journal_rows, 0
    signal_map = {str(row.get("signal_time")): row for _, row in signals.iterrows()}
    updated = 0
    output: list[dict[str, Any]] = []
    for row in journal_rows:
        clean = dict(row)
        if clean.get("d1_decision") != "TAKE" or clean.get("outcome_status") in {"tp_hit", "sl_hit", "expired"}:
            output.append(clean)
            continue
        signal = signal_map.get(str(clean.get("raw_signal_id")))
        if signal is None:
            output.append(clean)
            continue
        outcome = simulate_fixed_1r_outcome(signal, candles, max_holding_candles)
        before = (clean.get("outcome_status"), str(clean.get("realized_r") or ""), str(clean.get("outcome_timestamp") or ""))
        clean.update(outcome)
        after = (clean.get("outcome_status"), str(clean.get("realized_r") or ""), str(clean.get("outcome_timestamp") or ""))
        if before != after:
            updated += 1
        output.append(clean)
    return output, updated


def update_state(
    state: dict[str, Any],
    journal_rows: list[dict[str, Any]],
    duplicate_count: int,
    outcome_update_count: int,
) -> dict[str, Any]:
    ids = [str(row.get("raw_signal_id")) for row in journal_rows if row.get("raw_signal_id")]
    updated = json.loads(json.dumps(STATE_DEFAULT))
    updated.update(state)
    updated["notified_or_logged_signal_ids"] = sorted(set(ids))
    updated["last_processed_signal_id"] = ids[-1] if ids else updated.get("last_processed_signal_id")
    updated["total_logged"] = len(journal_rows)
    updated["total_duplicates_skipped"] = int(updated.get("total_duplicates_skipped") or 0) + duplicate_count
    updated["total_take"] = sum(1 for row in journal_rows if row.get("d1_decision") == "TAKE")
    updated["total_hold"] = sum(1 for row in journal_rows if row.get("d1_decision") == "HOLD")
    updated["total_outcomes_updated"] = int(updated.get("total_outcomes_updated") or 0) + outcome_update_count
    return updated


def build_summary(
    shadow_db: Path,
    report_dir: Path,
    latest_signal_ts: str,
    new_rows: list[dict[str, Any]],
    journal_rows: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    duplicate_count: int,
    outcome_update_count: int,
    research_decision: str,
    dry_run: bool,
) -> dict[str, Any]:
    performance = journal_performance(journal_rows)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "phase": "S5",
        "name": "D1 Shadow Forward Journal",
        "classification": "research-only",
        "shadow_db": str(shadow_db),
        "report_dir": str(report_dir),
        "dry_run": dry_run,
        "latest_raw_signal_timestamp": latest_signal_ts,
        "new_decisions_logged": len(new_rows),
        "take_count_new": sum(1 for row in new_rows if row.get("d1_decision") == "TAKE"),
        "hold_count_new": sum(1 for row in new_rows if row.get("d1_decision") == "HOLD"),
        "duplicate_count": duplicate_count,
        "outcome_update_count": outcome_update_count,
        "journal_performance": performance,
        "state": state,
        "research_decision": research_decision,
        "safety": {
            "orders_sent": "no",
            "execution_status": EXECUTION_STATUS,
            "execution_logic_modified": False,
            "live_or_paper_behavior_modified": False,
            "strategy_thresholds_modified": False,
            "timers_modified": False,
            "sell_execution_enabled": False,
            "forward_shadow_runner_modified": False,
        },
    }


def journal_performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [
        row
        for row in rows
        if row.get("d1_decision") == "TAKE" and row.get("outcome_status") in {"tp_hit", "sl_hit", "expired"}
    ]
    r_values = [value for value in (safe_float(row.get("realized_r")) for row in closed) if value is not None]
    wins = [value for value in r_values if value > 0.0]
    losses = [value for value in r_values if value <= 0.0]
    gross_loss = abs(sum(losses))
    return {
        "closed_take_count": len(r_values),
        "avg_r": mean(r_values),
        "profit_factor": (sum(wins) / gross_loss) if gross_loss > 0.0 else (10.0 if wins else 0.0),
        "win_rate": len(wins) / len(r_values) if r_values else 0.0,
        "max_drawdown_r": max_drawdown_r(r_values),
    }


def result_payload(summary: dict[str, Any], new_rows: list[dict[str, Any]], journal_rows: list[dict[str, Any]], paths: dict[str, Path]) -> dict[str, Any]:
    return {
        "summary": summary,
        "new_rows": new_rows,
        "journal_rows": journal_rows,
        "paths": {key: str(value) for key, value in paths.items()},
    }


def latest_signal_timestamp(signals: pd.DataFrame) -> str:
    if signals.empty or "_signal_time" not in signals:
        return ""
    ts = signals["_signal_time"].max()
    return ts.isoformat() if not pd.isna(ts) else ""


def fallback_volatility(value: float | None, high_q: float | None) -> str:
    if value is None or high_q is None:
        return ""
    return "high" if value >= high_q else "normal"


def session_label(ts: pd.Timestamp) -> str:
    if pd.isna(ts):
        return ""
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


def normalized_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip().lower()


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def max_drawdown_r(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


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


def print_phase_s5_report(result: dict[str, Any]) -> None:
    summary = result["summary"]
    perf = summary["journal_performance"]
    print("AURUM-1 Phase S5 D1 Shadow Forward Journal")
    print("=" * 76)
    print(f"Latest raw signal timestamp: {summary['latest_raw_signal_timestamp']}")
    print(f"New D1 decisions logged:    {summary['new_decisions_logged']}")
    print(f"TAKE/HOLD new:              {summary['take_count_new']} / {summary['hold_count_new']}")
    print(f"Duplicate count:            {summary['duplicate_count']}")
    print(f"Outcome update count:       {summary['outcome_update_count']}")
    print("Current D1 journal performance")
    print(
        f"  closed_take={perf['closed_take_count']} avgR={perf['avg_r']:.3f} "
        f"PF={perf['profit_factor']:.2f} win={perf['win_rate']:.2%} maxDD={perf['max_drawdown_r']:.3f}R"
    )
    print("Safety")
    print("  Orders sent: no")
    print(f"  Execution status: {EXECUTION_STATUS}")
    print(f"Research decision: {summary['research_decision']}")
    print("Outputs:")
    for key, value in result["paths"].items():
        if key == "log_file":
            continue
        print(f"  {value}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AURUM-1 Phase S5 D1 shadow forward journal.")
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--update-outcomes", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-holding-candles", type=int, default=DEFAULT_MAX_HOLDING_CANDLES)
    parser.add_argument("--as-of", default=None, help="Optional UTC timestamp to stamp new journal rows.")
    return parser.parse_args(argv)
