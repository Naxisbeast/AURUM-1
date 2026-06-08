"""Read-only Streamlit dashboard for AURUM-1 forward-shadow monitoring."""

from __future__ import annotations

import json
import importlib
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SHADOW_DB = ROOT / "reports" / "forward_shadow" / "donchian_shadow.sqlite3"
MARKET_DB = ROOT / "aurum1" / "data" / "forward_shadow_market_cache.sqlite3"
REPORT_DIR = ROOT / "reports" / "forward_shadow"
LOG_FILE = ROOT / "logs" / "forward_shadow_donchian.log"
STRATEGY_NAME = "raw_donchian_fixed_2r"
SERVICE_MODE = "research-only"
STALE_CANDLE_MINUTES = 45.0
FIXED_REWARD_R = 2.0
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
WHAT_IF_COLUMNS = [
    "skip_time",
    "reason",
    "direction",
    "price_at_skip_time",
    "rule_safety_reason",
    "block_category",
    "hypothetical_entry_time",
    "hypothetical_entry",
    "hypothetical_stop",
    "hypothetical_tp",
    "hypothetical_exit_time",
    "hypothetical_outcome",
    "hypothetical_R",
    "candles_held",
]


class LazyModule:
    def __init__(self, module_name: str) -> None:
        self.module_name = module_name
        self.loaded: Any | None = None

    def __getattr__(self, name: str) -> Any:
        if self.loaded is None:
            self.loaded = importlib.import_module(self.module_name)
        return getattr(self.loaded, name)


st = LazyModule("streamlit")
go = LazyModule("plotly.graph_objects")


def main() -> None:
    st.set_page_config(page_title="AURUM-1 Forward Shadow", layout="wide")
    st.title("AURUM-1 Forward Shadow")

    snapshot = load_shadow_snapshot(SHADOW_DB)
    status = build_status(snapshot)
    market_candles, market_error = load_market_candles(MARKET_DB)
    what_ifs = simulate_skipped_signal_what_ifs(snapshot["signals"], market_candles)
    render_header(status, snapshot["errors"])

    render_equity_curve(snapshot["equity"])
    render_drawdown(snapshot["equity"])
    render_candle_chart(snapshot["trades"], what_ifs, market_candles, market_error)
    render_trade_list(snapshot["trades"])
    render_signal_list(snapshot["signals"])
    render_skipped_what_if_analysis(what_ifs, market_error)
    render_weekly_report_viewer(REPORT_DIR)
    render_recent_events(snapshot["events"])

    st.caption(f"Ledger: {SHADOW_DB}")
    if LOG_FILE.exists():
        st.caption(f"Log: {LOG_FILE}")


def connect_readonly_sqlite(path: Path | str) -> tuple[sqlite3.Connection | None, str | None]:
    db_path = resolve_path(path)
    if not db_path.exists():
        return None, f"Missing database: {db_path}"
    try:
        conn = sqlite3.connect(sqlite_read_uri(db_path), uri=True, timeout=5.0)
        conn.execute("PRAGMA query_only = ON")
        return conn, None
    except sqlite3.Error as exc:
        return None, f"Could not open database: {db_path} ({exc})"


