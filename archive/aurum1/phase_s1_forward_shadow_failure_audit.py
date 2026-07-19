"""Phase S1 forward-shadow failure audit for AURUM-1.

This module is diagnostic only. It reads the forward-shadow SQLite ledger in
read-only mode, derives context from logged signals/candles, and writes report
artifacts. It does not import broker implementations, submit orders, change
strategy thresholds, or mutate live/paper trading state.
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = ROOT / "reports" / "forward_shadow"
DEFAULT_SHADOW_DB = DEFAULT_REPORT_DIR / "donchian_shadow.sqlite3"
STRATEGY_NAME = "raw_donchian_fixed_2r"
TIMEFRAME = "M15"
INSTRUMENT = "XAU_USD"
LOOKBACK = 20
MIN_DECISION_TRADES = 20

RESEARCH_DECISIONS = {
    "RAW_DONCHIAN_FAILS_CONTEXT_NEEDED",
    "SKIP_LOGIC_HURTING",
    "EXIT_LOGIC_HURTING",
    "VOLATILITY_FILTER_NEEDED",
    "SESSION_FILTER_NEEDED",
    "SAMPLE_TOO_SMALL_CONTINUE",
    "STRATEGY_HEALTHY_NO_CHANGE",
}

TRADE_AUDIT_FIELDS = [
    "timestamp",
    "signal_time",
    "entry_time",
    "exit_time",
    "instrument",
    "timeframe",
    "direction",
    "entry",
    "stop",
    "target",
    "exit_price",
    "realized_pnl",
    "realized_r",
    "outcome",
    "session_label",
    "weekday",
    "volatility_regime",
    "atr",
    "spread_cost",
    "total_cost",
    "signal_reason",
    "exit_reason",
    "donchian_breakout_direction",
    "breakout_level",
    "distance_from_breakout_level",
    "distance_from_breakout_atr",
    "distance_from_breakout_bucket",
    "atr_regime",
    "spread_cost_bucket",
    "time_since_last_trade_hours",
    "time_since_last_trade_bucket",
    "consecutive_signal_count",
    "holding_bars",
]

SKIPPED_AUDIT_FIELDS = [
    "timestamp",
    "signal_time",
    "entry_time",
    "instrument",
    "timeframe",
    "direction",
    "skip_reason",
    "market_conditions",
    "session",
    "weekday",
    "volatility_regime",
    "atr",
    "simulated_trade_outcome",
    "simulated_exit_time",
    "simulated_exit_reason",
    "simulated_r",
    "avoided_loss_r",
    "missed_profit_r",
    "entry",
    "stop",
    "target",
    "signal_reason",
    "donchian_breakout_direction",
    "breakout_level",
    "distance_from_breakout_level",
    "distance_from_breakout_atr",
    "distance_from_breakout_bucket",
    "atr_regime",
    "spread_cost_bucket",
    "time_since_last_trade_hours",
    "time_since_last_trade_bucket",
    "consecutive_signal_count",
]

BREAKDOWN_FIELDS = [
    "group_type",
    "group_value",
    "trade_count",
    "closed_trade_count",
    "win_rate",
    "avg_r",
    "net_r",
    "net_pnl",
    "profit_factor",
    "loss_count",
    "skipped_count",
    "skipped_simulated_wins",
    "skipped_simulated_losses",
    "net_avoided_r",
    "net_missed_r",
]

EXIT_COMPARISON_FIELDS = [
    "exit_name",
    "description",
    "signals_tested",
    "closed_count",
    "end_of_data_count",
    "open_count",
    "win_rate",
    "avg_r",
    "median_r",
    "net_r",
    "net_pnl",
    "profit_factor",
    "avg_holding_bars",
    "exit_reasons",
    "delta_avg_r_vs_fixed_2r",
    "notes",
]

DRAWDOWN_FIELDS = [
    "section",
    "rank",
    "signal_time",
    "entry_time",
    "exit_time",
    "direction",
    "session",
    "weekday",
    "volatility_regime",
    "realized_pnl",
    "realized_r",
    "window_start",
    "window_end",
    "drawdown",
    "cluster_dimension",
    "cluster_value",
    "cluster_loss_count",
    "cluster_loss_share",
    "note",
]


@dataclass(frozen=True)
class SimulationResult:
    outcome: str
    exit_time: str
    exit_reason: str
    exit_price: float | None
    r_multiple: float | None
    pnl: float | None
    holding_bars: int


def run_phase_s1_audit(
    shadow_db: Path | str = DEFAULT_SHADOW_DB,
    report_dir: Path | str = DEFAULT_REPORT_DIR,
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Run the Phase S1 audit and write all requested artifacts."""

    db_path = resolve_path(shadow_db)
    output_dir = resolve_path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    data = load_shadow_data(db_path)
    signals = normalize_signals(data["signals"])
    trades = normalize_trades(data["trades"])
    candles = normalize_candles(data["candles"])
    equity = normalize_equity(data["equity"])
    config = data["config"]
    weekly_reports = load_latest_weekly_reports(output_dir)

    contexts = build_signal_contexts(signals, trades, candles, config)
    trade_rows = build_trade_audit(signals, trades, contexts, config)
    skipped_rows = build_skipped_signal_audit(signals, candles, contexts, config)
    breakdown_rows = build_failure_mode_breakdown(trade_rows, skipped_rows)
    exit_rows = build_exit_comparison(signals, candles)
    drawdown_rows, drawdown_summary = build_drawdown_attribution(trade_rows, equity)

    trade_summary = summarize_trades(trade_rows)
    skipped_summary = summarize_skipped(skipped_rows)
    exit_summary = summarize_exit_comparison(exit_rows)
    decision, decision_reason = choose_research_decision(
        trade_summary,
        skipped_summary,
        breakdown_rows,
        exit_rows,
        drawdown_summary,
    )

    generated_at = utc_timestamp(as_of).isoformat() if as_of else datetime.now(UTC).isoformat()
    summary = {
        "generated_at": generated_at,
        "phase": "S1",
        "name": "Forward Shadow Failure Audit",
        "strategy": STRATEGY_NAME,
        "classification": "research-only",
        "shadow_db": str(db_path),
        "report_dir": str(output_dir),
        "trade_performance_summary": trade_summary,
        "skipped_signal_summary": skipped_summary,
        "exit_comparison_summary": exit_summary,
        "drawdown_attribution_summary": drawdown_summary,
        "latest_weekly_reports": weekly_reports,
        "research_decision": decision,
        "research_decision_reason": decision_reason,
        "success_definition": {
            "bad_raw_entry_logic": decision == "RAW_DONCHIAN_FAILS_CONTEXT_NEEDED",
            "bad_skip_logic": decision == "SKIP_LOGIC_HURTING",
            "bad_exit_logic": decision == "EXIT_LOGIC_HURTING",
            "bad_volatility_or_session_context": decision in {"VOLATILITY_FILTER_NEEDED", "SESSION_FILTER_NEEDED"},
            "sample_too_small": decision == "SAMPLE_TOO_SMALL_CONTINUE",
        },
        "safety": {
            "orders_placed": False,
            "execution_logic_modified": False,
            "live_or_paper_behavior_modified": False,
            "strategy_thresholds_modified": False,
            "timers_modified": False,
            "sqlite_read_mode": "query_only",
        },
    }

    paths = {
        "summary_json": output_dir / "phase_s1_failure_audit_summary.json",
        "trade_audit_csv": output_dir / "phase_s1_trade_audit.csv",
        "skipped_signal_audit_csv": output_dir / "phase_s1_skipped_signal_audit.csv",
        "failure_mode_breakdown_csv": output_dir / "phase_s1_failure_mode_breakdown.csv",
        "exit_comparison_csv": output_dir / "phase_s1_exit_comparison.csv",
        "drawdown_attribution_csv": output_dir / "phase_s1_drawdown_attribution.csv",
    }

    paths["summary_json"].write_text(json.dumps(summary, indent=2, sort_keys=True, default=json_default), encoding="utf-8")
    write_csv(paths["trade_audit_csv"], trade_rows, TRADE_AUDIT_FIELDS)
    write_csv(paths["skipped_signal_audit_csv"], skipped_rows, SKIPPED_AUDIT_FIELDS)
    write_csv(paths["failure_mode_breakdown_csv"], breakdown_rows, BREAKDOWN_FIELDS)
    write_csv(paths["exit_comparison_csv"], exit_rows, EXIT_COMPARISON_FIELDS)
    write_csv(paths["drawdown_attribution_csv"], drawdown_rows, DRAWDOWN_FIELDS)

    return {
        "summary": summary,
        "trade_audit": trade_rows,
        "skipped_signal_audit": skipped_rows,
        "failure_mode_breakdown": breakdown_rows,
        "exit_comparison": exit_rows,
        "drawdown_attribution": drawdown_rows,
        "paths": {key: str(value) for key, value in paths.items()},
    }


