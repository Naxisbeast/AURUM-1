"""Streamlit monitoring dashboard for AURUM-1 Phase 8."""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from aurum1.data.ingestion import load_settings
from aurum1.execution import PaperBroker
from monitor.metrics import (
    compute_drawdown_curve,
    compute_rolling_profit_factor,
    compute_rolling_sharpe,
    compute_rolling_win_rate,
    get_system_status,
    load_equity_curve,
)


def main() -> None:
    st.set_page_config(page_title="AURUM-1 Monitor", layout="wide")
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    db_path = str(ROOT / str(settings.get("data", {}).get("db_path", "aurum1/data/aurum1.sqlite3")))
    monitor_settings = settings.get("monitor", {})
    window_days = int(monitor_settings.get("rolling_window_days", 30))
    max_rows = int(monitor_settings.get("equity_chart_max_rows", 10000))

    paper_broker = PaperBroker(settings) if bool(settings.get("broker", {}).get("paper_trade", True)) else None
    equity_curve = load_equity_curve(db_path).tail(max_rows)
    trades = load_trade_log(db_path)
    events = load_event_log(db_path)
    status = get_system_status(db_path, paper_broker, settings)

    st.title("AURUM-1 Live Monitor")
    render_status_bar(status)
    render_equity_curve(equity_curve, settings)
    render_rolling_metrics(equity_curve, trades, window_days)
    render_open_positions(paper_broker)
    render_signal_monitor(trades, events, status)
    render_trade_log(trades)
    render_refresh_timer(int(monitor_settings.get("refresh_interval_sec", 60)))