def resolve_path(path: Path | str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return ROOT / candidate


def sqlite_read_uri(path: Path) -> str:
    encoded_path = quote(path.resolve(strict=False).as_posix(), safe="/:")
    return f"file:{encoded_path}?mode=ro"


def read_sqlite_dataframe(path: Path | str, query: str, params: tuple[Any, ...] = ()) -> tuple[pd.DataFrame, str | None]:
    if not is_read_query(query):
        raise ValueError("Only read queries are allowed")
    conn, error = connect_readonly_sqlite(path)
    if conn is None:
        return pd.DataFrame(), error
    with closing(conn):
        try:
            return pd.read_sql_query(query, conn, params=params), None
        except (sqlite3.Error, pd.errors.DatabaseError) as exc:
            return pd.DataFrame(), str(exc)


def is_read_query(query: str) -> bool:
    return bool(re.match(r"^\s*(SELECT|WITH)\b", query, flags=re.IGNORECASE))


def load_shadow_snapshot(path: Path) -> dict[str, Any]:
    equity, equity_error = load_table(path, "shadow_equity_curve", ["timestamp", "equity", "drawdown"], order_by="timestamp")
    trades, trades_error = load_table(
        path,
        "shadow_trades",
        [
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
        ],
        order_by="entry_time",
    )
    signals, signals_error = load_table(
        path,
        "shadow_signals",
        [
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
        ],
        order_by="signal_time",
    )
    candles, candles_error = load_table(path, "shadow_candles", ["timestamp", "open", "high", "low", "close", "volume", "signal_decision", "notes"], order_by="timestamp")
    events, events_error = load_table(path, "shadow_events", ["id", "event_time", "event_type", "severity", "message", "details"], order_by="event_time")
    run_log, run_log_error = load_table(path, "shadow_run_log", ["run_at", "strategy", "signal_count", "trade_count", "skipped_count", "notes"], order_by="run_at")

    normalize_time_columns(equity, ["timestamp"])
    normalize_time_columns(trades, ["signal_time", "entry_time", "exit_time"])
    normalize_time_columns(signals, ["signal_time", "entry_time", "exit_time"])
    normalize_time_columns(candles, ["timestamp"])
    normalize_time_columns(events, ["event_time"])
    normalize_time_columns(run_log, ["run_at"])

    errors = [item for item in [equity_error, trades_error, signals_error, candles_error, events_error, run_log_error] if item]
    return {
        "equity": equity,
        "trades": trades,
        "signals": signals,
        "candles": candles,
        "events": events,
        "run_log": run_log,
        "errors": errors,
    }


def load_table(path: Path | str, table: str, columns: list[str], *, order_by: str | None = None, limit: int | None = None) -> tuple[pd.DataFrame, str | None]:
    table_sql = quote_identifier(table)
    column_sql = ", ".join(quote_identifier(column) for column in columns)
    query = f"SELECT {column_sql} FROM {table_sql}"
    if order_by is not None:
        query += f" ORDER BY {quote_identifier(order_by)} ASC"
    params: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (int(limit),)
    frame, error = read_sqlite_dataframe(path, query, params)
    if error:
        return pd.DataFrame(columns=columns), error
    return frame, None


def quote_identifier(value: str) -> str:
    if not IDENTIFIER_RE.match(value):
        raise ValueError(f"Unsafe identifier: {value}")
    return f'"{value}"'


def normalize_time_columns(frame: pd.DataFrame, columns: list[str]) -> None:
    if frame.empty:
        return
    for column in columns:
        if column in frame:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")


def build_status(snapshot: dict[str, Any]) -> dict[str, Any]:
    equity = snapshot["equity"]
    trades = snapshot["trades"]
    signals = snapshot["signals"]
    candles = snapshot["candles"]
    events = snapshot["events"]
    run_log = snapshot["run_log"]
    now = pd.Timestamp.now(tz="UTC")
    latest_candle = latest_timestamp(candles, "timestamp")
    stale = stale_data_status(latest_candle, now)
    gaps = data_gap_summary(candles)
    latest_run = latest_timestamp(run_log, "run_at")
    return {
        "service_mode": SERVICE_MODE,
        "strategy": STRATEGY_NAME,
        "latest_candle": latest_candle,
        "latest_equity": latest_number(equity, "equity"),
        "latest_drawdown": latest_number(equity, "drawdown"),
        "signal_count": len(signals.index),
        "trade_count": len(trades.index),
        "skipped_count": int(signals["status"].astype(str).eq("skipped").sum()) if "status" in signals else 0,
        "errors_24h": errors_since(events, now - pd.Timedelta(hours=24)),
        "stale_data": stale,
        "data_gaps": gaps,
        "market_pause": stale["market_pause"],
        "latest_run": latest_run,
    }


def latest_timestamp(frame: pd.DataFrame, column: str) -> pd.Timestamp | None:
    if frame.empty or column not in frame:
        return None
    series = frame[column].dropna()
    if series.empty:
        return None
    return series.max()


def latest_number(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.iloc[-1])


def errors_since(events: pd.DataFrame, since: pd.Timestamp) -> int:
    if events.empty or "event_time" not in events or "severity" not in events:
        return 0
    recent = events[events["event_time"] >= since]
    return int(recent["severity"].astype(str).str.upper().eq("ERROR").sum())


def data_gap_summary(candles: pd.DataFrame) -> dict[str, Any]:
    if candles.empty or "timestamp" not in candles:
        return {"count": 0, "max_gap_minutes": 0.0, "latest_gap": None}
    times = candles["timestamp"].dropna().sort_values()
    if len(times.index) < 2:
        return {"count": 0, "max_gap_minutes": 0.0, "latest_gap": None}
    gaps = times.diff().dt.total_seconds().div(60.0).dropna()
    large = gaps[gaps > 30.0]
    return {
        "count": int(len(large.index)),
        "max_gap_minutes": float(gaps.max()) if not gaps.empty else 0.0,
        "latest_gap": float(large.iloc[-1]) if not large.empty else None,
    }


def stale_data_status(latest_candle: pd.Timestamp | None, as_of: pd.Timestamp) -> dict[str, Any]:
    market_pause = is_weekend_market_pause(as_of)
    if latest_candle is None:
        return {
            "is_stale": True,
            "age_minutes": None,
            "threshold_minutes": STALE_CANDLE_MINUTES,
            "market_pause": market_pause,
            "reason": "no candles",
        }
    age_minutes = max(0.0, float((as_of - latest_candle).total_seconds() / 60.0))
    is_stale = bool(age_minutes > STALE_CANDLE_MINUTES and not market_pause)
    return {
        "is_stale": is_stale,
        "age_minutes": age_minutes,
        "threshold_minutes": STALE_CANDLE_MINUTES,
        "market_pause": market_pause,
        "reason": "stale candle" if is_stale else "ok",
    }


def is_weekend_market_pause(timestamp: pd.Timestamp) -> bool:
    ts = pd.Timestamp(timestamp)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    ts = ts.tz_convert("UTC")
    weekday = ts.weekday()
    hour = ts.hour
    if weekday in {5, 6}:
        return True
    if weekday == 4 and hour >= 22:
        return True
    if weekday == 0 and hour < 22:
        return True
    return False


def load_market_candles(path: Path | str, *, limit: int | None = None) -> tuple[pd.DataFrame, str | None]:
    query = """
        SELECT "timestamp", "open", "high", "low", "close", "volume"
        FROM "ohlcv_M15"
        ORDER BY "timestamp" ASC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (int(limit),)
    frame, error = read_sqlite_dataframe(path, query, params)
    if error:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"]), error
    normalize_time_columns(frame, ["timestamp"])
    for column in ["open", "high", "low", "close", "volume"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")
    frame.index = range(len(frame.index))
    return frame, None


def simulate_skipped_signal_what_ifs(signals: pd.DataFrame, candles: pd.DataFrame) -> pd.DataFrame:
    skipped = skipped_signal_rows(signals)
    if skipped.empty:
        return pd.DataFrame(columns=WHAT_IF_COLUMNS)
    prepared_candles = prepare_candles_for_simulation(candles)
    rows = [simulate_one_skipped_signal(row, prepared_candles) for _, row in skipped.iterrows()]
    return pd.DataFrame(rows, columns=WHAT_IF_COLUMNS)


def skipped_signal_rows(signals: pd.DataFrame) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame()
    work = signals.copy()
    for column in ["signal_time", "direction", "status", "skip_reason", "entry_price", "stop_loss", "take_profit", "atr"]:
        if column not in work:
            work[column] = None
    work["signal_time"] = pd.to_datetime(work["signal_time"], utc=True, errors="coerce")
    status = work["status"].astype(str).str.lower()
    result = work[status.eq("skipped")].sort_values("signal_time")
    result.index = range(len(result.index))
    return result


def prepare_candles_for_simulation(candles: pd.DataFrame) -> pd.DataFrame:
    if candles.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    work = candles.copy()
    for column in ["timestamp", "open", "high", "low", "close"]:
        if column not in work:
            work[column] = None
    normalize_time_columns(work, ["timestamp"])
    for column in ["open", "high", "low", "close", "volume"]:
        if column in work:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    result = work.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")
    result.index = range(len(result.index))
    return result


def simulate_one_skipped_signal(signal: pd.Series, candles: pd.DataFrame) -> dict[str, Any]:
    skip_time = safe_timestamp(signal.get("signal_time"))
    direction = safe_text(signal.get("direction")).upper()
    reason = safe_text(signal.get("skip_reason")) or "unknown"
    base = {
        "skip_time": skip_time,
        "reason": reason,
        "direction": direction or "unknown",
        "price_at_skip_time": price_at_or_after(candles, skip_time, "close", fallback=safe_float(signal.get("entry_price"))),
        "rule_safety_reason": reason,
        "block_category": classify_skip_reason(reason),
        "hypothetical_entry_time": None,
        "hypothetical_entry": None,
        "hypothetical_stop": None,
        "hypothetical_tp": None,
        "hypothetical_exit_time": None,
        "hypothetical_outcome": "not_simulated",
        "hypothetical_R": None,
        "candles_held": None,
    }
    if skip_time is None:
        base["hypothetical_outcome"] = "missing_skip_time"
        return base
    if direction != "BUY":
        base["hypothetical_outcome"] = "buy_only_analysis"
        return base
    if candles.empty:
        base["hypothetical_outcome"] = "no_market_data"
        return base

    entry_index = first_candle_after(candles, skip_time)
    if entry_index is None:
        base["hypothetical_outcome"] = "no_future_candle"
        return base
    entry = safe_float(candles.loc[entry_index, "open"])
    stop = resolve_hypothetical_stop(signal, entry)
    base["hypothetical_entry_time"] = candles.loc[entry_index, "timestamp"]
    if entry is None or stop is None or stop >= entry:
        base["hypothetical_outcome"] = "insufficient_stop_data"
        base["hypothetical_entry"] = entry
        base["hypothetical_stop"] = stop
        return base
    risk_distance = entry - stop
    target = entry + FIXED_REWARD_R * risk_distance
    base["hypothetical_entry"] = entry
    base["hypothetical_stop"] = stop
    base["hypothetical_tp"] = target

    scan = candles.iloc[entry_index + 1 :]
    if scan.empty:
        base["hypothetical_exit_time"] = candles.loc[entry_index, "timestamp"]
        base["hypothetical_outcome"] = "time_exit"
        base["hypothetical_R"] = 0.0
        base["candles_held"] = 0
        return base
    for candle_index, candle in scan.iterrows():
        outcome = candle_exit_result(candle, entry, stop, target, risk_distance)
        if outcome is None:
            continue
        exit_time, result, r_value = outcome
        base["hypothetical_exit_time"] = exit_time
        base["hypothetical_outcome"] = result
        base["hypothetical_R"] = r_value
        base["candles_held"] = int(candle_index - entry_index)
        return base

    final = scan.iloc[-1]
    base["hypothetical_exit_time"] = final["timestamp"]
    base["hypothetical_outcome"] = "time_exit"
    base["hypothetical_R"] = (float(final["close"]) - entry) / risk_distance
    base["candles_held"] = int(scan.index[-1] - entry_index)
    return base


def candle_exit_result(candle: pd.Series, entry: float, stop: float, target: float, risk_distance: float) -> tuple[pd.Timestamp, str, float] | None:
    open_price = float(candle["open"])
    low = float(candle["low"])
    high = float(candle["high"])
    timestamp = candle["timestamp"]
    if open_price <= stop:
        return timestamp, "stop_loss_gap", (open_price - entry) / risk_distance
    if low <= stop:
        return timestamp, "stop_loss", -1.0
    if high >= target:
        return timestamp, "take_profit", FIXED_REWARD_R
    return None


def resolve_hypothetical_stop(signal: pd.Series, entry: float | None) -> float | None:
    if entry is None:
        return None
    stop = safe_float(signal.get("stop_loss"))
    if stop is not None:
        return stop
    atr = safe_float(signal.get("atr"))
    if atr is not None and atr > 0.0:
        return entry - 2.0 * atr
    take_profit = safe_float(signal.get("take_profit"))
    if take_profit is not None and take_profit > entry:
        return entry - ((take_profit - entry) / FIXED_REWARD_R)
    return None


def first_candle_after(candles: pd.DataFrame, timestamp: pd.Timestamp) -> int | None:
    matches = candles.index[candles["timestamp"] > timestamp].tolist()
    return int(matches[0]) if matches else None


def price_at_or_after(candles: pd.DataFrame, timestamp: pd.Timestamp | None, column: str, *, fallback: float | None = None) -> float | None:
    if timestamp is None or candles.empty or column not in candles:
        return fallback
    exact = candles[candles["timestamp"] == timestamp]
    if not exact.empty:
        return safe_float(exact.iloc[0].get(column), fallback=fallback)
    later = candles[candles["timestamp"] > timestamp]
    if not later.empty:
        return safe_float(later.iloc[0].get(column), fallback=fallback)
    return fallback


def classify_skip_reason(reason: str) -> str:
    text = reason.lower()
    if any(token in text for token in ["open_position", "duplicate", "already", "idempotent"]):
        return "duplicate prevention"
    if any(token in text for token in ["stale", "data", "gap", "feed"]):
        return "stale data"
    if any(token in text for token in ["risk", "spread", "drawdown", "loss", "kill", "sizing", "unit"]):
        return "risk"
    if any(token in text for token in ["donchian", "strategy", "rule", "breakout", "filter", "direction"]):
        return "strategy rule"
    return "another reason"


def skipped_what_if_summary(outcomes: pd.DataFrame) -> dict[str, Any]:
    if outcomes.empty:
        return {
            "total_skipped": 0,
            "simulated": 0,
            "winners": 0,
            "losers": 0,
            "average_r": None,
            "common_reason": "n/a",
            "impact": "n/a",
        }
    r_values = pd.to_numeric(outcomes["hypothetical_R"], errors="coerce").dropna()
    simulated = outcomes[outcomes["hypothetical_outcome"].isin(["take_profit", "stop_loss", "stop_loss_gap", "time_exit"])]
    common_reason = outcomes["reason"].astype(str).mode()
    average_r = float(r_values.mean()) if not r_values.empty else None
    if average_r is None:
        impact = "n/a"
    elif average_r > 0.0:
        impact = "would have improved"
    elif average_r < 0.0:
        impact = "would have worsened"
    else:
        impact = "neutral"
    return {
        "total_skipped": int(len(outcomes.index)),
        "simulated": int(len(simulated.index)),
        "winners": int((r_values > 0.0).sum()),
        "losers": int((r_values < 0.0).sum()),
        "average_r": average_r,
        "common_reason": str(common_reason.iloc[0]) if not common_reason.empty else "n/a",
        "impact": impact,
    }


def safe_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    timestamp = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(timestamp):
        return None
    return pd.Timestamp(timestamp)


def safe_float(value: Any, *, fallback: float | None = None) -> float | None:
    if value is None or pd.isna(value):
        return fallback
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    if pd.isna(result):
        return fallback
    return result


def safe_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)


def render_header(status: dict[str, Any], load_errors: list[str]) -> None:
    status_text = "stale" if status["stale_data"]["is_stale"] else "fresh"
    pause_text = "paused" if status["market_pause"] else "open"
    cards = [
        ("Service mode", status["service_mode"]),
        ("Strategy", status["strategy"]),
        ("Latest candle", format_time(status["latest_candle"])),
        ("Latest equity", format_currency(status["latest_equity"])),
        ("Latest drawdown", format_percent(status["latest_drawdown"])),
        ("Signals", f"{status['signal_count']:,}"),
        ("Trades", f"{status['trade_count']:,}"),
        ("Skipped signals", f"{status['skipped_count']:,}"),
        ("Errors 24h", f"{status['errors_24h']:,}"),
        ("Stale data", status_text),
        ("Data gaps", f"{status['data_gaps']['count']:,}"),
        ("Market", pause_text),
    ]
    for offset in range(0, len(cards), 4):
        columns = st.columns(4)
        for column, (label, value) in zip(columns, cards[offset : offset + 4]):
            column.metric(label, value)
    st.caption("research-only | forward shadow only | broker orders disabled | live trading disabled")
    for error in load_errors:
        st.warning(error)


def render_equity_curve(equity: pd.DataFrame) -> None:
    st.subheader("Equity Curve")
    if equity.empty:
        st.info("No equity rows found.")
        return
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=equity["timestamp"],
            y=equity["equity"],
            mode="lines",
            name="Equity",
            line={"color": "#2563eb", "width": 2},
        )
    )
    fig.update_layout(height=360, margin={"l": 20, "r": 20, "t": 20, "b": 30}, yaxis_title="Equity")
    st.plotly_chart(fig, use_container_width=True)


def render_drawdown(equity: pd.DataFrame) -> None:
    st.subheader("Drawdown")
    if equity.empty or "drawdown" not in equity:
        st.info("No drawdown rows found.")
        return
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=equity["timestamp"],
            y=pd.to_numeric(equity["drawdown"], errors="coerce") * 100.0,
            mode="lines",
            fill="tozeroy",
            name="Drawdown",
            line={"color": "#dc2626", "width": 2},
        )
    )
    fig.update_layout(height=300, margin={"l": 20, "r": 20, "t": 20, "b": 30}, yaxis_title="Drawdown %")
    st.plotly_chart(fig, use_container_width=True)


def render_candle_chart(trades: pd.DataFrame, what_ifs: pd.DataFrame, candles: pd.DataFrame, market_error: str | None) -> None:
    st.subheader("M15 Close With Trade Markers")
    if market_error:
        st.info(market_error)
        return
    if candles.empty:
        st.info("No M15 candles found.")
        return
    marker_filter = st.selectbox("Marker filter", ["Both", "Actual trades only", "Skipped what-if only"])
    show_actual = marker_filter in {"Both", "Actual trades only"}
    show_skipped = marker_filter in {"Both", "Skipped what-if only"}
    chart_candles = candles.tail(1500).copy()
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=chart_candles["timestamp"],
            y=chart_candles["close"],
            mode="lines",
            name="M15 close",
            line={"color": "#334155", "width": 1.6},
        )
    )
    entries, exits = trade_markers_for_range(trades, chart_candles["timestamp"].min(), chart_candles["timestamp"].max())
    skipped_entries = what_if_markers_for_range(what_ifs, chart_candles["timestamp"].min(), chart_candles["timestamp"].max())
    if show_actual and not entries.empty:
        fig.add_trace(
            go.Scatter(
                x=entries["entry_time"],
                y=entries["entry_price"],
                mode="markers",
                name="Actual entry",
                marker={"color": "#16a34a", "size": 10, "symbol": "triangle-up"},
            )
        )
    if show_actual and not exits.empty:
        fig.add_trace(
            go.Scatter(
                x=exits["exit_time"],
                y=exits["exit_price"],
                mode="markers",
                name="Actual exit",
                marker={"color": "#dc2626", "size": 10, "symbol": "x"},
            )
        )
    if show_skipped and not skipped_entries.empty:
        fig.add_trace(
            go.Scatter(
                x=skipped_entries["hypothetical_entry_time"],
                y=skipped_entries["hypothetical_entry"],
                mode="markers",
                name="Skipped what-if entry",
                marker={"color": "#eab308", "size": 11, "symbol": "circle-open", "line": {"color": "#eab308", "width": 2}},
            )
        )
        selected = select_skipped_overlay(skipped_entries)
        if selected is not None:
            add_skipped_target_lines(fig, selected, chart_candles["timestamp"].max())
    fig.update_layout(height=420, margin={"l": 20, "r": 20, "t": 20, "b": 30}, yaxis_title="XAU/USD")
    st.plotly_chart(fig, use_container_width=True)


def trade_markers_for_range(trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame()
    entries = trades[(trades["entry_time"] >= start) & (trades["entry_time"] <= end)].copy()
    exits = trades[(trades["exit_time"] >= start) & (trades["exit_time"] <= end)].copy()
    return entries, exits


def what_if_markers_for_range(what_ifs: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if what_ifs.empty or "hypothetical_entry_time" not in what_ifs:
        return pd.DataFrame()
    entries = what_ifs.dropna(subset=["hypothetical_entry_time", "hypothetical_entry"]).copy()
    return entries[(entries["hypothetical_entry_time"] >= start) & (entries["hypothetical_entry_time"] <= end)]


def select_skipped_overlay(skipped_entries: pd.DataFrame) -> pd.Series | None:
    choices: list[tuple[str, int | None]] = [("No TP/SL guide", None)]
    for index, row in skipped_entries.tail(100).iterrows():
        choices.append((f"{format_time(row.get('skip_time'))} | {safe_text(row.get('reason'))}", int(index)))
    label = st.selectbox("Skipped TP/SL guide", choices, format_func=lambda item: item[0])
    if label[1] is None:
        return None
    return skipped_entries.loc[label[1]]


def add_skipped_target_lines(fig: Any, row: pd.Series, chart_end: pd.Timestamp) -> None:
    start = row.get("hypothetical_entry_time")
    stop = safe_float(row.get("hypothetical_stop"))
    target = safe_float(row.get("hypothetical_tp"))
    if start is None or pd.isna(start):
        return
    end = row.get("hypothetical_exit_time")
    if end is None or pd.isna(end):
        end = chart_end
    if target is not None:
        fig.add_trace(
            go.Scatter(
                x=[start, end],
                y=[target, target],
                mode="lines",
                name="What-if TP",
                line={"color": "#eab308", "width": 1.5, "dash": "dash"},
            )
        )
    if stop is not None:
        fig.add_trace(
            go.Scatter(
                x=[start, end],
                y=[stop, stop],
                mode="lines",
                name="What-if SL",
                line={"color": "#f59e0b", "width": 1.5, "dash": "dash"},
            )
        )


def render_trade_list(trades: pd.DataFrame) -> None:
    st.subheader("Trades")
    if trades.empty:
        st.info("No shadow trades found.")
        return
    display = trades.sort_values("entry_time", ascending=False).rename(
        columns={
            "entry_time": "entry timestamp",
            "exit_time": "exit timestamp",
            "stop_loss": "stop price",
            "gross_pnl": "gross P&L",
            "net_pnl": "net P&L",
            "r_multiple": "R multiple",
        }
    )
    columns = [
        "entry timestamp",
        "exit timestamp",
        "direction",
        "entry_price",
        "stop price",
        "take_profit",
        "exit_reason",
        "gross P&L",
        "net P&L",
        "R multiple",
    ]
    st.dataframe(display[columns], use_container_width=True, hide_index=True)


def render_signal_list(signals: pd.DataFrame) -> None:
    st.subheader("Signals")
    if signals.empty:
        st.info("No shadow signals found.")
        return
    all_signals = signals.sort_values("signal_time", ascending=False).rename(
        columns={
            "signal_time": "signal timestamp",
            "entry_time": "entry timestamp",
            "skip_reason": "skip reason",
            "stop_loss": "stop price",
        }
    )
    skipped = all_signals[all_signals["status"].astype(str).eq("skipped")].copy()
    signal_columns = [
        "signal timestamp",
        "entry timestamp",
        "direction",
        "status",
        "skip reason",
        "entry_price",
        "stop price",
        "take_profit",
        "exit_time",
        "exit_reason",
    ]
    tab_all, tab_skipped = st.tabs(["All signals", "Skipped signals"])
    with tab_all:
        st.dataframe(all_signals[signal_columns], use_container_width=True, hide_index=True)
    with tab_skipped:
        if skipped.empty:
            st.info("No skipped signals found.")
        else:
            st.dataframe(skipped[signal_columns], use_container_width=True, hide_index=True)


def render_skipped_what_if_analysis(outcomes: pd.DataFrame, market_error: str | None) -> None:
    st.subheader("Skipped Signal What-If Analysis")
    if market_error:
        st.info(market_error)
    if outcomes.empty:
        st.info("No skipped signals found for what-if analysis.")
        return
    summary = skipped_what_if_summary(outcomes)
    metrics = [
        ("Total skipped signals", f"{summary['total_skipped']:,}"),
        ("Skipped signals simulated", f"{summary['simulated']:,}"),
        ("Hypothetical winners", f"{summary['winners']:,}"),
        ("Hypothetical losers", f"{summary['losers']:,}"),
        ("Hypothetical average R", format_r(summary["average_r"])),
        ("Most common skip reason", summary["common_reason"]),
        ("What-if impact", summary["impact"]),
    ]
    for offset in range(0, len(metrics), 4):
        columns = st.columns(4)
        for column, (label, value) in zip(columns, metrics[offset : offset + 4]):
            column.metric(label, value)

    detail_columns = [
        "skip_time",
        "reason",
        "direction",
        "price_at_skip_time",
        "rule_safety_reason",
        "block_category",
    ]
    st.caption("Skipped Signal Details")
    st.dataframe(outcomes[detail_columns].sort_values("skip_time", ascending=False), use_container_width=True, hide_index=True)

    outcome_columns = [
        "skip_time",
        "reason",
        "hypothetical_entry",
        "hypothetical_stop",
        "hypothetical_tp",
        "hypothetical_exit_time",
        "hypothetical_outcome",
        "hypothetical_R",
        "candles_held",
    ]
    st.caption("Skipped Signal Outcomes")
    st.dataframe(outcomes[outcome_columns].sort_values("skip_time", ascending=False), use_container_width=True, hide_index=True)


def render_weekly_report_viewer(report_dir: Path) -> None:
    st.subheader("Weekly Report")
    reports = list_weekly_reports(report_dir)
    if not reports:
        st.info(f"No weekly JSON reports found in {report_dir}.")
        return
    selected = st.selectbox("Report", reports, index=0, format_func=lambda item: item.name)
    report, error = load_weekly_report(selected)
    if error:
        st.warning(error)
        return
    fields = [
        ("Gross P&L", "gross_pnl", "currency"),
        ("Net P&L", "net_pnl", "currency"),
        ("PF", "profit_factor", "number"),
        ("Sharpe estimate", "sharpe_estimate", "number"),
        ("Max drawdown", "max_drawdown", "percent_abs"),
        ("Trades", "trade_count", "integer"),
        ("Win rate", "win_rate", "percent"),
        ("Average R", "average_r", "number"),
        ("Median R", "median_r", "number"),
        ("Skipped signals", "skipped_signals", "integer"),
        ("API failures", "api_failures", "integer"),
        ("Runtime errors", "runtime_errors", "integer"),
        ("Data gaps", "data_gaps", "gap_count"),
        ("Health", "health", "health"),
    ]
    for offset in range(0, len(fields), 4):
        columns = st.columns(4)
        for column, (label, key, kind) in zip(columns, fields[offset : offset + 4]):
            column.metric(label, format_report_value(report.get(key), kind))
    with st.expander("Health details", expanded=False):
        st.json(report.get("health", {}))
    issues = report.get("execution_logging_issues") or []
    if issues:
        st.dataframe(pd.DataFrame({"execution logging issue": issues}), use_container_width=True, hide_index=True)


def list_weekly_reports(report_dir: Path | str) -> list[Path]:
    path = resolve_path(report_dir)
    if not path.exists():
        return []
    return sorted(path.glob("*weekly*.json"), key=lambda item: item.stat().st_mtime, reverse=True)


def load_weekly_report(path: Path | str) -> tuple[dict[str, Any], str | None]:
    report_path = resolve_path(path)
    try:
        return json.loads(report_path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"Could not load weekly report: {report_path} ({exc})"


def render_recent_events(events: pd.DataFrame) -> None:
    st.subheader("Recent Events")
    if events.empty:
        st.info("No shadow events found.")
        return
    display = events.sort_values("event_time", ascending=False).head(50)
    display = display[["event_time", "event_type", "severity", "message"]].rename(columns={"event_time": "event timestamp"})
    st.dataframe(display, use_container_width=True, hide_index=True)


def format_time(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return pd.Timestamp(value).isoformat()


def format_currency(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"${float(value):,.2f}"


def format_percent(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):.2%}"


def format_r(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):.2f}R"


def format_report_value(value: Any, kind: str) -> str:
    if value is None:
        return "n/a"
    if kind == "currency":
        return format_currency(value)
    if kind == "integer":
        return f"{int(value):,}"
    if kind == "percent":
        return f"{float(value):.1%}"
    if kind == "percent_abs":
        return f"{abs(float(value)):.2%}"
    if kind == "number":
        return f"{float(value):.2f}"
    if kind == "gap_count":
        if isinstance(value, dict):
            return f"{int(value.get('count', 0)):,}"
        return str(value)
    if kind == "health":
        if isinstance(value, dict):
            return str(value.get("status", "n/a"))
        return str(value)
    return str(value)


if __name__ == "__main__":
    main()
