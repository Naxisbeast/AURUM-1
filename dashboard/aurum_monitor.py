"""
AURUM-1 Operations Dashboard — D4 Paper Trader Monitor

READ-ONLY. Reads exclusively from paper_trading.sqlite3 and journalctl
timestamps. Never imports from aurum1/ package or touches live execution.

Audit note: Fields that would show 0/None/empty are explicitly handled:
  - missed_signals table: hidden entirely (no data exists)
  - open_positions: shows "No open positions" when empty
  - All zero-initialized metrics: shown as "— not yet tracked —"
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st

st.set_page_config(page_title="AURUM-1 Operations Monitor", layout="wide")

PAPER_DB = ROOT / "aurum1" / "data" / "paper_trading.sqlite3"
D7_DB = ROOT / "reports" / "forward_shadow" / "donchian_d7.sqlite3"

# ── Helpers ──

def _load_paper_db() -> sqlite3.Connection:
    """Open a read-only connection to the paper trading DB."""
    conn = sqlite3.connect(f"file:{PAPER_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn

def _load_d7_db() -> sqlite3.Connection | None:
    """Open a read-only connection to the D7 DB if it exists."""
    if not D7_DB.exists():
        return None
    conn = sqlite3.connect(f"file:{D7_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn

def _fetchall(conn, query: str, params: tuple = ()) -> list[sqlite3.Row]:
    try:
        return conn.execute(query, params).fetchall()
    except sqlite3.Error:
        return []

def _fetchone(conn, query: str, params: tuple = ()) -> sqlite3.Row | None:
    try:
        return conn.execute(query, params).fetchone()
    except sqlite3.Error:
        return None

# ── Data loading ──

@st.cache_data(ttl=30)
def load_overview() -> dict[str, Any]:
    """Load all dashboard data from paper_trading DB."""
    result = {
        "status": "UNKNOWN",
        "last_candle": None,
        "equity": None, "peak_equity": None, "daily_pnl": None,
        "open_positions": 0, "position_detail": None,
        "trade_count": 0, "win_count": 0, "loss_count": 0,
        "profit_factor": None, "win_rate": None, "current_dd": None,
        "avg_r": None, "total_pnl": None,
        "days_live": 0,
        "trades": [],
        "equity_curve": pd.DataFrame(),
        "daily_kill": False, "dd_kill": False,
        "last_trade_time": None,
    }

    if not PAPER_DB.exists():
        result["status"] = "NO_DB"
        return result

    conn = _load_paper_db()

    # Last processed timestamp from settings
    last_ts = _fetchone(conn, "SELECT value FROM settings WHERE key = 'last_processed_ts'")
    result["last_candle"] = last_ts["value"] if last_ts else None

    # Latest account snapshot
    snap = _fetchone(conn, """
        SELECT equity, peak_equity, daily_pnl, position_count, trade_count
        FROM account_snapshots ORDER BY id DESC LIMIT 1
    """)
    if snap:
        result["equity"] = snap["equity"]
        result["peak_equity"] = snap["peak_equity"]
        result["daily_pnl"] = snap["daily_pnl"]
        result["open_positions"] = snap["position_count"]
    else:
        result["status"] = "NO_SNAPSHOTS"
        return result

    # Kill switch state
    risk_settings = {"daily_loss_kill_pct": 0.03, "total_drawdown_kill_pct": 0.08}
    if result["daily_pnl"] is not None and result["equity"] and result["equity"] > 0:
        result["daily_kill"] = result["daily_pnl"] < -(result["equity"] * risk_settings["daily_loss_kill_pct"])
    if result["peak_equity"] and result["equity"]:
        result["current_dd"] = (result["peak_equity"] - result["equity"]) / result["peak_equity"]
        result["dd_kill"] = result["equity"] < result["peak_equity"] * (1 - risk_settings["total_drawdown_kill_pct"])

    # Open position detail
    open_pos = _fetchone(conn, "SELECT * FROM open_positions LIMIT 1")
    if open_pos:
        result["position_detail"] = dict(open_pos)

    # All trades
    trades_rows = _fetchall(conn, "SELECT * FROM trades ORDER BY id")
    if trades_rows:
        trades_list = []
        for r in trades_rows:
            d = dict(r)
            trades_list.append(d)
        result["trades"] = trades_list
        result["trade_count"] = len(trades_list)
        result["win_count"] = sum(1 for t in trades_list if (t.get("net_pnl") or 0) > 0)
        result["loss_count"] = sum(1 for t in trades_list if (t.get("net_pnl") or 0) <= 0)

        # Profit factor
        gross_win = sum(t["net_pnl"] for t in trades_list if (t.get("net_pnl") or 0) > 0)
        gross_loss = abs(sum(t["net_pnl"] for t in trades_list if (t.get("net_pnl") or 0) < 0))
        if gross_loss > 0:
            result["profit_factor"] = gross_win / gross_loss

        # Win rate
        if result["trade_count"] > 0:
            result["win_rate"] = result["win_count"] / result["trade_count"]

        # Total net PnL
        result["total_pnl"] = sum((t.get("net_pnl") or 0) for t in trades_list)

        # Average R
        r_vals = [t.get("r_multiple", 0) or 0 for t in trades_list if t.get("r_multiple") is not None]
        if r_vals:
            result["avg_r"] = float(np.mean(r_vals))

        # Last trade time
        if trades_list:
            result["last_trade_time"] = trades_list[-1].get("exit_time") or trades_list[-1].get("timestamp")

    # Days live from first account_snapshot
    first_snap = _fetchone(conn, "SELECT timestamp FROM account_snapshots ORDER BY id ASC LIMIT 1")
    if first_snap:
        try:
            d4_start = pd.Timestamp(first_snap["timestamp"], tz=UTC)
            result["days_live"] = max(1, (datetime.now(UTC) - d4_start).days)
        except Exception:
            result["days_live"] = 0

    # Equity curve
    snap_rows = _fetchall(conn, "SELECT timestamp, equity, peak_equity, daily_pnl FROM account_snapshots ORDER BY id")
    if snap_rows:
        result["equity_curve"] = pd.DataFrame(
            [dict(r) for r in snap_rows],
            columns=["timestamp", "equity", "peak_equity", "daily_pnl"]
        )
        result["equity_curve"]["timestamp"] = pd.to_datetime(result["equity_curve"]["timestamp"], utc=True)
        result["equity_curve"]["equity"] = pd.to_numeric(result["equity_curve"]["equity"], errors="coerce")

    # Determine overall status
    if result["dd_kill"]:
        result["status"] = "HALTED_DD"
    elif result["daily_kill"]:
        result["status"] = "HALTED_DAILY"
    elif result["equity"] and result["trade_count"] > 0:
        result["status"] = "HEALTHY"
    elif result["equity"] and result["trade_count"] == 0:
        result["status"] = "STANDBY"  # Running but no trades yet
    else:
        result["status"] = "STANDBY"

    conn.close()
    return result

@st.cache_data(ttl=30)
def load_d7_aggregate() -> dict[str, Any] | None:
    """Load D7 aggregate KPIs from its SQLite DB."""
    conn = _load_d7_db()
    if conn is None:
        return None
    try:
        trades = _fetchall(conn, "SELECT * FROM d7_trades")
        equity_rows = _fetchall(conn, "SELECT * FROM d7_equity_curve ORDER BY rowid DESC LIMIT 1")
        if not trades:
            return {"trades": 0, "wins": 0, "losses": 0, "pf": None, "total_pnl": 0.0, "wr": None, "equity": None}
        trade_count = len(trades)
        win_count = sum(1 for t in trades if t["net_pnl"] and t["net_pnl"] > 0)
        loss_count = trade_count - win_count
        gross_win = sum(t["net_pnl"] for t in trades if t["net_pnl"] and t["net_pnl"] > 0)
        gross_loss = abs(sum(t["net_pnl"] for t in trades if t["net_pnl"] and t["net_pnl"] < 0))
        pf = gross_win / gross_loss if gross_loss > 0 else None
        total_pnl = sum((t["net_pnl"] or 0) for t in trades)
        equity = equity_rows[0]["equity"] if equity_rows else None
        # Days live: D7 launched 2026-07-16
        d7_launch = pd.Timestamp("2026-07-16", tz=UTC)
        days_live = max(0, (datetime.now(UTC) - d7_launch).days)
        is_early = days_live < 3  # Early if running less than 3 days

        result = {
            "trades": trade_count, "wins": win_count, "losses": loss_count,
            "pf": pf, "total_pnl": total_pnl,
            "wr": win_count / trade_count if trade_count > 0 else None,
            "equity": equity,
            "days_live": days_live, "is_early": is_early,
        }
        conn.close()
        return result
    except Exception:
        conn.close()
        return None

# ── Rendering ──

def render_health_bar(data: dict[str, Any]) -> None:
    """Top health bar — always visible without scrolling."""
    status = data["status"]

    status_config = {
        "HEALTHY":       {"color": "#22c55e", "label": "HEALTHY", "detail": "All systems nominal"},
        "STANDBY":       {"color": "#facc15", "label": "STANDBY", "detail": "Running, awaiting first trade"},
        "HALTED_DD":     {"color": "#ef4444", "label": "HALTED — Total Drawdown Limit",
                          "detail": f"Max drawdown limit (8%) reached. Equity at ${data['equity']:,.2f} vs peak ${data['peak_equity']:,.2f} ({data['current_dd']*100:.1f}%). Trading will resume when drawdown recovers above threshold."},
        "HALTED_DAILY":  {"color": "#ef4444", "label": "HALTED — Daily Loss Limit",
                          "detail": f"Daily loss limit (3%) exceeded. Today's PnL: ${data['daily_pnl']:,.2f}. Trading will resume next trading day."},
        "NO_DB":         {"color": "#6b7280", "label": "NO DATA",
                          "detail": "Paper trading database not found."},
        "NO_SNAPSHOTS":  {"color": "#6b7280", "label": "NO DATA",
                          "detail": "No account snapshots recorded yet."},
    }
    cfg = status_config.get(status, status_config["NO_DB"])

    # Last candle freshness
    freshness = ""
    if data["last_candle"]:
        try:
            last_ts = pd.Timestamp(data["last_candle"], tz=UTC)
            elapsed = (datetime.now(UTC) - last_ts).total_seconds()
            if elapsed < 120:
                freshness = "Just now"
            elif elapsed < 3600:
                freshness = f"{int(elapsed // 60)}m ago"
            else:
                freshness = f"{elapsed/3600:.1f}h ago"
        except Exception:
            freshness = str(data["last_candle"])

    # Kill switch state display
    kill_info = ""
    if data["daily_kill"] or data["dd_kill"]:
        triggers = []
        if data["daily_kill"]: triggers.append("daily loss limit")
        if data["dd_kill"]: triggers.append("drawdown limit")
        kill_info = f" | ⚠️ Kill switch: {', '.join(triggers)}"

    # Last trade time
    last_trade = ""
    if data["last_trade_time"]:
        try:
            lt = pd.Timestamp(data["last_trade_time"], tz=UTC)
            last_trade = f" | Last trade: {lt.strftime('%b %d %H:%M')}"
        except Exception:
            pass

    eq_str = f"${data['equity']:,.2f}" if data['equity'] is not None else "—"
    pnl_str = f"${data['daily_pnl']:+,.2f}" if data['daily_pnl'] is not None else "—"
    pnl_color = "#16a34a" if (data['daily_pnl'] or 0) >= 0 else "#dc2626"

    st.markdown(
        f"""
        <div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap;
                    padding:20px 24px;border:1px solid #334155;border-radius:12px;
                    background:var(--secondary-background-color, #1e293b);margin-bottom:20px;">
          <strong style="color:{cfg['color']};font-size:1.2rem;">● {cfg['label']}</strong>
          <div style="display:flex;flex-direction:column;gap:2px;">
            <span style="font-size:0.75rem;color:#64748b;">Last updated</span>
            <strong>{freshness}</strong>
          </div>
          <div style="display:flex;flex-direction:column;gap:2px;">
            <span style="font-size:0.75rem;color:#64748b;">Equity</span>
            <strong>{eq_str}</strong>
          </div>
          <div style="display:flex;flex-direction:column;gap:2px;">
            <span style="font-size:0.75rem;color:#64748b;">Today's P&amp;L</span>
            <strong style="color:{pnl_color};">{pnl_str}</strong>
          </div>
          <div style="display:flex;flex-direction:column;gap:2px;">
            <span style="font-size:0.75rem;color:#64748b;">Open positions</span>
            <strong>{data['open_positions']}</strong>
          </div>
          <div style="font-size:0.85rem;color:#94a3b8;align-self:flex-end;">
            {last_trade}{kill_info}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # If halted, show the specific reason with equal visual weight
    if status in ("HALTED_DD", "HALTED_DAILY"):
        st.markdown(
            f"""
            <div style="padding:12px 18px;border:1px solid #7f1d1d;border-radius:10px;
                        background:rgba(239,68,68,0.08);margin-bottom:16px;">
              <p style="color:#fca5a5;margin:0;font-size:0.95rem;">
                {cfg['detail']}
              </p>
              <p style="color:#94a3b8;margin:4px 0 0 0;font-size:0.85rem;">
                This is the risk framework executing correctly — no system error.
              </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

def render_kpi_cards(data: dict[str, Any]) -> None:
    """KPI metric cards from D4 data."""
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        pf = data.get("profit_factor")
        pf_str = f"{pf:.3f}" if pf is not None else "— not yet tracked —"
        st.metric("Profit Factor", pf_str)
    with col2:
        wr = data.get("win_rate")
        wr_str = f"{wr*100:.1f}%" if wr is not None else "— not yet tracked —"
        st.metric("Win Rate", wr_str)
    with col3:
        dd = data.get("current_dd")
        dd_str = f"{dd*100:.2f}%" if dd is not None else "— not yet tracked —"
        dd_color = "normal" if (dd or 0) < 0.05 else "inverse" if (dd or 0) < 0.08 else "off"
        st.metric("Current Drawdown", dd_str)
    with col4:
        cnt = data.get("trade_count", 0)
        st.metric("Total Trades", cnt if cnt > 0 else "— not yet tracked —")
    with col5:
        avg_r = data.get("avg_r")
        avg_r_str = f"{avg_r:.3f}R" if avg_r is not None else "— not yet tracked —"
        st.metric("Avg R", avg_r_str)

def render_equity_chart(data: dict[str, Any]) -> None:
    """Equity curve from account_snapshots with drawdown shading and ATH markers."""
    eq = data.get("equity_curve")
    if eq is None or eq.empty:
        st.caption("Equity curve: — not yet tracked —")
        return

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.7, 0.3], vertical_spacing=0.05,
    )

    # Equity line
    fig.add_trace(
        go.Scatter(x=eq["timestamp"], y=eq["equity"], mode="lines",
                   name="Equity", line={"color": "#22c55e", "width": 2}),
        row=1, col=1,
    )

    # All-time high markers
    cumulative_max = eq["equity"].cummax()
    new_highs = eq[eq["equity"] == cumulative_max]
    fig.add_trace(
        go.Scatter(x=new_highs["timestamp"], y=new_highs["equity"],
                   mode="markers", name="New High",
                   marker={"color": "#22c55e", "size": 6, "symbol": "triangle-up"}),
        row=1, col=1,
    )

    # Drawdown shading
    peak = eq["equity"].cummax()
    dd = (peak - eq["equity"]) / peak
    fig.add_trace(
        go.Scatter(x=eq["timestamp"], y=dd * 100, mode="lines",
                   name="Drawdown", line={"color": "#ef4444", "width": 1},
                   fill="tozeroy", fillcolor="rgba(239,68,68,0.15)"),
        row=2, col=1,
    )

    fig.update_layout(
        height=400, margin={"l": 0, "r": 0, "t": 10, "b": 0},
        hovermode="x unified",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font={"color": "#e2e8f0"},
        legend={"orientation": "h", "y": 1.1},
    )
    fig.update_xaxes(showgrid=False, row=1, col=1)
    fig.update_yaxes(title_text="Equity ($)", showgrid=True, gridcolor="#334155", row=1, col=1)
    fig.update_xaxes(showgrid=False, row=2, col=1)
    fig.update_yaxes(title_text="DD %", showgrid=True, gridcolor="#334155", row=2, col=1)

    st.plotly_chart(fig, use_container_width=True)

def render_price_chart(data: dict[str, Any]) -> None:
    """Trade chart with equity curve as background + trade markers on equity.

    Old-style visualization: each trade is a colored marker (green BUY, red SELL)
    with connecting vertical line, plotted against the equity curve.
    Hover shows entry/exit price, R-multiple, and PnL.
    """
    trades = data.get("trades", [])
    eq = data.get("equity_curve", pd.DataFrame())

    if not trades:
        st.caption("Price chart: no trades to plot")
        return

    fig = go.Figure()

    # Equity curve as thin background reference
    if not eq.empty:
        fig.add_trace(go.Scatter(
            x=eq["timestamp"], y=eq["equity"],
            mode="lines", name="Equity",
            line={"color": "rgba(79, 138, 245, 0.4)", "width": 1},
        ))

    # Plot each trade: entry marker + exit marker + connecting line
    for t in trades:
        entry_time = t.get("entry_time") or t.get("timestamp")
        exit_time = t.get("exit_time")
        entry_price = t.get("entry_price")
        exit_price = t.get("exit_price")
        direction = t.get("direction", "BUY")
        pnl = (t.get("net_pnl") or 0)
        is_win = pnl > 0
        rr = t.get("r_multiple")

        if not entry_time or not entry_price or entry_price <= 0:
            continue

        try:
            ts = pd.Timestamp(entry_time)
        except Exception:
            continue

        # Color and shape
        if direction == "BUY":
            entry_symbol = "triangle-up"
        else:
            entry_symbol = "triangle-down"
        color = "#22c55e" if is_win else "#ef4444"
        label = "WIN" if is_win else "LOSS"

        # Find equity value at trade time for y-axis positioning
        eq_val = None
        if not eq.empty:
            eq_ts = pd.to_datetime(eq["timestamp"], utc=True)
            trade_ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
            closest = (eq_ts - trade_ts).abs().idxmin()
            eq_val = float(eq.iloc[closest]["equity"])

        if eq_val:
            y_val = eq_val
        else:
            y_val = 10000 + (pnl * 5)

        hover = (
            f"{'🟢' if is_win else '🔴'} {direction} {label}<br>"
            f"Entry: ${entry_price:.2f} → Exit: ${exit_price:.2f}<br>"
            f"R: {rr:+.2f} | PnL: ${pnl:+.2f}"
        )

        # Connecting vertical line
        fig.add_trace(go.Scatter(
            x=[ts, ts],
            y=[y_val - 10, y_val + 10],
            mode="lines",
            line={"color": color, "width": 3},
            showlegend=False,
            hoverinfo="skip",
        ))

        # Entry marker
        fig.add_trace(go.Scatter(
            x=[ts], y=[y_val],
            mode="markers",
            marker={"symbol": entry_symbol, "size": 14, "color": color,
                    "line": {"color": "white", "width": 1.5}},
            name=hover,
            hovertext=hover,
            hoverinfo="text",
            showlegend=False,
        ))

    fig.update_layout(
        height=400, margin={"l": 0, "r": 0, "t": 10, "b": 0},
        hovermode="closest",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font={"color": "#e2e8f0"},
        legend={"orientation": "h", "y": 1.1},
    )
    fig.update_xaxes(showgrid=False, title_text="")
    fig.update_yaxes(showgrid=True, gridcolor="#334155", title_text="Equity ($)")

    st.plotly_chart(fig, use_container_width=True)


def render_open_position(data: dict[str, Any]) -> None:
    """Show open position detail, or explicit empty state."""
    pos = data.get("position_detail")
    if pos is None:
        st.markdown(
            "<div style='padding:20px;text-align:center;color:#64748b;border:1px dashed #334155;"
            "border-radius:8px;margin:12px 0;'>No open positions</div>",
            unsafe_allow_html=True,
        )
        return

    det = pos
    direction = det.get("direction", "—")
    entry = det.get("entry_price", 0)
    stop = det.get("stop_loss", 0)
    pnl = det.get("unrealised_pnl", 0)
    pnl_color = "#16a34a" if (pnl or 0) >= 0 else "#dc2626"
    st.markdown(
        f"""
        <div style="padding:14px;border:1px solid #334155;border-radius:8px;margin:12px 0;">
          <strong style="color:{'#22c55e' if direction == 'BUY' else '#ef4444'};">{direction}</strong>
          @ ${entry:,.2f} &nbsp;|&nbsp;
          Stop: ${stop:,.2f} &nbsp;|&nbsp;
          Unrealised: <strong style="color:{pnl_color};">${pnl:+,.2f}</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_trade_log(data: dict[str, Any]) -> None:
    """Trade log — clean old-style table with colored rows and WIN/LOSS tags."""
    trades = data.get("trades", [])
    if not trades:
        st.caption("Trade log: — not yet tracked —")
        return

    rows = []
    for t in reversed(trades[-50:]):
        pnl = (t.get("net_pnl") or 0)
        is_win = pnl > 0
        exit_reason = (t.get("exit_reason") or "—").replace("_", " ")

        entry_str = f"${t['entry_price']:,.2f}" if t.get("entry_price") else "—"
        exit_str = f"${t['exit_price']:,.2f}" if t.get("exit_price") else "—"
        r_str = f"{t['r_multiple']:+.2f}" if t.get("r_multiple") is not None else "—"
        pnl_str = f"${pnl:+,.2f}"
        time_str = (t.get("exit_time") or t.get("timestamp") or "")[:16] if t.get("exit_time") else (t.get("timestamp") or "")[:16]
        badge = "✅" if is_win else "❌"

        rows.append({
            "": badge,
            "Time": time_str,
            "Dir": t.get("direction", "—"),
            "Entry": entry_str,
            "Exit": exit_str,
            "R": r_str,
            "PnL": pnl_str,
            "Exit": exit_reason,
            "_win": is_win,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        st.caption("Trade log: — not yet tracked —")
        return

    def _row_style(row):
        if row.get("_win", False):
            return ["background-color: rgba(22,163,74,0.08)"] * len(row)
        return ["background-color: rgba(220,38,38,0.06)"] * len(row)

    display = ["", "Time", "Dir", "Entry", "Exit", "R", "PnL", "Exit"]
    styled = df[display].style.apply(_row_style, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)

def render_comparison_card(d4: dict[str, Any], d7: dict[str, Any] | None) -> None:
    """Side-by-side D4 | D7 comparison card.

    D7 is in early launch phase — shows days live and sample size
    prominently so the maturity gap is clear, not disguised.
    """
    st.subheader("System Comparison")
    st.caption("D4 has a significant head start. Days live and trade counts are shown alongside performance metrics to make the maturity gap visible.")

    col1, col2 = st.columns(2)

    with col1:
        d4_days = d4.get("days_live", 0)
        st.markdown(
            f"""
            <div style="border:1px solid #334155;border-radius:10px;padding:16px;height:100%;">
              <div style="font-size:1.1rem;font-weight:600;margin-bottom:8px;">D4 (20-bar Donchian)</div>
              <div style="color:#94a3b8;font-size:0.85rem;margin-bottom:12px;">
                Live · Day {d4_days} · {d4.get('trade_count', 0)} trades
              </div>
              <table style="width:100%;border-collapse:collapse;">
                <tr><td style="padding:4px 0;color:#94a3b8;">PF</td>
                    <td style="text-align:right;font-weight:600;">{d4.get('profit_factor', 0):.3f}</td></tr>
                <tr><td style="padding:4px 0;color:#94a3b8;">WR</td>
                    <td style="text-align:right;font-weight:600;">{d4.get('win_rate', 0)*100:.1f}%</td></tr>
                <tr><td style="padding:4px 0;color:#94a3b8;">Total PnL (all-time)</td>
                    <td style="text-align:right;font-weight:600;">${(d4.get('total_pnl', 0) or 0):+,.2f}</td></tr>
                <tr><td style="padding:4px 0;color:#94a3b8;">Equity</td>
                    <td style="text-align:right;font-weight:600;">${d4.get('equity', 0):,.2f}</td></tr>
              </table>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col2:
        if d7 is None:
            st.markdown(
                """
                <div style="border:1px solid #334155;border-radius:10px;padding:16px;height:100%;">
                  <div style="font-size:1.1rem;font-weight:600;margin-bottom:8px;">D7 (10-bar Donchian)</div>
                  <div style="color:#64748b;text-align:center;padding:20px 0;">No data yet</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            return

        d7_days = d7.get("days_live", 0)
        d7_trades = d7.get("trades", 0)
        is_early = d7_trades < 30
        maturity_label = " — early sample" if is_early else ""

        st.markdown(
            f"""
            <div style="border:1px solid #334155;border-radius:10px;padding:16px;height:100%;">
              <div style="font-size:1.1rem;font-weight:600;margin-bottom:8px;">D7 (10-bar Donchian)</div>
              <div style="color:#94a3b8;font-size:0.85rem;margin-bottom:12px;">
                Day {d7_days}{maturity_label} · {d7_trades} trades
                { '<div style=\"color:#64748b;font-size:0.75rem;margin-top:4px;\">Trades computed from historical cache — not live yet</div>' if d7_days < 1 else '' }
              </div>
              <table style="width:100%;border-collapse:collapse;">
                <tr><td style="padding:4px 0;color:#94a3b8;">PF</td>
                    <td style="text-align:right;font-weight:600;">{d7.get('pf', 0):.3f}</td></tr>
                <tr><td style="padding:4px 0;color:#94a3b8;">WR</td>
                    <td style="text-align:right;font-weight:600;">{d7.get('wr', 0)*100:.1f}%</td></tr>
                <tr><td style="padding:4px 0;color:#94a3b8;">Total PnL (all-time)</td>
                    <td style="text-align:right;font-weight:600;">${(d7.get('total_pnl', 0) or 0):+,.2f}</td></tr>
                <tr><td style="padding:4px 0;color:#94a3b8;">Equity</td>
                    <td style="text-align:right;font-weight:600;">${d7.get('equity', 0):,.2f}</td></tr>
              </table>
            </div>
            """,
            unsafe_allow_html=True,
        )

# ── Main ──

def main():
    st.title("AURUM-1 Operations Monitor")

    data = load_overview()
    d7_data = load_d7_aggregate()

    # Health bar (always on top)
    render_health_bar(data)

    # Progress metric + D4 KPI cards
    col_left, col_right = st.columns([3, 1])
    with col_left:
        st.subheader("D4 (20-bar Donchian)")
        render_kpi_cards(data)
    with col_right:
        st.metric("Status", data["status"])
        if data["trade_count"] > 0:
            pnl_total = data.get("total_pnl", 0) or 0
            st.metric("Total PnL (all-time)", f"${pnl_total:+,.2f}")

    # D4 vs D7 side-by-side comparison
    render_comparison_card(data, d7_data)

    # Current state
    st.subheader("Current State")
    render_open_position(data)

    # Performance
    st.subheader("Performance")
    render_equity_chart(data)

    # Price chart with trade markers
    st.subheader("Price & Trade History")
    render_price_chart(data)

    # Trade log
    st.subheader("Trade Log")
    render_trade_log(data)

    # Footer
    st.caption(f"Data source: {PAPER_DB}")
    st.caption("AURUM-1 operations dashboard — read-only. Never modifies trading state.")


if __name__ == "__main__":
    main()
