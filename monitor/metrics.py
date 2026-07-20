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


# ---------------------------------------------------------------------------
# MAE/MFE — Trade exit quality analysis
# ---------------------------------------------------------------------------


def compute_mae_mfe(
    trades: pd.DataFrame,
    ohlcv: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compute Maximum Adverse Excursion (MAE) and Maximum Favorable Excursion (MFE)
    for each trade.

    MAE measures how far price moved AGAINST the position before exit.
    MFE measures how far price moved FOR the position before exit.

    A good exit strategy has:
    - Low MAE (doesn't let losses run beyond the stop)
    - Captures most of the MFE (doesn't leave too much on the table)

    Parameters
    ----------
    trades : pd.DataFrame
        Must have columns: entry_time, exit_time, direction, entry_price, r_multiple, exit_reason
    ohlcv : pd.DataFrame, optional
        OHLCV data with DatetimeIndex for intra-trade price analysis.
        If None, only basic computed fields are returned.

    Returns
    -------
    pd.DataFrame with columns: trade_id, direction, mae_pct, mfe_pct, mae_r, mfe_r,
    mfe_mae_ratio, efficiency, exit_reason
    """
    if trades.empty:
        return pd.DataFrame(columns=[
            "trade_id", "direction", "mae_pct", "mfe_pct", "mae_r", "mfe_r",
            "mfe_mae_ratio", "efficiency", exit_reason or "exit_reason",
        ])

    results = []
    for _, trade in trades.iterrows():
        entry_time = trade.get("entry_time")
        exit_time = trade.get("exit_time")
        direction = str(trade.get("direction", "BUY"))
        entry_price = float(trade.get("entry_price", 0.0))
        r_multiple = float(trade.get("r_multiple", trade.get("r", 0.0)))
        exit_reason = str(trade.get("exit_reason", trade.get("reason", "unknown")))
        net_pnl = float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0))))

        mae_pct = 0.0
        mfe_pct = 0.0
        efficiency = 0.0

        if ohlcv is not None and not ohlcv.empty and entry_time is not None and exit_time is not None:
            try:
                et = pd.Timestamp(entry_time)
                xt = pd.Timestamp(exit_time)
                mask = (ohlcv.index >= et) & (ohlcv.index <= xt)
                trade_candles = ohlcv[mask]

                if not trade_candles.empty and entry_price > 0:
                    if direction == "BUY":
                        mae = (trade_candles["low"].min() - entry_price) / entry_price
                        mfe = (trade_candles["high"].max() - entry_price) / entry_price
                    else:
                        mae = (entry_price - trade_candles["high"].max()) / entry_price
                        mfe = (entry_price - trade_candles["low"].min()) / entry_price

                    mae_pct = float(mae * 100)
                    mfe_pct = float(mfe * 100)
                    if abs(mfe) > 1e-12:
                        efficiency = float(net_pnl / (mfe * entry_price * abs(r_multiple) if r_multiple != 0 else 1))
            except (KeyError, ValueError, IndexError):
                pass

        mae_r = mae_pct / 100 * abs(r_multiple) / (abs(r_multiple) if r_multiple != 0 else 1) if r_multiple != 0 else 0
        mfe_r = mfe_pct / 100 * abs(r_multiple) / (abs(r_multiple) if r_multiple != 0 else 1) if r_multiple != 0 else 0
        mfe_mae_ratio = abs(mfe_pct / mae_pct) if abs(mae_pct) > 1e-12 else float("inf")

        results.append({
            "trade_id": trade.get("id", trade.get("position_id", len(results))),
            "direction": direction,
            "mae_pct": round(mae_pct, 4),
            "mfe_pct": round(mfe_pct, 4),
            "mae_r": round(mae_r, 4),
            "mfe_r": round(mfe_r, 4),
            "mfe_mae_ratio": round(mfe_mae_ratio, 4) if mfe_mae_ratio != float("inf") else 999.0,
            "efficiency": round(efficiency, 4),
            "exit_reason": exit_reason,
        })

    return pd.DataFrame(results)


def compute_r_distribution(trades: pd.DataFrame) -> dict[str, Any]:
    """Compute R-multiple distribution statistics from trade history.

    Returns
    -------
    dict with keys: r_mean, r_median, r_std, r_sharpe (mean/std),
    r_deciles {10..90}, win_rate, avg_win_r, avg_loss_r,
    consecutive_wins, consecutive_losses, r_by_session
    """
    if trades.empty:
        return {"r_mean": 0.0, "r_median": 0.0, "r_std": 0.0, "n_trades": 0}

    r_values = pd.to_numeric(
        trades.get("r_multiple", trades.get("r", pd.Series(dtype=float))),
        errors="coerce",
    ).dropna()

    if r_values.empty:
        return {"r_mean": 0.0, "r_median": 0.0, "r_std": 0.0, "n_trades": 0}

    wins = r_values[r_values > 0]
    losses = r_values[r_values <= 0]

    # Consecutive win/loss streaks
    streak = 0
    max_consecutive_wins = 0
    max_consecutive_losses = 0
    for r in r_values:
        if r > 0:
            streak = max(0, streak) + 1
            max_consecutive_wins = max(max_consecutive_wins, streak)
        else:
            streak = min(0, streak) - 1
            max_consecutive_losses = min(max_consecutive_losses, streak)

    # R by session (if session info available)
    r_by_session: dict[str, float] = {}
    if "session" in trades.columns:
        for session in trades["session"].unique():
            mask = trades["session"] == session
            session_r = r_values[mask.values] if hasattr(mask, "values") else r_values[mask]
            if len(session_r) > 0:
                r_by_session[str(session)] = round(float(session_r.mean()), 4)

    # Deciles
    deciles = {}
    for p in [10, 20, 30, 40, 50, 60, 70, 80, 90]:
        deciles[str(p)] = round(float(np.percentile(r_values, p)), 4)

    return {
        "r_mean": round(float(r_values.mean()), 4),
        "r_median": round(float(r_values.median()), 4),
        "r_std": round(float(r_values.std()), 4),
        "r_sharpe": round(float(r_values.mean() / r_values.std()), 4) if r_values.std() > 0 else 0.0,
        "n_trades": len(r_values),
        "n_wins": len(wins),
        "n_losses": len(losses),
        "win_rate": round(len(wins) / len(r_values), 4) if len(r_values) > 0 else 0.0,
        "avg_win_r": round(float(wins.mean()), 4) if len(wins) > 0 else 0.0,
        "avg_loss_r": round(float(losses.mean()), 4) if len(losses) > 0 else 0.0,
        "max_consecutive_wins": max_consecutive_wins,
        "max_consecutive_losses": abs(max_consecutive_losses),
        "r_deciles": deciles,
        "cumulative_r": round(float(r_values.sum()), 4),
        "r_by_session": r_by_session,
    }


# ---------------------------------------------------------------------------
# System health metrics
# ---------------------------------------------------------------------------

def load_system_health(db_path: str) -> dict[str, Any]:
    """Load system health indicators from the health file and paper DB.

    Reads the D4 paper trader health JSON and falls back to paper_trading DB.
    Health file is at repo_root/run/d4_paper_trader_health.json (4 levels up
    from aurum1/data/aurum1.sqlite3 or 3 levels up from whatever db_path is).
    Returns a dict suitable for dashboard display.
    """
    from pathlib import Path

    # Walk up to find repo root: aurum1/data/xxx.sqlite3 -> aurum1/data -> aurum1 -> .
    db_parents = Path(db_path).resolve().parents
    root = db_parents[2] if len(db_parents) > 2 else Path(".")
    health_file = root / "run" / "d4_paper_trader_health.json"
    health: dict[str, Any] = {
        "source": "none",
        "avg_entry_slippage": None,
        "avg_exit_slippage": None,
        "avg_spread_pips": None,
        "avg_latency_seconds": None,
        "min_latency_seconds": None,
        "max_latency_seconds": None,
        "missed_signals": 0,
        "missed_signal_reasons": [],
        "latest_candle_age_minutes": None,
        "trade_count": 0,
        "uptime_hours": 0.0,
        "total_signals": 0,
        "health_timestamp": None,
    }

    if health_file.exists():
        try:
            data = json.loads(health_file.read_text())
            health["source"] = "d4_health_file"
            health["avg_entry_slippage"] = data.get("avg_entry_slippage_units")
            health["avg_exit_slippage"] = data.get("avg_exit_slippage_units")
            health["avg_spread_pips"] = data.get("avg_spread_pips")
            health["avg_latency_seconds"] = data.get("avg_latency_seconds")
            health["min_latency_seconds"] = data.get("min_latency_seconds")
            health["max_latency_seconds"] = data.get("max_latency_seconds")
            health["missed_signals"] = data.get("missed_signals", 0)
            health["missed_signal_reasons"] = data.get("missed_signal_reasons", [])
            health["latest_candle_age_minutes"] = data.get("market_latest_candle_age_minutes")
            health["trade_count"] = data.get("trade_count", 0)
            health["uptime_hours"] = round(data.get("uptime_seconds", 0) / 3600.0, 1)
            health["total_signals"] = data.get("signals_seen", 0)
            health["health_timestamp"] = data.get("timestamp")
            return health
        except (json.JSONDecodeError, KeyError, OSError):
            pass

    # Fallback: read from paper_trading DB
    paper_db = Path(db_path).parent / "paper_trading.sqlite3"
    if paper_db.exists():
        try:
            with closing(sqlite3.connect(paper_db)) as conn:
                trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
                missed = conn.execute("SELECT COUNT(*) FROM missed_signals").fetchone()[0]
                health["source"] = "paper_db"
                health["trade_count"] = trade_count
                health["missed_signals"] = missed
        except sqlite3.Error:
            pass

    return health


__all__ = [
    "compute_drawdown_curve", "compute_mae_mfe", "compute_r_distribution",
    "compute_rolling_profit_factor", "compute_rolling_sharpe",
    "compute_rolling_win_rate", "get_system_status", "load_equity_curve",
    "latest_signal_snapshot", "next_event", "load_trade_log", "load_event_log",
    "load_system_health",
]
