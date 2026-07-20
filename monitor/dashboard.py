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
from monitor.metrics import (
    compute_drawdown_curve,
    compute_rolling_profit_factor,
    compute_rolling_sharpe,
    compute_rolling_win_rate,
    get_system_status,
    load_equity_curve,
    load_system_health,
)
from monitor.evidence import EvidenceCollector


def main() -> None:
    st.set_page_config(page_title="AURUM-1 Monitor", layout="wide")
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    db_path = str(ROOT / str(settings.get("data", {}).get("db_path", "aurum1/data/aurum1.sqlite3")))
    monitor_settings = settings.get("monitor", {})
    window_days = int(monitor_settings.get("rolling_window_days", 30))
    max_rows = int(monitor_settings.get("equity_chart_max_rows", 10000))

    equity_curve = load_equity_curve(db_path).tail(max_rows)
    trades = load_trade_log(db_path)
    events = load_event_log(db_path)
    status = get_system_status(db_path, settings)

    st.title("AURUM-1 Live Monitor")
    render_status_bar(status)
    render_evidence_progress()
    render_equity_curve(equity_curve, settings)
    render_trade_chart(equity_curve, trades, settings)
    render_rolling_metrics(equity_curve, trades, window_days)
    render_open_positions(db_path)
    render_signal_monitor(trades, events, status)
    render_system_health(db_path)
    render_trade_log(trades)
    render_refresh_timer(int(monitor_settings.get("refresh_interval_sec", 60)))