def render_status_bar(status: dict[str, Any]) -> None:
    mode = str(status.get("system_mode", "STOPPED"))
    color = "#22c55e" if mode == "LIVE" else "#facc15" if mode == "PAPER" else "#ef4444"
    last_candle = _format_timestamp(status.get("last_candle_processed"))
    daily_pnl = float(status.get("daily_pnl", 0.0))
    pnl_color = "#16a34a" if daily_pnl >= 0.0 else "#dc2626"
    blackout = "YES" if status.get("blackout_active") else "NO"
    daily_kill = "TRIGGERED" if status.get("daily_kill_triggered") else "OK"
    dd_kill = "TRIGGERED" if status.get("total_drawdown_kill_triggered") else "OK"
    st.markdown(
        f"""
        <div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap;
                    padding:12px 14px;border:1px solid #e5e7eb;border-radius:8px;
                    background:#f8fafc;margin-bottom:12px;">
          <strong style="color:{color};">● {mode}</strong>
          <span>Last candle processed: <strong>{last_candle}</strong></span>
          <span>Open positions: <strong>{status.get("open_positions", 0)}</strong></span>
          <span>Current equity: <strong>${float(status.get("equity", 0.0)):,.2f}</strong></span>
          <span>Today P&amp;L: <strong style="color:{pnl_color};">${daily_pnl:,.2f} ({float(status.get("daily_pnl_pct", 0.0)):.2%})</strong></span>
          <span>Active mode: <strong>{status.get("active_mode", "RULE_REGIME")}</strong></span>
          <span>Blackout: <strong style="color:{'#dc2626' if blackout == 'YES' else '#111827'};">{blackout}</strong></span>
          <span>Daily kill switch: <strong style="color:{'#dc2626' if daily_kill == 'TRIGGERED' else '#111827'};">{daily_kill}</strong></span>
          <span>Total drawdown kill: <strong style="color:{'#dc2626' if dd_kill == 'TRIGGERED' else '#111827'};">{dd_kill}</strong></span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_equity_curve(equity_curve: pd.DataFrame, settings: dict[str, Any]) -> None:
    st.subheader("Equity Curve")
    if equity_curve.empty:
        st.info("No equity history yet.")
        return
    drawdown = compute_drawdown_curve(equity_curve)
    initial_equity = float(settings.get("broker", {}).get("paper_initial_equity", equity_curve["equity"].iloc[0]))
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.08,
        subplot_titles=("Equity Curve - AURUM-1", "Drawdown %"),
    )
    fig.add_trace(go.Scatter(x=equity_curve["timestamp"], y=equity_curve["equity"], mode="lines", name="Equity", line=dict(color="#2563eb")), row=1, col=1)
    fig.add_trace(go.Scatter(x=equity_curve["timestamp"], y=[initial_equity] * len(equity_curve), mode="lines", name="Initial Equity", line=dict(color="#16a34a", dash="dash")), row=1, col=1)
    fig.add_trace(go.Scatter(x=drawdown.index, y=drawdown * 100.0, fill="tozeroy", mode="lines", name="Drawdown %", line=dict(color="#dc2626")), row=2, col=1)
    fig.update_yaxes(title_text="Equity ($)", row=1, col=1)
    fig.update_yaxes(title_text="Drawdown %", row=2, col=1)
    fig.update_layout(height=560, margin=dict(l=20, r=20, t=50, b=30), legend=dict(orientation="h"))
    st.plotly_chart(fig, use_container_width=True)


def render_rolling_metrics(equity_curve: pd.DataFrame, trades: pd.DataFrame, window_days: int) -> None:
    st.subheader("Rolling Performance Metrics")
    sharpe = compute_rolling_sharpe(equity_curve, window_days)
    profit_factor = compute_rolling_profit_factor(trades, window_days)
    win_rate = compute_rolling_win_rate(trades, window_days)
    drawdown = compute_drawdown_curve(equity_curve)
    rolling_max_dd = drawdown.rolling(f"{window_days}D").min().abs() if not drawdown.empty else pd.Series(dtype="float64")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Sharpe Ratio", _last_value(sharpe, "{:.2f}"), _period_delta(sharpe, window_days, "{:+.2f}"))
    col2.metric("Profit Factor", _last_value(profit_factor, "{:.2f}"), _period_delta(profit_factor, window_days, "{:+.2f}"))
    col3.metric("Win Rate", _last_value(win_rate, "{:.1%}"), _period_delta(win_rate, window_days, "{:+.1%}"))
    col4.metric("Max Drawdown", _last_value(rolling_max_dd, "{:.2%}"), _period_delta(rolling_max_dd, window_days, "{:+.2%}"), delta_color="inverse")

    if sharpe.empty:
        st.info("No rolling Sharpe history yet.")
        return
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=sharpe.index, y=sharpe, mode="lines", name="30-day Sharpe", line=dict(color="#2563eb")))
    fig.add_hline(y=0.50, line_dash="dash", line_color="#64748b", annotation_text="Promotion gate 0.50")
    fig.update_layout(height=320, margin=dict(l=20, r=20, t=30, b=30), yaxis_title="Sharpe")
    st.plotly_chart(fig, use_container_width=True)


def render_open_positions(paper_broker: PaperBroker | None) -> None:
    st.subheader("Open Positions")
    positions = [] if paper_broker is None else paper_broker.get_open_positions()
    if not positions:
        st.info("No open positions")
        return
    now = datetime.now(UTC)
    frame = pd.DataFrame(
        [
            {
                "Position ID": item.position_id,
                "Direction": item.direction,
                "Entry Price": item.open_price,
                "Current Price": item.current_price,
                "Unrealised P&L": item.unrealised_pnl,
                "Stop Loss": item.stop_loss,
                "Take Profit": item.take_profit,
                "Open Time": item.open_time.isoformat(),
                "Duration": str(now - item.open_time),
                "ATR at Entry": None,
                "Regime": None,
            }
            for item in positions
        ]
    )
    st.dataframe(frame.style.apply(_position_row_style, axis=1), use_container_width=True, hide_index=True)


def render_signal_monitor(trades: pd.DataFrame, events: pd.DataFrame, status: dict[str, Any]) -> None:
    st.subheader("Signal Monitor")
    signal = latest_signal_snapshot(trades)
    event = next_event(events)
    left, right = st.columns([0.52, 0.48])
    with left:
        st.write(f"Current MachineState: **{signal.get('machine_state', 'SCANNING')}**")
        st.write(f"Current regime: **{signal.get('regime', 'RANGING')}**")
        st.write(f"Regime confidence: **{float(signal.get('regime_confidence', 0.0)):.0%}**")
        st.write(f"Last ensemble score: **{float(signal.get('raw_score', 0.0)):.2f}**")
        st.write(f"Last signal direction: **{signal.get('direction', 'FLAT')}**")
        st.write(f"Last signal timestamp: **{_format_timestamp(signal.get('timestamp'))}**")
        st.write(f"Sentiment quality: **{signal.get('sentiment_quality', 'empty')}**")
        st.write(f"Sentiment scalar: **{float(signal.get('sentiment_scalar', 0.0)):.2f}**")
        st.write(f"Blackout status: **{'active' if status.get('blackout_active') else 'clear'}**")
        st.write(f"Next high-impact event: **{event}**")
    with right:
        score = float(signal.get("raw_score", 0.0))
        fig = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=score,
                domain={"x": [0, 1], "y": [0, 1]},
                gauge={
                    "axis": {"range": [-1.0, 1.0]},
                    "bar": {"color": "#111827"},
                    "steps": [
                        {"range": [-1.0, -0.60], "color": "#fecaca"},
                        {"range": [-0.60, 0.60], "color": "#fef3c7"},
                        {"range": [0.60, 1.0], "color": "#bbf7d0"},
                    ],
                },
            )
        )
        fig.update_layout(height=300, margin=dict(l=20, r=20, t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)


def render_trade_log(trades: pd.DataFrame) -> None:
    st.subheader("Trade Log")
    if trades.empty:
        st.info("No trade records yet.")
        return
    filters = st.columns(4)
    min_date = pd.to_datetime(trades["timestamp"], utc=True).min().date()
    max_date = pd.to_datetime(trades["timestamp"], utc=True).max().date()
    date_range = filters[0].date_input("Date range", value=(min_date, max_date))
    direction = filters[1].selectbox("Direction", ["ALL", "BUY", "SELL"])
    status = filters[2].selectbox("Status", ["ALL", "filled", "rejected", "timeout", "closed"])
    regime = filters[3].selectbox("Regime", ["ALL", "TRENDING_UP", "TRENDING_DOWN", "RANGING"])

    shown = trades.copy()
    shown["timestamp"] = pd.to_datetime(shown["timestamp"], utc=True)
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start, end = pd.Timestamp(date_range[0], tz=UTC), pd.Timestamp(date_range[1], tz=UTC) + pd.Timedelta(days=1)
        shown = shown[(shown["timestamp"] >= start) & (shown["timestamp"] < end)]
    if direction != "ALL":
        shown = shown[shown["direction"] == direction]
    if status != "ALL":
        shown = shown[shown["status"] == status]
    if regime != "ALL":
        shown = shown[shown["regime"] == regime]

    columns = [
        "timestamp",
        "direction",
        "entry",
        "exit_current",
        "pnl",
        "lot_size",
        "status",
        "regime",
        "signal_score",
        "rejection_reason",
    ]
    st.dataframe(shown[columns], use_container_width=True, hide_index=True, height=420)
    pnl = pd.to_numeric(shown["pnl"], errors="coerce").fillna(0.0)
    wins = int((pnl > 0.0).sum())
    total = len(shown)
    win_rate = wins / total if total else 0.0
    avg_rr = float(pd.to_numeric(shown["rr"], errors="coerce").dropna().mean()) if "rr" in shown else 0.0
    st.caption(f"Total trades shown: {total} | Win rate: {win_rate:.1%} | Total P&L: ${pnl.sum():,.2f} | Avg R:R: {avg_rr:.2f}")


def render_refresh_timer(refresh_interval: int) -> None:
    placeholder = st.empty()
    for remaining in range(max(1, refresh_interval), 0, -1):
        placeholder.caption(f"Auto-refresh in {remaining}s")
        time.sleep(1)
    st.rerun()


def load_trade_log(db_path: str) -> pd.DataFrame:
    path = Path(db_path)
    if not path.exists():
        return empty_trade_frame()
    with closing(sqlite3.connect(path)) as conn:
        try:
            raw = pd.read_sql_query(
                "SELECT timestamp, direction, price, size, sl, tp, order_id, status, payload_json FROM trades_log ORDER BY timestamp",
                conn,
            )
        except (sqlite3.Error, pd.errors.DatabaseError):
            return empty_trade_frame()
    if raw.empty:
        return empty_trade_frame()
    rows = []
    for item in raw.to_dict(orient="records"):
        payload = _json_payload(item.get("payload_json"))
        risk_order = payload.get("risk_order", {}) if isinstance(payload.get("risk_order"), dict) else {}
        instruction = risk_order.get("instruction", {}) if isinstance(risk_order.get("instruction"), dict) else {}
        raw_response = payload.get("raw_response", {}) if isinstance(payload.get("raw_response"), dict) else {}
        pnl = raw_response.get("pnl", payload.get("pnl"))
        entry = instruction.get("entry_price", item.get("price"))
        risk_amount = risk_order.get("risk_amount")
        rows.append(
            {
                "timestamp": item.get("timestamp"),
                "direction": item.get("direction"),
                "entry": entry,
                "exit_current": item.get("price"),
                "pnl": pnl if pnl is not None else 0.0,
                "lot_size": item.get("size"),
                "status": item.get("status"),
                "regime": instruction.get("regime"),
                "signal_score": instruction.get("signal_score"),
                "rejection_reason": payload.get("rejection_reason") or raw_response.get("reason"),
                "rr": (float(pnl) / float(risk_amount)) if pnl is not None and risk_amount not in (None, 0, "0") else None,
                "payload": payload,
            }
        )
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    return frame.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)


def load_event_log(db_path: str) -> pd.DataFrame:
    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()
    with closing(sqlite3.connect(path)) as conn:
        try:
            frame = pd.read_sql_query("SELECT * FROM economic_events ORDER BY event_time", conn)
        except (sqlite3.Error, pd.errors.DatabaseError):
            return pd.DataFrame()
    if not frame.empty:
        frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True, errors="coerce")
    return frame


def latest_signal_snapshot(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {}
    row = trades.iloc[-1].to_dict()
    payload = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
    risk_order = payload.get("risk_order", {}) if isinstance(payload.get("risk_order"), dict) else {}
    instruction = risk_order.get("instruction", {}) if isinstance(risk_order.get("instruction"), dict) else {}
    return {
        "machine_state": "SCANNING",
        "regime": instruction.get("regime", row.get("regime") or "RANGING"),
        "regime_confidence": instruction.get("confidence", 0.0),
        "raw_score": instruction.get("signal_score", row.get("signal_score") or 0.0),
        "direction": instruction.get("direction", row.get("direction") or "FLAT"),
        "timestamp": row.get("timestamp"),
        "sentiment_quality": "empty",
        "sentiment_scalar": 0.0,
    }


def next_event(events: pd.DataFrame) -> str:
    if events.empty or "event_time" not in events.columns:
        return "None scheduled"
    now = pd.Timestamp.now(tz=UTC)
    future = events[events["event_time"] >= now].sort_values("event_time")
    if future.empty:
        return "None scheduled"
    row = future.iloc[0]
    return f"{row.get('event_name', 'High impact event')} at {_format_timestamp(row.get('event_time'))}"


def empty_trade_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "timestamp",
            "direction",
            "entry",
            "exit_current",
            "pnl",
            "lot_size",
            "status",
            "regime",
            "signal_score",
            "rejection_reason",
            "rr",
            "payload",
        ]
    )


def _position_row_style(row: pd.Series) -> list[str]:
    pnl = float(row.get("Unrealised P&L", 0.0))
    color = "background-color: #dcfce7" if pnl > 0.0 else "background-color: #fee2e2" if pnl < 0.0 else ""
    return [color] * len(row)


def _last_value(series: pd.Series, fmt: str) -> str:
    if series.empty:
        return "n/a"
    return fmt.format(float(series.iloc[-1]))


def _period_delta(series: pd.Series, window_days: int, fmt: str) -> str:
    if len(series) < 2:
        return "n/a"
    current = float(series.iloc[-1])
    cutoff = series.index[-1] - pd.Timedelta(days=window_days)
    previous = series.loc[series.index <= cutoff]
    if previous.empty:
        previous_value = float(series.iloc[0])
    else:
        previous_value = float(previous.iloc[-1])
    return fmt.format(current - previous_value)


def _format_timestamp(value: Any) -> str:
    if value is None or value == "":
        return "n/a"
    timestamp = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(timestamp):
        return "n/a"
    return timestamp.strftime("%Y-%m-%d %H:%M UTC")


def _json_payload(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


if __name__ == "__main__":
    main()