def resolve_path(path: Path | str) -> Path:
    candidate = Path(path)
    return ROOT / candidate if not candidate.is_absolute() else candidate


def load_shadow_data(shadow_db: Path) -> dict[str, Any]:
    if not shadow_db.exists():
        raise FileNotFoundError(f"Forward shadow DB not found: {shadow_db}")

    uri = f"{shadow_db.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.execute("PRAGMA query_only = ON")
        return {
            "signals": read_table(conn, "shadow_signals", signal_columns()),
            "trades": read_table(conn, "shadow_trades", trade_columns()),
            "candles": read_table(conn, "shadow_candles", candle_columns()),
            "equity": read_table(conn, "shadow_equity_curve", equity_columns()),
            "config": read_config(conn),
        }


def read_table(conn: sqlite3.Connection, table_name: str, expected_columns: list[str]) -> pd.DataFrame:
    exists = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    if not exists or int(exists[0]) == 0:
        return pd.DataFrame(columns=expected_columns)
    frame = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
    for column in expected_columns:
        if column not in frame.columns:
            frame[column] = None
    return frame


def read_config(conn: sqlite3.Connection) -> dict[str, Any]:
    exists = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='shadow_config'",
    ).fetchone()
    if not exists or int(exists[0]) == 0:
        return {}
    rows = conn.execute("SELECT key, value FROM shadow_config").fetchall()
    config: dict[str, Any] = {}
    for key, raw in rows:
        try:
            config[str(key)] = json.loads(str(raw))
        except json.JSONDecodeError:
            config[str(key)] = raw
    return config


def signal_columns() -> list[str]:
    return [
        "signal_time",
        "entry_time",
        "strategy",
        "direction",
        "status",
        "skip_reason",
        "entry_price",
        "stop_loss",
        "take_profit",
        "atr",
        "units",
        "risk_amount",
        "target_risk_amount",
        "spread_estimate",
        "slippage_estimate",
        "exit_time",
        "exit_reason",
    ]


def trade_columns() -> list[str]:
    return [
        "signal_time",
        "entry_time",
        "exit_time",
        "strategy",
        "direction",
        "entry_price",
        "stop_loss",
        "take_profit",
        "units",
        "risk_amount",
        "spread_estimate",
        "entry_slippage_estimate",
        "exit_slippage_estimate",
        "exit_price",
        "exit_reason",
        "gross_pnl",
        "net_pnl",
        "r_multiple",
        "holding_bars",
    ]


def candle_columns() -> list[str]:
    return ["timestamp", "open", "high", "low", "close", "volume", "signal_decision", "notes"]


def equity_columns() -> list[str]:
    return ["timestamp", "equity", "drawdown"]