def render_evidence_progress() -> None:
    """Render evidence collection progress bar."""
    try:
        collector = EvidenceCollector(ROOT)
        report = collector.generate_report()
    except Exception:
        st.info("Evidence collection data not available yet.")
        return

    pct_to_50 = min(100.0, (report.total_trades / 50) * 100)
    pct_to_100 = min(100.0, (report.total_trades / 100) * 100)

    st.subheader("Evidence Collection Progress")
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Trades", str(report.total_trades))
    col2.metric("Since 0.35%", str(report.trades_at_new_risk))
    col3.metric("Risk Review Gate", f"{report.trades_remaining_to_50} left", f"{pct_to_50:.0f}%")
    col4.metric("Strategy Gate", f"{report.trades_remaining_to_100} left", f"{pct_to_100:.0f}%")
    col5.metric("Trade Rate", f"{report.trade_rate_per_day:.1f}/d")

    st.progress(pct_to_50 / 100.0, text=f"Progress to 50-trade risk review: {report.total_trades}/50")

    if report.risk_review_due:
        st.success("50 trades reached — risk review due! Consider 0.50% risk review.")
    if report.strategy_review_due:
        st.success("100 trades reached — strategy review due!")


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
                    padding:12px 14px;border:1px solid #334155;border-radius:8px;
                    background:var(--secondary-background-color, #1e293b);margin-bottom:12px;">
          <strong style="color:{color};">● {mode}</strong>
          <span>Last candle processed: <strong>{last_candle}</strong></span>
          <span>Open positions: <strong>{status.get("open_positions", 0)}</strong></span>
          <span>Current equity: <strong>${float(status.get("equity", 0.0)):,.2f}</strong></span>
          <span>Today P&amp;L: <strong style="color:{pnl_color};">${daily_pnl:,.2f} ({float(status.get("daily_pnl_pct", 0.0)):.2%})</strong></span>
          <span>Active mode: <strong>{status.get("active_mode", "RULE_REGIME")}</strong></span>
          <span>Blackout: <strong style="color:{'#f87171' if blackout == 'YES' else 'inherit'};">{blackout}</strong></span>
          <span>Daily kill switch: <strong style="color:{'#f87171' if daily_kill == 'TRIGGERED' else 'inherit'};">{daily_kill}</strong></span>
          <span>Total drawdown kill: <strong style="color:{'#f87171' if dd_kill == 'TRIGGERED' else 'inherit'};">{dd_kill}</strong></span>
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


def render_trade_chart(equity_curve: pd.DataFrame, trades: pd.DataFrame, settings: dict[str, Any]) -> None:
    """Show trades on a price line chart with entry→exit arrows."""
    st.subheader("Trade Chart — All Entries & Exits")
    if trades.empty and equity_curve.empty:
        st.info("No equity or trade data yet.")
        return

    fig = go.Figure()

    # Equity curve as thin background reference
    if not equity_curve.empty:
        fig.add_trace(go.Scatter(
            x=equity_curve["timestamp"], y=equity_curve["equity"],
            mode="lines", name="Equity ($)",
            line=dict(color="rgba(79, 138, 245, 0.4)", width=1),
        ))

    # Plot each trade: entry marker + exit marker + connecting line
    if not trades.empty:
        for _, t in trades.iterrows():
            ts = t.get("timestamp")
            pnl = float(t.get("pnl", 0))
            direction = str(t.get("direction", ""))
            entry = float(t.get("entry", 0))
            exit_p = float(t.get("exit_current", 0))
            reason = str(t.get("rejection_reason", ""))
            rr = float(t.get("rr", 0)) if t.get("rr") else 0

            if entry <= 0 or exit_p <= 0:
                continue

            # Color and shape
            is_win = pnl > 0
            if direction == "BUY":
                entry_symbol = "triangle-up"
                exit_symbol = "diamond"
            else:
                entry_symbol = "triangle-down"
                exit_symbol = "diamond"
            color = "#22c55e" if is_win else "#ef4444"
            label = "WIN" if is_win else "LOSS"

            # Entry marker at entry price, exit marker at exit price, connected by line
            # Align trade with equity curve by picking the equity value at the same time
            eq_val_at_entry = None
            if not equity_curve.empty:
                eq_ts = pd.to_datetime(equity_curve["timestamp"], utc=True)
                trade_ts = pd.Timestamp(ts).tz_convert("UTC") if pd.notna(ts) else None
                if trade_ts:
                    closest = (eq_ts - trade_ts).abs().idxmin()
                    eq_val_at_entry = float(equity_curve.iloc[closest]["equity"])

            if eq_val_at_entry:
                y_val = eq_val_at_entry
            else:
                y_val = 10000 + (pnl * 5)  # fallback: map PnL to equity-like scale

            hover = (
                f"{'🟢' if is_win else '🔴'} {direction} {label}<br>"
                f"Entry: ${entry:.2f} → Exit: ${exit_p:.2f}<br>"
                f"R: {rr:+.2f} | PnL: ${pnl:+.2f}<br>"
                f"{'TP' if 'profit' in reason else 'SL' if 'stop' in reason else reason}"
            )

            # Connecting line from entry time to exit time
            fig.add_trace(go.Scatter(
                x=[ts, ts],
                y=[y_val - 10, y_val + 10],
                mode="lines",
                line=dict(color=color, width=3),
                showlegend=False,
                hoverinfo="skip",
            ))

            # Marker on the equity curve
            fig.add_trace(go.Scatter(
                x=[ts], y=[y_val],
                mode="markers",
                marker=dict(symbol=entry_symbol, size=14, color=color, line=dict(color="white", width=1.5)),
                name=hover,
                hovertext=hover,
                hoverinfo="text",
                showlegend=False,
            ))

    fig.update_layout(
        height=400, margin=dict(l=20, r=20, t=30, b=30),
        yaxis_title="Equity ($)",
        hovermode="closest",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(gridcolor="#334155")
    fig.update_yaxes(gridcolor="#334155")
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


def render_r_distribution(trades: pd.DataFrame) -> None:
    """R-multiple distribution panel: histogram, cumulative R, streaks."""
    from monitor.metrics import compute_r_distribution

    st.subheader("R-Multiple Distribution")
    stats = compute_r_distribution(trades)

    if stats["n_trades"] == 0:
        st.info("No trade data yet.")
        return

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Cumulative R", f"{stats['cumulative_r']:+.2f}")
    col2.metric("Avg R", f"{stats['r_mean']:+.4f}" if stats['r_mean'] != 0 else "0")
    col3.metric("R Sharpe", f"{stats['r_sharpe']:.3f}")
    col4.metric("Win Rate", f"{stats['win_rate']:.1%}")
    col5.metric("N Trades", str(stats['n_trades']))

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Avg Win R", f"+{stats['avg_win_r']:.3f}")
    col2.metric("Avg Loss R", f"{stats['avg_loss_r']:.3f}")
    col3.metric("Consecutive Wins", str(stats.get("max_consecutive_wins", 0)))
    col4.metric("Consecutive Losses", str(stats.get("max_consecutive_losses", 0)))
    col5.metric("R Median", f"{stats['r_median']:.4f}")

    # R-multiple histogram
    r_values = pd.to_numeric(
        trades.get("r_multiple", trades.get("r", pd.Series(dtype=float))),
        errors="coerce",
    ).dropna()

    if len(r_values) > 0:
        fig = go.Figure()
        fig.add_trace(go.Histogram(x=r_values, nbinsx=30, marker_color="#3b82f6"))
        fig.add_vline(x=0, line_dash="dash", line_color="#ef4444")
        fig.add_vline(x=2.0, line_dash="dash", line_color="#22c55e", annotation_text="TP")
        fig.add_vline(x=-1.0, line_dash="dash", line_color="#ef4444", annotation_text="SL")
        fig.update_layout(
            height=220, margin=dict(l=20, r=20, t=10, b=30),
            xaxis_title="R-multiple", yaxis_title="Trades",
        )
        st.plotly_chart(fig, use_container_width=True)

    # Deciles table
    if "r_deciles" in stats and stats["r_deciles"]:
        st.caption("R-multiple Deciles")
        decile_data = {f"P{k}": v for k, v in stats["r_deciles"].items()}
        decile_df = pd.DataFrame([decile_data])
        st.dataframe(decile_df, use_container_width=True, hide_index=True)


def render_open_positions(db_path: str) -> None:
    """Read open positions from paper_trading DB (no broker creation)."""
    st.subheader("Open Positions")
    paper_db = Path(db_path).parent / "paper_trading.sqlite3"
    if not paper_db.exists():
        st.info("No open positions")
        return
    try:
        with closing(sqlite3.connect(paper_db)) as conn:
            rows = conn.execute(
                "SELECT direction, entry_price, current_price, stop_loss, take_profit, "
                "units, lot_size, entry_slippage, open_time FROM open_positions ORDER BY id"
            ).fetchall()
        if not rows:
            st.info("No open positions")
            return
        frame = pd.DataFrame(
            [
                {
                    "Direction": "🟢 BUY" if row[0] == "BUY" else "🔴 SELL",
                    "Entry": round(row[1], 2),
                    "Current": round(row[2], 2),
                    "Stop Loss": round(row[3], 2),
                    "Take Profit": round(row[4], 2),
                    "Units": row[5],
                    "Open Time": row[8][:19] if row[8] else "-",
                }
                for row in rows
            ]
        )
        st.dataframe(frame, use_container_width=True, hide_index=True)
    except Exception:
        st.info("No open positions")


def render_signal_monitor(trades: pd.DataFrame, events: pd.DataFrame, status: dict[str, Any]) -> None:
    st.subheader("Signal Monitor")
    signal = latest_signal_snapshot(trades)
    event = next_event(events)
    st.write(f"Current MachineState: **{signal.get('machine_state', 'SCANNING')}**")
    st.write(f"Last signal direction: **{signal.get('direction', 'FLAT')}**")
    st.write(f"Last signal timestamp: **{_format_timestamp(signal.get('timestamp'))}**")
    st.write(f"Last R-multiple: **{signal.get('r_multiple', '—')}**")
    st.write(f"Last PnL: **${float(signal.get('pnl', 0.0)):+,.2f}**")
    st.write(f"Blackout status: **{'active' if status.get('blackout_active') else 'clear'}**")
    st.write(f"Next high-impact event: **{event}**")


def render_system_health(db_path: str) -> None:
    """Render system health panel: latency, slippage, spread, missed signals."""
    health = load_system_health(db_path)

    st.subheader("System Health")

    if health.get("source") == "none":
        st.info("No health data available. D4 paper trader health file not found.")
        return

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Uptime", f'{health.get("uptime_hours", 0):.1f}h')
    col2.metric("Total Signals", str(health.get("total_signals", 0)))
    col3.metric("Missed Signals", str(health.get("missed_signals", 0)))
    col4.metric("Trades", str(health.get("trade_count", 0)))

    has_health_file = health.get("source") == "d4_health_file"
    if has_health_file:
        col1, col2, col3, col4 = st.columns(4)

        lat = health.get("avg_latency_seconds")
        lat_str = f"{lat:.3f}s" if lat is not None else "—"
        col1.metric("Avg Latency", lat_str)

        sp = health.get("avg_spread_pips")
        sp_str = f"{sp:.1f}p" if sp is not None else "—"
        col2.metric("Avg Spread", sp_str)

        entry_slip = health.get("avg_entry_slippage")
        slip_str = f"{entry_slip:+.4f}" if entry_slip is not None else "—"
        col3.metric("Entry Slippage", slip_str)

        exit_slip = health.get("avg_exit_slippage")
        exit_str = f"{exit_slip:+.4f}" if exit_slip is not None else "—"
        col4.metric("Exit Slippage", exit_str)

        # Candle age warning
        candle_age = health.get("latest_candle_age_minutes")
        if candle_age is not None and candle_age > 120:
            st.warning(f"Stale data: latest candle is {candle_age:.0f} minutes old")

        # Missed signal reasons
        reasons = health.get("missed_signal_reasons", [])
        if reasons:
            with st.expander("Missed Signal Reasons"):
                for r in reasons:
                    st.write(f"- {r.get('reason', '?')}: {r.get('count', 0)}x")


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

    # Format R-multiple with color
    def _color_r(val):
        try:
            v = float(val)
            color = "#22c55e" if v > 0 else "#ef4444" if v < 0 else "inherit"
            return f"color: {color}; font-weight: bold"
        except (ValueError, TypeError):
            return ""

    shown["R"] = pd.to_numeric(shown["rr"], errors="coerce")
    shown["Result"] = shown.apply(
        lambda r: "✅ WIN" if float(r.get("pnl", 0)) > 0 else "❌ LOSS", axis=1
    )
    shown["Entry $"] = shown["entry"].apply(lambda x: f"${x:.2f}" if x else "-")
    shown["Exit $"] = shown["exit_current"].apply(lambda x: f"${x:.2f}" if x else "-")
    shown["PnL $"] = shown["pnl"].apply(lambda x: f"${x:+.2f}")
    shown["Time"] = shown["timestamp"].dt.strftime("%m/%d %H:%M")

    display_cols = ["Time", "direction", "Entry $", "Exit $", "R", "PnL $", "Result", "rejection_reason"]
    display_map = {
        "Time": "Time", "direction": "Dir", "Entry $": "Entry",
        "Exit $": "Exit", "R": "R", "PnL $": "PnL",
        "Result": "", "rejection_reason": "Reason"
    }
    try:
        styled = shown[display_cols].rename(columns=display_map).style.map(_color_r, subset=["R"])
    except AttributeError:
        styled = shown[display_cols].rename(columns=display_map).style.applymap(_color_r, subset=["R"])
    st.dataframe(styled, use_container_width=True, hide_index=True, height=420)
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
    paper_db = path.parent / "paper_trading.sqlite3"

    # Try paper_trading first (has actual trade data)
    if paper_db.exists():
        with closing(sqlite3.connect(paper_db)) as conn:
            try:
                raw = pd.read_sql_query(
                    "SELECT exit_time as timestamp, direction, entry_price, exit_price, "
                    "r_multiple, net_pnl, exit_reason FROM trades ORDER BY exit_time",
                    conn,
                )
            except (sqlite3.Error, pd.errors.DatabaseError):
                raw = pd.DataFrame()
        if not raw.empty:
            raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True, errors="coerce")
            raw["pnl"] = pd.to_numeric(raw["net_pnl"], errors="coerce").fillna(0.0)
            raw["entry"] = raw["entry_price"]
            raw["exit_current"] = raw["exit_price"]
            raw["rr"] = pd.to_numeric(raw["r_multiple"], errors="coerce")
            raw["rejection_reason"] = raw["exit_reason"]
            raw["lot_size"] = 0.0
            raw["status"] = "closed"
            raw["regime"] = ""
            raw["signal_score"] = 0.0
            raw["payload"] = ""
            cols = ["timestamp", "direction", "entry", "exit_current", "pnl", "lot_size",
                    "status", "regime", "signal_score", "rejection_reason", "rr", "payload"]
            return raw[cols].dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    # Fallback: legacy aurum1 trades_log
    if path.exists():
        with closing(sqlite3.connect(path)) as conn:
            try:
                raw = pd.read_sql_query(
                    "SELECT timestamp, direction, price, size, sl, tp, order_id, status, payload_json FROM trades_log ORDER BY timestamp",
                    conn,
                )
            except (sqlite3.Error, pd.errors.DatabaseError):
                raw = pd.DataFrame()
        if not raw.empty:
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

    return empty_trade_frame()


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
    return {
        "machine_state": "SCANNING",
        "direction": row.get("direction", "FLAT"),
        "timestamp": row.get("timestamp"),
        "r_multiple": f"{float(row.get('rr', 0)):+.2f}" if row.get("rr") and float(row.get("rr", 0)) != 0 else "—",
        "pnl": float(row.get("pnl", 0)),
        "raw_score": 0.0,
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
