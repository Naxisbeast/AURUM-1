"""Metric computation helpers for the AURUM-1 monitoring dashboard."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd



def load_equity_curve(db_path: str) -> pd.DataFrame:
    """Load timestamp/equity rows from performance_log, falling back to paper_trading account_snapshots."""

    path = Path(db_path)

    # Primary source: performance_log (legacy)
    if path.exists():
        with closing(sqlite3.connect(path)) as conn:
            try:
                raw = pd.read_sql_query(
                    "SELECT timestamp, metric_name, metric_value, payload_json FROM performance_log",
                    conn,
                )
            except (sqlite3.Error, pd.errors.DatabaseError):
                raw = pd.DataFrame()
        if not raw.empty:
            rows: list[dict[str, Any]] = []
            for record in raw.to_dict(orient="records"):
                equity = _equity_from_record(record)
                if equity is None:
                    continue
                rows.append({"timestamp": record.get("timestamp"), "equity": equity})
            if rows:
                frame = pd.DataFrame(rows)
                frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
                frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
                frame = frame.dropna(subset=["timestamp", "equity"])
                return frame.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)

    # Fallback: paper_trading account_snapshots (D4 paper trader)
    paper_db = path.parent / "paper_trading.sqlite3"
    if paper_db.exists():
        with closing(sqlite3.connect(paper_db)) as conn:
            try:
                raw = pd.read_sql_query(
                    "SELECT timestamp, equity FROM account_snapshots ORDER BY timestamp",
                    conn,
                )
            except (sqlite3.Error, pd.errors.DatabaseError):
                raw = pd.DataFrame()
        if not raw.empty:
            raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True, errors="coerce")
            raw["equity"] = pd.to_numeric(raw["equity"], errors="coerce")
            raw = raw.dropna(subset=["timestamp", "equity"])
            return raw.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)

    return pd.DataFrame(columns=["timestamp", "equity"])


def compute_rolling_sharpe(
    equity_curve: pd.DataFrame,
    window_days: int = 30,
) -> pd.Series:
    """Compute rolling annualized Sharpe from daily equity returns."""

    if equity_curve.empty or "equity" not in equity_curve.columns:
        return pd.Series(dtype="float64")
    curve = _time_indexed(equity_curve, "equity")
    if curve.empty:
        return pd.Series(dtype="float64")

    daily_equity = curve.resample("1D").last().ffill()
    daily_returns = daily_equity.pct_change()
    values: list[float] = []
    for timestamp in daily_equity.index:
        start = timestamp - pd.Timedelta(days=window_days)
        window = daily_returns.loc[(daily_returns.index > start) & (daily_returns.index <= timestamp)].dropna()
        values.append(_annualized_sharpe(window))
    daily_sharpe = pd.Series(values, index=daily_equity.index, dtype="float64")
    return daily_sharpe.reindex(curve.index, method="ffill").fillna(0.0)


def compute_rolling_profit_factor(
    trades: pd.DataFrame,
    window_days: int = 30,
) -> pd.Series:
    """Compute rolling gross-profit/gross-loss over closed trade P&L."""

    trades = _trade_frame(trades)
    if trades.empty:
        return pd.Series(dtype="float64")
    values = []
    for timestamp in trades.index:
        pnl = trades.loc[(trades.index > timestamp - pd.Timedelta(days=window_days)) & (trades.index <= timestamp), "pnl"]
        gross_profit = float(pnl[pnl > 0.0].sum())
        gross_loss = abs(float(pnl[pnl <= 0.0].sum()))
        if gross_loss == 0.0:
            values.append(10.0 if gross_profit > 0.0 else 0.0)
        else:
            values.append(min(gross_profit / gross_loss, 10.0))
    return pd.Series(values, index=trades.index, dtype="float64")


def compute_rolling_win_rate(
    trades: pd.DataFrame,
    window_days: int = 30,
) -> pd.Series:
    """Compute rolling win rate for closed trades."""

    trades = _trade_frame(trades)
    if trades.empty:
        return pd.Series(dtype="float64")
    values = []
    for timestamp in trades.index:
        pnl = trades.loc[(trades.index > timestamp - pd.Timedelta(days=window_days)) & (trades.index <= timestamp), "pnl"]
        values.append(float((pnl > 0.0).mean()) if len(pnl) else 0.0)
    return pd.Series(values, index=trades.index, dtype="float64")


def compute_drawdown_curve(
    equity_curve: pd.DataFrame,
) -> pd.Series:
    """Return drawdown percentage at each equity timestamp."""

    if equity_curve.empty or "equity" not in equity_curve.columns:
        return pd.Series(dtype="float64")
    curve = _time_indexed(equity_curve, "equity")
    if curve.empty:
        return pd.Series(dtype="float64")
    rolling_max = curve.cummax().replace(0.0, np.nan)
    return ((curve - rolling_max) / rolling_max).fillna(0.0)


def get_system_status(
    db_path: str,
    settings: dict,
) -> dict[str, Any]:
    """Return the dashboard status-bar fields without mutating system state."""

    broker_settings = settings.get("broker", {})
    risk_settings = settings.get("risk", {})
    mode = str(settings.get("signals", {}).get("default_machine_mode", "RULE_REGIME"))
    equity = float(broker_settings.get("paper_initial_equity", 10000.0))
    daily_pnl = 0.0
    open_positions = 0
    peak_equity = equity
    spread = float(settings.get("execution", {}).get("paper_spread_pips", 0.0))

    # Try reading from paper_trading account_snapshots first (avoids mutating broker state)
    paper_db = Path(db_path).parent / "paper_trading.sqlite3"
    if paper_db.exists():
        with closing(sqlite3.connect(paper_db)) as conn:
            try:
                row = conn.execute(
                    "SELECT equity, daily_pnl, peak_equity, position_count FROM account_snapshots ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if row:
                    equity = float(row[0])
                    daily_pnl = float(row[1]) if row[1] else 0.0
                    peak_equity = float(row[2]) if row[2] else equity
                    open_positions = int(row[3]) if row[3] else 0
            except sqlite3.Error:
                pass

    last_candle = _last_timestamp_from_tables(db_path)
    daily_kill = daily_pnl < -(equity * float(risk_settings.get("daily_loss_kill_pct", 0.03)))
    total_drawdown_kill = equity < peak_equity * (1.0 - float(risk_settings.get("total_drawdown_kill_pct", 0.08)))
    return {
        "system_mode": "PAPER" if bool(broker_settings.get("paper_trade", True)) else "LIVE",
        "last_candle_processed": last_candle,
        "open_positions": open_positions,
        "equity": equity,
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": (daily_pnl / equity) if equity else 0.0,
        "active_mode": mode,
        "blackout_active": False,
        "daily_kill_triggered": daily_kill,
        "total_drawdown_kill_triggered": total_drawdown_kill,
        "current_spread_pips": spread,
    }


def _time_indexed(frame: pd.DataFrame, value_column: str) -> pd.Series:
    work = frame.copy()
    if "timestamp" in work.columns:
        work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
        work = work.dropna(subset=["timestamp"]).set_index("timestamp")
    elif not isinstance(work.index, pd.DatetimeIndex):
        return pd.Series(dtype="float64")
    else:
        work.index = pd.to_datetime(work.index, utc=True, errors="coerce")
    values = pd.to_numeric(work[value_column], errors="coerce").dropna()
    return values.sort_index()


def _trade_frame(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "pnl" not in trades.columns:
        return pd.DataFrame(columns=["pnl"])
    work = trades.copy()
    timestamp_column = "timestamp" if "timestamp" in work.columns else "closed_at" if "closed_at" in work.columns else None
    if timestamp_column is None and isinstance(work.index, pd.DatetimeIndex):
        work.index = pd.to_datetime(work.index, utc=True, errors="coerce")
    elif timestamp_column is not None:
        work[timestamp_column] = pd.to_datetime(work[timestamp_column], utc=True, errors="coerce")
        work = work.dropna(subset=[timestamp_column]).set_index(timestamp_column)
    else:
        return pd.DataFrame(columns=["pnl"])
    work["pnl"] = pd.to_numeric(work["pnl"], errors="coerce")
    return work.dropna(subset=["pnl"]).sort_index()


def _annualized_sharpe(returns: pd.Series) -> float:
    if len(returns) < 2:
        return 0.0
    mean_return = float(returns.mean())
    std_return = float(returns.std())
    if std_return == 0.0:
        return 10.0 if mean_return > 0.0 else 0.0
    return float((mean_return / std_return) * np.sqrt(252.0))


def _equity_from_record(record: dict[str, Any]) -> float | None:
    metric_name = str(record.get("metric_name", "")).lower()
    if metric_name in {"equity", "account_equity", "paper_equity"} and record.get("metric_value") is not None:
        return _float_or_none(record.get("metric_value"))
    payload = _json_payload(record.get("payload_json"))
    for key in ("equity", "account_equity", "paper_equity", "balance"):
        if key in payload:
            value = _float_or_none(payload.get(key))
            if value is not None:
                return value
    return None


def _last_timestamp_from_tables(db_path: str) -> datetime | None:
    path = Path(db_path)
    if not path.exists():
        return None
    with closing(sqlite3.connect(path)) as conn:
        candidates: list[pd.Timestamp] = []
        for table, column in (("performance_log", "timestamp"), ("trades_log", "timestamp"), ("ohlcv_M15", "timestamp")):
            try:
                row = conn.execute(f"SELECT MAX({column}) FROM {table}").fetchone()
            except sqlite3.Error:
                continue
            if row and row[0]:
                timestamp = pd.to_datetime(row[0], utc=True, errors="coerce")
                if not pd.isna(timestamp):
                    candidates.append(timestamp)
    if not candidates:
        return None
    return max(candidates).to_pydatetime()


def _json_payload(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