def normalize_signals(signals: pd.DataFrame) -> pd.DataFrame:
    frame = signals.copy()
    if frame.empty:
        return frame
    for column in ("signal_time", "entry_time", "exit_time"):
        frame[column] = frame[column].map(blank_to_none)
        frame[f"_{column}"] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    for column in ("entry_price", "stop_loss", "take_profit", "atr", "units", "risk_amount", "spread_estimate", "slippage_estimate"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["direction"] = frame["direction"].fillna("BUY").astype(str).str.upper()
    frame["status"] = frame["status"].fillna("unknown").astype(str)
    return frame.sort_values("_signal_time").reset_index(drop=True)


def normalize_trades(trades: pd.DataFrame) -> pd.DataFrame:
    frame = trades.copy()
    if frame.empty:
        return frame
    for column in ("signal_time", "entry_time", "exit_time"):
        frame[column] = frame[column].map(blank_to_none)
        frame[f"_{column}"] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    for column in (
        "entry_price",
        "stop_loss",
        "take_profit",
        "units",
        "risk_amount",
        "spread_estimate",
        "entry_slippage_estimate",
        "exit_slippage_estimate",
        "exit_price",
        "gross_pnl",
        "net_pnl",
        "r_multiple",
        "holding_bars",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["direction"] = frame["direction"].fillna("BUY").astype(str).str.upper()
    return frame.sort_values("_entry_time").reset_index(drop=True)


def normalize_candles(candles: pd.DataFrame) -> pd.DataFrame:
    frame = candles.copy()
    if frame.empty:
        return frame
    frame["timestamp"] = frame["timestamp"].map(blank_to_none)
    frame["_timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["_timestamp"]).sort_values("_timestamp").reset_index(drop=True)
    frame["prev_donchian_high"] = frame["high"].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
    frame["prev_donchian_low"] = frame["low"].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
    return frame


def normalize_equity(equity: pd.DataFrame) -> pd.DataFrame:
    frame = equity.copy()
    if frame.empty:
        return frame
    frame["timestamp"] = frame["timestamp"].map(blank_to_none)
    frame["_timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    for column in ("equity", "drawdown"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["_timestamp"]).sort_values("_timestamp").reset_index(drop=True)


def build_signal_contexts(
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    candles: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if signals.empty:
        return {}

    atr_values = [float(value) for value in signals["atr"].dropna().tolist() if float(value) > 0.0]
    low_q, high_q = quantile_pair(atr_values)
    candle_index = pd.DatetimeIndex(candles["_timestamp"]) if not candles.empty else pd.DatetimeIndex([], tz=UTC)

    entered_times = sorted(
        ts for ts in pd.to_datetime(signals.loc[signals["status"].eq("entered"), "entry_time"], utc=True, errors="coerce").tolist() if not pd.isna(ts)
    )
    contexts: dict[str, dict[str, Any]] = {}
    previous_signal_ts: pd.Timestamp | None = None
    consecutive = 0

    for _, signal in signals.sort_values("_signal_time").iterrows():
        signal_time = str(signal.get("signal_time") or "")
        ts = safe_timestamp(signal.get("_signal_time"))
        entry_ts = safe_timestamp(signal.get("_entry_time"))
        if previous_signal_ts is not None and ts is not None and (ts - previous_signal_ts).total_seconds() <= 3600:
            consecutive += 1
        else:
            consecutive = 1
        if ts is not None:
            previous_signal_ts = ts

        candle = candle_at_or_before(candles, candle_index, ts)
        atr = safe_float(signal.get("atr"))
        direction = str(signal.get("direction") or "BUY").upper()
        close = safe_float(candle.get("close")) if candle is not None else None
        prev_high = safe_float(candle.get("prev_donchian_high")) if candle is not None else None
        prev_low = safe_float(candle.get("prev_donchian_low")) if candle is not None else None
        breakout_level = prev_high if direction == "BUY" else prev_low
        distance = breakout_distance(direction, close, prev_high, prev_low)
        distance_atr = distance / atr if distance is not None and atr and atr > 0.0 else None
        time_since = time_since_previous_entered(entry_ts, entered_times)
        risk = safe_float(signal.get("risk_amount")) or 0.0
        cost = (safe_float(signal.get("spread_estimate")) or 0.0) + (safe_float(signal.get("slippage_estimate")) or 0.0)
        cost_r = cost / risk if risk > 0.0 else None

        session = session_label(ts)
        weekday = weekday_label(ts)
        volatility = volatility_bucket(atr, low_q, high_q)
        context = {
            "instrument": instrument_from_config(config),
            "timeframe": TIMEFRAME,
            "session_label": session,
            "weekday": weekday,
            "volatility_regime": volatility,
            "atr_regime": f"atr_{volatility}" if volatility != "unknown" else "unknown",
            "donchian_breakout_direction": infer_breakout_direction(direction, close, prev_high, prev_low),
            "breakout_level": breakout_level,
            "distance_from_breakout_level": distance,
            "distance_from_breakout_atr": distance_atr,
            "distance_from_breakout_bucket": distance_bucket(distance_atr),
            "spread_cost_bucket": cost_bucket(cost_r),
            "time_since_last_trade_hours": time_since,
            "time_since_last_trade_bucket": time_since_bucket(time_since),
            "consecutive_signal_count": consecutive,
            "signal_reason": "donchian_20_breakout",
            "market_conditions": market_conditions_text(session, weekday, volatility, atr, distance_atr),
        }
        contexts[signal_time] = context
    return contexts


def build_trade_audit(
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    contexts: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    signal_by_time = {str(row.get("signal_time")): row for _, row in signals.iterrows()} if not signals.empty else {}
    seen_trade_signals: set[str] = set()

    for _, trade in trades.iterrows():
        signal_time = str(trade.get("signal_time") or "")
        seen_trade_signals.add(signal_time)
        signal = signal_by_time.get(signal_time)
        context = contexts.get(signal_time, default_context(config))
        realized_r = safe_float(trade.get("r_multiple"))
        net_pnl = safe_float(trade.get("net_pnl"))
        total_cost = (
            (safe_float(trade.get("spread_estimate")) or 0.0)
            + (safe_float(trade.get("entry_slippage_estimate")) or 0.0)
            + (safe_float(trade.get("exit_slippage_estimate")) or 0.0)
        )
        rows.append(
            {
                **base_context_fields(context),
                "timestamp": iso_or_blank(trade.get("_entry_time")),
                "signal_time": signal_time,
                "entry_time": iso_or_blank(trade.get("_entry_time")),
                "exit_time": iso_or_blank(trade.get("_exit_time")),
                "direction": str(trade.get("direction") or "BUY").upper(),
                "entry": safe_float(trade.get("entry_price")),
                "stop": safe_float(trade.get("stop_loss")),
                "target": safe_float(trade.get("take_profit")),
                "exit_price": safe_float(trade.get("exit_price")),
                "realized_pnl": net_pnl,
                "realized_r": realized_r,
                "outcome": trade_outcome(realized_r, str(trade.get("exit_reason") or "")),
                "atr": safe_float(signal.get("atr")) if signal is not None else None,
                "spread_cost": safe_float(trade.get("spread_estimate")),
                "total_cost": total_cost,
                "exit_reason": str(trade.get("exit_reason") or ""),
                "holding_bars": safe_int(trade.get("holding_bars")),
            }
        )

    if not signals.empty:
        open_signals = signals[(signals["status"].eq("entered")) & (~signals["signal_time"].astype(str).isin(seen_trade_signals))]
        for _, signal in open_signals.iterrows():
            signal_time = str(signal.get("signal_time") or "")
            context = contexts.get(signal_time, default_context(config))
            rows.append(
                {
                    **base_context_fields(context),
                    "timestamp": iso_or_blank(signal.get("_entry_time")),
                    "signal_time": signal_time,
                    "entry_time": iso_or_blank(signal.get("_entry_time")),
                    "exit_time": "",
                    "direction": str(signal.get("direction") or "BUY").upper(),
                    "entry": safe_float(signal.get("entry_price")),
                    "stop": safe_float(signal.get("stop_loss")),
                    "target": safe_float(signal.get("take_profit")),
                    "exit_price": None,
                    "realized_pnl": None,
                    "realized_r": None,
                    "outcome": "open",
                    "atr": safe_float(signal.get("atr")),
                    "spread_cost": safe_float(signal.get("spread_estimate")),
                    "total_cost": (safe_float(signal.get("spread_estimate")) or 0.0) + (safe_float(signal.get("slippage_estimate")) or 0.0),
                    "exit_reason": str(signal.get("exit_reason") or ""),
                    "holding_bars": None,
                }
            )
    return rows


def build_skipped_signal_audit(
    signals: pd.DataFrame,
    candles: pd.DataFrame,
    contexts: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if signals.empty:
        return rows
    skipped = signals[signals["status"].eq("skipped")].copy()
    for _, signal in skipped.iterrows():
        signal_time = str(signal.get("signal_time") or "")
        context = contexts.get(signal_time, default_context(config))
        sim = simulate_signal_exit(signal, candles, target_r=2.0, exit_mode="fixed", close_at_end=False)
        sim_r = sim.r_multiple
        avoided = abs(sim_r) if sim_r is not None and sim.outcome == "loss" else 0.0
        missed = sim_r if sim_r is not None and sim.outcome == "win" else 0.0
        rows.append(
            {
                **base_context_fields(context),
                "timestamp": iso_or_blank(signal.get("_signal_time")),
                "signal_time": signal_time,
                "entry_time": iso_or_blank(signal.get("_entry_time")),
                "direction": str(signal.get("direction") or "BUY").upper(),
                "skip_reason": str(signal.get("skip_reason") or "unknown"),
                "market_conditions": context.get("market_conditions", ""),
                "session": context.get("session_label", "unknown"),
                "atr": safe_float(signal.get("atr")),
                "simulated_trade_outcome": sim.outcome,
                "simulated_exit_time": sim.exit_time,
                "simulated_exit_reason": sim.exit_reason,
                "simulated_r": sim_r,
                "avoided_loss_r": avoided,
                "missed_profit_r": missed,
                "entry": safe_float(signal.get("entry_price")),
                "stop": safe_float(signal.get("stop_loss")),
                "target": safe_float(signal.get("take_profit")),
                "signal_reason": context.get("signal_reason", "donchian_20_breakout"),
            }
        )
    return rows


def build_failure_mode_breakdown(trade_rows: list[dict[str, Any]], skipped_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    dimensions = [
        ("direction", "direction"),
        ("session", "session_label"),
        ("weekday", "weekday"),
        ("volatility_regime", "volatility_regime"),
        ("donchian_breakout_direction", "donchian_breakout_direction"),
        ("distance_from_breakout_level", "distance_from_breakout_bucket"),
        ("atr_regime", "atr_regime"),
        ("spread_cost_bucket", "spread_cost_bucket"),
        ("time_since_last_trade", "time_since_last_trade_bucket"),
        ("consecutive_signal_count", "consecutive_signal_count"),
    ]
    for group_type, field in dimensions:
        values = sorted(
            {
                str(row.get(field, "unknown") or "unknown")
                for row in [*trade_rows, *skipped_rows]
            }
        )
        for value in values:
            trades_subset = [row for row in trade_rows if str(row.get(field, "unknown") or "unknown") == value]
            skipped_subset = [row for row in skipped_rows if str(row.get(field, "unknown") or "unknown") == value]
            closed = [row for row in trades_subset if row.get("outcome") in {"win", "loss", "expired"}]
            r_values = [float(row["realized_r"]) for row in closed if row.get("realized_r") not in (None, "")]
            pnl_values = [float(row["realized_pnl"]) for row in closed if row.get("realized_pnl") not in (None, "")]
            rows.append(
                {
                    "group_type": group_type,
                    "group_value": value,
                    "trade_count": len(trades_subset),
                    "closed_trade_count": len(closed),
                    "win_rate": pct(sum(1 for row in closed if row.get("outcome") == "win"), len(closed)),
                    "avg_r": mean(r_values),
                    "net_r": sum(r_values),
                    "net_pnl": sum(pnl_values),
                    "profit_factor": profit_factor(r_values),
                    "loss_count": sum(1 for row in closed if row.get("outcome") == "loss"),
                    "skipped_count": len(skipped_subset),
                    "skipped_simulated_wins": sum(1 for row in skipped_subset if row.get("simulated_trade_outcome") == "win"),
                    "skipped_simulated_losses": sum(1 for row in skipped_subset if row.get("simulated_trade_outcome") == "loss"),
                    "net_avoided_r": sum(float(row.get("avoided_loss_r") or 0.0) for row in skipped_subset),
                    "net_missed_r": sum(float(row.get("missed_profit_r") or 0.0) for row in skipped_subset),
                }
            )
    return rows


def build_exit_comparison(signals: pd.DataFrame, candles: pd.DataFrame) -> list[dict[str, Any]]:
    if signals.empty:
        return []
    entered = signals[signals["status"].eq("entered")].copy()
    methods = [
        ("fixed_1r", "Fixed target at 1R with original stop.", "fixed", 1.0),
        ("fixed_1_5r", "Fixed target at 1.5R with original stop.", "fixed", 1.5),
        ("fixed_2r", "Current fixed target at 2R with original stop.", "fixed", 2.0),
        ("trailing_stop", "Fixed-distance trailing stop using original risk distance.", "trailing", None),
        ("next_structural_high_low", "Exit on the next structural Donchian high/low break if candle data allows.", "structural", None),
    ]
    results_by_name: dict[str, list[SimulationResult]] = {}
    for name, _, mode, target_r in methods:
        results_by_name[name] = [
            simulate_signal_exit(signal, candles, target_r=target_r or 2.0, exit_mode=mode, close_at_end=True)
            for _, signal in entered.iterrows()
        ]

    fixed_avg = mean([result.r_multiple for result in results_by_name.get("fixed_2r", []) if result.r_multiple is not None])
    rows: list[dict[str, Any]] = []
    for name, description, _, _ in methods:
        sims = results_by_name[name]
        r_values = [float(result.r_multiple) for result in sims if result.r_multiple is not None]
        pnl_values = [float(result.pnl) for result in sims if result.pnl is not None]
        closed = [result for result in sims if result.outcome in {"win", "loss", "expired"}]
        rows.append(
            {
                "exit_name": name,
                "description": description,
                "signals_tested": len(sims),
                "closed_count": len(closed),
                "end_of_data_count": sum(1 for result in sims if result.exit_reason == "end_of_data"),
                "open_count": sum(1 for result in sims if result.outcome == "open"),
                "win_rate": pct(sum(1 for result in closed if result.outcome == "win"), len(closed)),
                "avg_r": mean(r_values),
                "median_r": median(r_values),
                "net_r": sum(r_values),
                "net_pnl": sum(pnl_values),
                "profit_factor": profit_factor(r_values),
                "avg_holding_bars": mean([float(result.holding_bars) for result in sims if result.holding_bars >= 0]),
                "exit_reasons": json.dumps(dict(Counter(result.exit_reason for result in sims)), sort_keys=True),
                "delta_avg_r_vs_fixed_2r": mean(r_values) - fixed_avg if name != "fixed_2r" else 0.0,
                "notes": "Diagnostic comparison only; no exit parameters were changed.",
            }
        )
    return rows


def build_drawdown_attribution(
    trade_rows: list[dict[str, Any]],
    equity: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    closed = [row for row in trade_rows if row.get("outcome") in {"win", "loss", "expired"}]
    worst = sorted(closed, key=lambda row: float(row.get("realized_r") or 0.0))[:10]
    for rank, trade in enumerate(worst, start=1):
        rows.append(
            {
                "section": "worst_trade",
                "rank": rank,
                "signal_time": trade.get("signal_time", ""),
                "entry_time": trade.get("entry_time", ""),
                "exit_time": trade.get("exit_time", ""),
                "direction": trade.get("direction", ""),
                "session": trade.get("session_label", ""),
                "weekday": trade.get("weekday", ""),
                "volatility_regime": trade.get("volatility_regime", ""),
                "realized_pnl": trade.get("realized_pnl"),
                "realized_r": trade.get("realized_r"),
                "window_start": "",
                "window_end": "",
                "drawdown": "",
                "cluster_dimension": "",
                "cluster_value": "",
                "cluster_loss_count": "",
                "cluster_loss_share": "",
                "note": "Worst trades by realized R.",
            }
        )

    window = worst_drawdown_window(equity)
    rows.append(
        {
            "section": "worst_drawdown_window",
            "rank": 1,
            "signal_time": "",
            "entry_time": "",
            "exit_time": "",
            "direction": "",
            "session": "",
            "weekday": "",
            "volatility_regime": "",
            "realized_pnl": "",
            "realized_r": "",
            "window_start": window["start"],
            "window_end": window["end"],
            "drawdown": window["drawdown"],
            "cluster_dimension": "",
            "cluster_value": "",
            "cluster_loss_count": "",
            "cluster_loss_share": "",
            "note": window["note"],
        }
    )

    clusters = drawdown_clusters(closed)
    for cluster in clusters:
        rows.append(
            {
                "section": "loss_cluster",
                "rank": cluster["rank"],
                "signal_time": "",
                "entry_time": "",
                "exit_time": "",
                "direction": "",
                "session": "",
                "weekday": "",
                "volatility_regime": "",
                "realized_pnl": "",
                "realized_r": "",
                "window_start": "",
                "window_end": "",
                "drawdown": "",
                "cluster_dimension": cluster["dimension"],
                "cluster_value": cluster["value"],
                "cluster_loss_count": cluster["loss_count"],
                "cluster_loss_share": cluster["loss_share"],
                "note": "Loss clustering candidate." if cluster["clustered"] else "No dominant loss cluster.",
            }
        )

    summary = {
        "worst_10_trades": [
            {
                "signal_time": row.get("signal_time"),
                "exit_time": row.get("exit_time"),
                "realized_r": row.get("realized_r"),
                "realized_pnl": row.get("realized_pnl"),
                "session": row.get("session_label"),
                "direction": row.get("direction"),
                "volatility_regime": row.get("volatility_regime"),
            }
            for row in worst
        ],
        "worst_drawdown_window": window,
        "clusters": clusters,
        "max_consecutive_losses": max_consecutive_losses([float(row.get("realized_r") or 0.0) for row in closed]),
    }
    return rows, summary


def simulate_signal_exit(
    signal: pd.Series,
    candles: pd.DataFrame,
    *,
    target_r: float,
    exit_mode: str,
    close_at_end: bool,
) -> SimulationResult:
    if candles.empty:
        return SimulationResult("unknown", "", "no_candles", None, None, None, -1)
    entry_ts = safe_timestamp(signal.get("_entry_time"))
    if entry_ts is None:
        return SimulationResult("unknown", "", "missing_entry_time", None, None, None, -1)

    direction = str(signal.get("direction") or "BUY").upper()
    entry = safe_float(signal.get("entry_price"))
    stop = safe_float(signal.get("stop_loss"))
    units = safe_float(signal.get("units")) or 0.0
    risk_amount = safe_float(signal.get("risk_amount")) or 0.0
    spread = safe_float(signal.get("spread_estimate")) or 0.0
    slippage_distance = exit_slippage_distance(signal)
    if entry is None or stop is None or risk_amount <= 0.0:
        return SimulationResult("unknown", "", "missing_price_or_risk", None, None, None, -1)

    risk_distance = abs(entry - stop)
    if risk_distance <= 0.0:
        return SimulationResult("unknown", "", "zero_risk_distance", None, None, None, -1)
    target = entry + target_r * risk_distance if direction == "BUY" else entry - target_r * risk_distance
    segment = candles[candles["_timestamp"] > entry_ts].copy()
    if segment.empty:
        return SimulationResult("open", "", "no_future_candles", None, None, None, 0)

    active_stop = stop
    for offset, (_, candle) in enumerate(segment.iterrows(), start=1):
        open_price = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        exit_price: float | None = None
        reason = ""

        if direction == "BUY":
            if open_price <= active_stop:
                exit_price, reason = open_price, "stop_loss_gap"
            elif low <= active_stop:
                exit_price, reason = active_stop, "stop_loss"
            elif exit_mode == "fixed" and high >= target:
                exit_price, reason = target, f"fixed_{target_r:g}r_target"
            elif exit_mode == "structural" and structural_break(direction, candle):
                exit_price, reason = close, "next_structural_low_exit"
        else:
            if open_price >= active_stop:
                exit_price, reason = open_price, "stop_loss_gap"
            elif high >= active_stop:
                exit_price, reason = active_stop, "stop_loss"
            elif exit_mode == "fixed" and low <= target:
                exit_price, reason = target, f"fixed_{target_r:g}r_target"
            elif exit_mode == "structural" and structural_break(direction, candle):
                exit_price, reason = close, "next_structural_high_exit"

        if exit_price is not None:
            return simulation_result(direction, entry, exit_price, slippage_distance, units, risk_amount, spread, candle["_timestamp"], reason, offset)

        if exit_mode == "trailing":
            if direction == "BUY":
                active_stop = max(active_stop, high - risk_distance)
            else:
                active_stop = min(active_stop, low + risk_distance)

    if close_at_end:
        last = segment.iloc[-1]
        return simulation_result(direction, entry, float(last["close"]), slippage_distance, units, risk_amount, spread, last["_timestamp"], "end_of_data", len(segment))
    return SimulationResult("open", "", "open_at_data_end", None, None, None, len(segment))


def simulation_result(
    direction: str,
    entry: float,
    intended_exit: float,
    slippage_distance: float,
    units: float,
    risk_amount: float,
    spread: float,
    exit_ts: Any,
    reason: str,
    holding_bars: int,
) -> SimulationResult:
    actual_exit = intended_exit - slippage_distance if direction == "BUY" else intended_exit + slippage_distance
    price_delta = actual_exit - entry
    signed_delta = price_delta if direction == "BUY" else -price_delta
    gross = signed_delta * units
    net = gross - spread
    r_multiple = net / risk_amount if risk_amount > 0.0 else None
    if reason == "end_of_data":
        outcome = "win" if r_multiple is not None and r_multiple > 0.0 else "loss"
    elif r_multiple is None:
        outcome = "unknown"
    else:
        outcome = "win" if r_multiple > 0.0 else "loss"
    return SimulationResult(outcome, iso_or_blank(exit_ts), reason, actual_exit, r_multiple, net, holding_bars)


def structural_break(direction: str, candle: pd.Series) -> bool:
    close = safe_float(candle.get("close"))
    if close is None:
        return False
    if direction == "BUY":
        level = safe_float(candle.get("prev_donchian_low"))
        return level is not None and close < level
    level = safe_float(candle.get("prev_donchian_high"))
    return level is not None and close > level


def exit_slippage_distance(signal: pd.Series) -> float:
    units = safe_float(signal.get("units")) or 0.0
    slippage_estimate = safe_float(signal.get("slippage_estimate")) or 0.0
    if units <= 0.0:
        return 0.0
    return max(0.0, slippage_estimate / (2.0 * units))


def summarize_trades(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [row for row in rows if row.get("outcome") in {"win", "loss", "expired"}]
    wins = [row for row in closed if row.get("outcome") == "win"]
    losses = [row for row in closed if row.get("outcome") == "loss"]
    r_values = [float(row["realized_r"]) for row in closed if row.get("realized_r") not in (None, "")]
    pnl_values = [float(row["realized_pnl"]) for row in closed if row.get("realized_pnl") not in (None, "")]
    return {
        "signals_entered_or_open": len(rows),
        "closed_trades": len(closed),
        "open_trades": sum(1 for row in rows if row.get("outcome") == "open"),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": pct(len(wins), len(closed)),
        "net_pnl": sum(pnl_values),
        "net_r": sum(r_values),
        "average_r": mean(r_values),
        "median_r": median(r_values),
        "profit_factor": profit_factor(r_values),
        "max_consecutive_losses": max_consecutive_losses(r_values),
    }


def summarize_skipped(rows: list[dict[str, Any]]) -> dict[str, Any]:
    losses = [row for row in rows if row.get("simulated_trade_outcome") == "loss"]
    wins = [row for row in rows if row.get("simulated_trade_outcome") == "win"]
    avoided = sum(float(row.get("avoided_loss_r") or 0.0) for row in rows)
    missed = sum(float(row.get("missed_profit_r") or 0.0) for row in rows)
    reasons = Counter(str(row.get("skip_reason") or "unknown") for row in rows)
    return {
        "total_skipped": len(rows),
        "skipped_that_would_have_lost": len(losses),
        "skipped_that_would_have_won": len(wins),
        "simulated_unknown_or_open": len(rows) - len(losses) - len(wins),
        "net_avoided_r": avoided,
        "net_missed_r": missed,
        "net_skip_value_r": avoided - missed,
        "top_skip_reasons": dict(reasons.most_common(10)),
        "skipping_logic_assessment": "helping" if avoided > missed else "hurting" if missed > avoided else "inconclusive",
    }


def summarize_exit_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"best_by_avg_r": None, "fixed_2r": None}
    best = max(rows, key=lambda row: float(row.get("avg_r") or 0.0))
    fixed = next((row for row in rows if row.get("exit_name") == "fixed_2r"), None)
    return {"best_by_avg_r": best, "fixed_2r": fixed}


def choose_research_decision(
    trade_summary: dict[str, Any],
    skipped_summary: dict[str, Any],
    breakdown_rows: list[dict[str, Any]],
    exit_rows: list[dict[str, Any]],
    drawdown_summary: dict[str, Any],
) -> tuple[str, str]:
    trade_count = int(trade_summary.get("closed_trades", 0))
    avg_r = float(trade_summary.get("average_r", 0.0))
    pf = float(trade_summary.get("profit_factor", 0.0))
    win_rate = float(trade_summary.get("win_rate", 0.0))

    enough_skips = int(skipped_summary.get("skipped_that_would_have_won", 0)) + int(skipped_summary.get("skipped_that_would_have_lost", 0)) >= 5
    if enough_skips and float(skipped_summary.get("net_skip_value_r", 0.0)) < -1.0:
        return "SKIP_LOGIC_HURTING", "Skipped signals show more missed R than avoided loss R."

    fixed = next((row for row in exit_rows if row.get("exit_name") == "fixed_2r"), None)
    alternatives = [row for row in exit_rows if row.get("exit_name") != "fixed_2r"]
    if fixed and alternatives and trade_count >= 5:
        fixed_avg = float(fixed.get("avg_r") or 0.0)
        best_alt = max(alternatives, key=lambda row: float(row.get("avg_r") or 0.0))
        if float(best_alt.get("avg_r") or 0.0) - fixed_avg >= 0.35:
            return "EXIT_LOGIC_HURTING", f"{best_alt['exit_name']} outperformed fixed_2r by at least 0.35R on average."

    vol_cluster = dominant_negative_group(breakdown_rows, "volatility_regime")
    if vol_cluster is not None and trade_count >= 8:
        return "VOLATILITY_FILTER_NEEDED", f"Losses are concentrated in volatility regime {vol_cluster['group_value']}."

    session_cluster = dominant_negative_group(breakdown_rows, "session")
    if session_cluster is not None and trade_count >= 8:
        return "SESSION_FILTER_NEEDED", f"Losses are concentrated in session {session_cluster['group_value']}."

    if trade_count < MIN_DECISION_TRADES:
        return "SAMPLE_TOO_SMALL_CONTINUE", f"Only {trade_count} closed trades are available; minimum diagnostic sample is {MIN_DECISION_TRADES}."

    if pf >= 1.10 and avg_r > 0.0 and win_rate >= 0.30:
        return "STRATEGY_HEALTHY_NO_CHANGE", "Forward shadow performance is positive with acceptable PF and win rate."

    return "RAW_DONCHIAN_FAILS_CONTEXT_NEEDED", "Closed-trade performance is negative and not sufficiently explained by skip, exit, session, or volatility buckets."


def dominant_negative_group(rows: list[dict[str, Any]], group_type: str) -> dict[str, Any] | None:
    candidates = [
        row
        for row in rows
        if row.get("group_type") == group_type and int(row.get("closed_trade_count") or 0) >= 3 and float(row.get("avg_r") or 0.0) < 0.0
    ]
    if not candidates:
        return None
    total_losses = sum(int(row.get("loss_count") or 0) for row in candidates)
    if total_losses < 3:
        return None
    strongest = max(candidates, key=lambda row: int(row.get("loss_count") or 0))
    share = int(strongest.get("loss_count") or 0) / total_losses if total_losses else 0.0
    return strongest if share >= 0.60 else None


def worst_drawdown_window(equity: pd.DataFrame) -> dict[str, Any]:
    if equity.empty:
        return {"start": "", "end": "", "drawdown": 0.0, "note": "No equity curve available."}
    min_idx = equity["drawdown"].astype(float).idxmin()
    trough = equity.loc[min_idx]
    before = equity.loc[:min_idx].copy()
    if before.empty:
        return {"start": "", "end": iso_or_blank(trough["_timestamp"]), "drawdown": safe_float(trough.get("drawdown")) or 0.0, "note": "No prior equity peak available."}
    peak_idx = before["equity"].astype(float).idxmax()
    peak = before.loc[peak_idx]
    return {
        "start": iso_or_blank(peak["_timestamp"]),
        "end": iso_or_blank(trough["_timestamp"]),
        "drawdown": safe_float(trough.get("drawdown")) or 0.0,
        "note": "Worst equity drawdown window from prior peak to trough.",
    }


def drawdown_clusters(closed_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    losses = [row for row in closed_trades if row.get("outcome") == "loss"]
    dimensions = [("session", "session_label"), ("direction", "direction"), ("volatility", "volatility_regime"), ("weekday", "weekday")]
    clusters: list[dict[str, Any]] = []
    for rank, (dimension, field) in enumerate(dimensions, start=1):
        counts = Counter(str(row.get(field, "unknown") or "unknown") for row in losses)
        value, count = counts.most_common(1)[0] if counts else ("unknown", 0)
        share = pct(count, len(losses))
        clusters.append(
            {
                "rank": rank,
                "dimension": dimension,
                "value": value,
                "loss_count": count,
                "loss_share": share,
                "clustered": bool(len(losses) >= 3 and share >= 0.60),
            }
        )
    return clusters


def load_latest_weekly_reports(report_dir: Path, limit: int = 3) -> list[dict[str, Any]]:
    reports = sorted(report_dir.glob("donchian_shadow_weekly_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    output: list[dict[str, Any]] = []
    for path in reports[:limit]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        output.append(
            {
                "path": str(path),
                "period_start": payload.get("period_start"),
                "period_end": payload.get("period_end"),
                "gross_pnl": payload.get("gross_pnl"),
                "net_pnl": payload.get("net_pnl"),
                "profit_factor": payload.get("profit_factor"),
                "win_rate": payload.get("win_rate"),
                "trades": payload.get("trade_count"),
                "average_r": payload.get("average_r"),
                "skipped_signals": payload.get("skipped_signals"),
            }
        )
    return output


def print_phase_s1_report(result: dict[str, Any]) -> None:
    summary = result["summary"]
    trade = summary["trade_performance_summary"]
    skipped = summary["skipped_signal_summary"]
    drawdown = summary["drawdown_attribution_summary"]

    print("AURUM-1 Phase S1 Forward Shadow Failure Audit")
    print("=" * 76)
    print("Trade performance summary")
    print(f"  Closed trades: {trade['closed_trades']}  Wins: {trade['wins']}  Losses: {trade['losses']}")
    print(f"  Net P&L: {trade['net_pnl']:.2f}  Avg R: {trade['average_r']:.3f}  PF: {trade['profit_factor']:.2f}  Win rate: {trade['win_rate']:.2%}")
    print()
    print("Skipped signal summary")
    print(f"  Total skipped: {skipped['total_skipped']}  Would lose: {skipped['skipped_that_would_have_lost']}  Would win: {skipped['skipped_that_would_have_won']}")
    print(f"  Net avoided R: {skipped['net_avoided_r']:.3f}  Net missed R: {skipped['net_missed_r']:.3f}  Assessment: {skipped['skipping_logic_assessment']}")
    print()
    print("Failure mode table")
    print(f"{'group':<28}{'value':<24}{'trades':>8}{'avgR':>9}{'PF':>8}{'skips':>8}")
    for row in top_breakdown_rows(result["failure_mode_breakdown"]):
        print(
            f"{row['group_type']:<28}{str(row['group_value'])[:23]:<24}"
            f"{int(row['closed_trade_count']):>8}{float(row['avg_r']):>9.3f}"
            f"{float(row['profit_factor']):>8.2f}{int(row['skipped_count']):>8}"
        )
    print()
    print("Exit comparison table")
    print(f"{'exit':<28}{'signals':>8}{'avgR':>9}{'netR':>9}{'PF':>8}{'win':>8}")
    for row in result["exit_comparison"]:
        print(
            f"{row['exit_name']:<28}{int(row['signals_tested']):>8}"
            f"{float(row['avg_r']):>9.3f}{float(row['net_r']):>9.3f}"
            f"{float(row['profit_factor']):>8.2f}{float(row['win_rate']):>8.2%}"
        )
    print()
    print("Drawdown attribution summary")
    window = drawdown["worst_drawdown_window"]
    print(f"  Worst window: {window.get('start')} -> {window.get('end')}  DD: {float(window.get('drawdown') or 0.0):.2%}")
    print(f"  Max consecutive losses: {drawdown['max_consecutive_losses']}")
    clustered = [cluster for cluster in drawdown["clusters"] if cluster.get("clustered")]
    if clustered:
        print("  Clustering: " + ", ".join(f"{item['dimension']}={item['value']} ({item['loss_share']:.0%})" for item in clustered))
    else:
        print("  Clustering: no dominant session/direction/volatility/time cluster detected")
    print()
    print(f"Research decision: {summary['research_decision']}")
    print(f"Reason: {summary['research_decision_reason']}")
    print("Outputs:")
    for value in result["paths"].values():
        print(f"  {value}")


def top_breakdown_rows(rows: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    interesting = [
        row
        for row in rows
        if int(row.get("closed_trade_count") or 0) > 0 or int(row.get("skipped_count") or 0) > 0
    ]
    return sorted(interesting, key=lambda row: (int(row.get("closed_trade_count") or 0), abs(float(row.get("avg_r") or 0.0))), reverse=True)[:limit]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: csv_value(row.get(field)) for field in fields})


def base_context_fields(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "instrument": context.get("instrument", INSTRUMENT),
        "timeframe": context.get("timeframe", TIMEFRAME),
        "session_label": context.get("session_label", "unknown"),
        "weekday": context.get("weekday", "unknown"),
        "volatility_regime": context.get("volatility_regime", "unknown"),
        "signal_reason": context.get("signal_reason", "donchian_20_breakout"),
        "donchian_breakout_direction": context.get("donchian_breakout_direction", "unknown"),
        "breakout_level": context.get("breakout_level"),
        "distance_from_breakout_level": context.get("distance_from_breakout_level"),
        "distance_from_breakout_atr": context.get("distance_from_breakout_atr"),
        "distance_from_breakout_bucket": context.get("distance_from_breakout_bucket", "unknown"),
        "atr_regime": context.get("atr_regime", "unknown"),
        "spread_cost_bucket": context.get("spread_cost_bucket", "unknown"),
        "time_since_last_trade_hours": context.get("time_since_last_trade_hours"),
        "time_since_last_trade_bucket": context.get("time_since_last_trade_bucket", "unknown"),
        "consecutive_signal_count": context.get("consecutive_signal_count", 1),
    }


def default_context(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "instrument": instrument_from_config(config),
        "timeframe": TIMEFRAME,
        "session_label": "unknown",
        "weekday": "unknown",
        "volatility_regime": "unknown",
        "atr_regime": "unknown",
        "signal_reason": "donchian_20_breakout",
        "donchian_breakout_direction": "unknown",
        "distance_from_breakout_bucket": "unknown",
        "spread_cost_bucket": "unknown",
        "time_since_last_trade_bucket": "unknown",
        "consecutive_signal_count": 1,
        "market_conditions": "unknown",
    }


def instrument_from_config(config: dict[str, Any]) -> str:
    raw = config.get("instrument") or config.get("oanda_instrument")
    return str(raw or INSTRUMENT)


def candle_at_or_before(candles: pd.DataFrame, candle_index: pd.DatetimeIndex, ts: pd.Timestamp | None) -> pd.Series | None:
    if ts is None or candles.empty:
        return None
    pos = candle_index.searchsorted(ts, side="right") - 1
    if pos < 0:
        return None
    return candles.iloc[int(pos)]


def time_since_previous_entered(entry_ts: pd.Timestamp | None, entered_times: list[pd.Timestamp]) -> float | None:
    if entry_ts is None:
        return None
    previous = [ts for ts in entered_times if ts < entry_ts]
    if not previous:
        return None
    return (entry_ts - previous[-1]).total_seconds() / 3600.0


def session_label(ts: pd.Timestamp | None) -> str:
    if ts is None:
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


def weekday_label(ts: pd.Timestamp | None) -> str:
    if ts is None:
        return "unknown"
    return ts.day_name()


def quantile_pair(values: list[float]) -> tuple[float | None, float | None]:
    if len(values) < 3:
        return None, None
    series = pd.Series(values)
    return float(series.quantile(0.33)), float(series.quantile(0.66))


def volatility_bucket(value: float | None, low_q: float | None, high_q: float | None) -> str:
    if value is None or not math.isfinite(value) or low_q is None or high_q is None:
        return "unknown"
    if value <= low_q:
        return "low"
    if value >= high_q:
        return "high"
    return "medium"


def cost_bucket(cost_r: float | None) -> str:
    if cost_r is None or not math.isfinite(cost_r):
        return "unknown"
    if cost_r <= 0.02:
        return "low_cost"
    if cost_r <= 0.05:
        return "medium_cost"
    return "high_cost"


def time_since_bucket(hours: float | None) -> str:
    if hours is None or not math.isfinite(hours):
        return "first_trade_or_unknown"
    if hours < 6:
        return "under_6h"
    if hours < 24:
        return "6h_to_24h"
    if hours < 72:
        return "1d_to_3d"
    return "over_3d"


def distance_bucket(distance_atr: float | None) -> str:
    if distance_atr is None or not math.isfinite(distance_atr):
        return "unknown"
    if distance_atr < 0.0:
        return "inside_breakout_level"
    if distance_atr <= 0.25:
        return "near_breakout"
    if distance_atr <= 0.75:
        return "moderate_extension"
    return "far_extension"


def breakout_distance(direction: str, close: float | None, prev_high: float | None, prev_low: float | None) -> float | None:
    if close is None:
        return None
    if direction == "BUY" and prev_high is not None:
        return close - prev_high
    if direction == "SELL" and prev_low is not None:
        return prev_low - close
    return None


def infer_breakout_direction(direction: str, close: float | None, prev_high: float | None, prev_low: float | None) -> str:
    if direction == "BUY":
        if close is not None and prev_high is not None and close > prev_high:
            return "up"
        return "up_assumed"
    if direction == "SELL":
        if close is not None and prev_low is not None and close < prev_low:
            return "down"
        return "down_assumed"
    return "unknown"


def market_conditions_text(session: str, weekday: str, volatility: str, atr: float | None, distance_atr: float | None) -> str:
    atr_text = "unknown" if atr is None else f"{atr:.4f}"
    distance_text = "unknown" if distance_atr is None else f"{distance_atr:.3f}"
    return f"session={session}; weekday={weekday}; volatility={volatility}; atr={atr_text}; breakout_distance_atr={distance_text}"


def trade_outcome(realized_r: float | None, exit_reason: str) -> str:
    reason = exit_reason.lower()
    if any(token in reason for token in ("expired", "timeout", "backtest_end")):
        return "expired"
    if realized_r is None:
        return "open"
    return "win" if realized_r > 0.0 else "loss"


def pct(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def mean(values: list[float | None]) -> float:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return sum(clean) / len(clean) if clean else 0.0


def median(values: list[float]) -> float:
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return 0.0
    midpoint = len(clean) // 2
    if len(clean) % 2:
        return clean[midpoint]
    return (clean[midpoint - 1] + clean[midpoint]) / 2.0


def profit_factor(r_values: list[float]) -> float:
    wins = sum(value for value in r_values if value > 0.0)
    losses = abs(sum(value for value in r_values if value <= 0.0))
    if losses == 0.0:
        return 10.0 if wins > 0.0 else 0.0
    return wins / losses


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


def safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def safe_int(value: Any) -> int | None:
    number = safe_float(value)
    return int(number) if number is not None else None


def safe_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    if isinstance(value, pd.Timestamp):
        ts = value
    else:
        ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def utc_timestamp(value: Any) -> pd.Timestamp:
    ts = safe_timestamp(value)
    if ts is None:
        return pd.Timestamp.now(tz="UTC")
    return ts


def iso_or_blank(value: Any) -> str:
    ts = safe_timestamp(value)
    return ts.isoformat() if ts is not None else ""


def blank_to_none(value: Any) -> Any:
    if value in ("", "None", "nan"):
        return None
    return value


def csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=json_default)
    return value


def json_default(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, TypeError):
            pass
    return str(value)
