"""D4 Paper Trader — autonomous paper trading using the best strategy.

D4 (Donchian 20, BUY+SELL, 2R exit) running as a continuous service with:
  - Candle processing via local market cache (forward shadow keeps it populated)
  - PaperBroker for order execution (no real money)
  - RiskManager for position sizing
  - Persistent state: trades and snapshots survive restart
  - Single-instance protection via PID file

This reads from the forward_shadow_market_cache.sqlite3 that the
aurum1-forward-shadow.service maintains, so no OANDA/yfinance API key is needed.
"""

from __future__ import annotations
import argparse, json, math, os, signal, sqlite3, sys, threading, time
from collections import Counter
from contextlib import closing
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.execution import ExecutionEngine
from aurum1.execution.broker import PositionRecord
from aurum1.instruments import InstrumentSpec
from aurum1.risk import AccountState, RiskManager
from aurum1.signals import CandleRow, TradeInstruction
from scripts.research.research_edge_prototypes import build_research_features

STRATEGY = "d4_paper_trader"
LOOKBACK = 20
RISK_PCT = 0.0025  # display-only; real risk from settings.yaml
MARKET_DB = ROOT / "aurum1" / "data" / "forward_shadow_market_cache.sqlite3"
PID_FILE = ROOT / "run" / "d4_paper_trader.pid"
HEALTH_FILE = ROOT / "run" / "d4_paper_trader_health.json"
TRADE_HISTORY_MAX = 10000
SNAPSHOT_INTERVAL_CYCLES = 15   # ~15 min at 60s poll
OBSERVABILITY_REPORT_INTERVAL = 60  # ~1h at 60s poll (show summary every N cycles)


class D4PaperTrader:
    """Autonomous paper trading system using D4 strategy."""

    def __init__(self, settings: dict[str, Any]):
        self.settings = settings
        # Paths are settings-injectable (defaults as before) so tests can point
        # at temp files and the OANDA practice mode can use its own DB.
        pt = settings.get("paper_trading", {})
        self._paper_db = Path(pt.get("db_path", ROOT / "aurum1" / "data" / "paper_trading.sqlite3"))
        self.market_db = Path(pt.get("market_db_path", MARKET_DB))
        self.health_file = Path(pt.get("health_file", HEALTH_FILE))
        self.spec = InstrumentSpec.from_settings(settings)
        # NOTE: Slippage and spread are handled by PaperBroker (in the broker
        # module) using Gaussian slippage and session-aware spread estimation.
        # No hardcoded slippage constants needed here.
        self.stop_requested = threading.Event()

        # Observable metrics
        self._signals_seen = 0
        self._missed_signals = 0
        self._missed_signal_log: list[dict] = []  # timestamp, direction, price, reason
        self._total_latency = 0.0
        self._latency_count = 0
        self._latency_min = float("inf")
        self._latency_max = 0.0
        self._slippage_history: list[float] = []   # entry slippage (signed)
        self._exit_slippage_history: list[float] = []  # exit slippage (signed)
        self._spread_history: list[float] = []
        self._start_time = datetime.now(UTC)
        self._last_entry_time: datetime | None = None
        self._last_direction: str | None = None
        self.execution = ExecutionEngine(settings)
        self.risk_mgr = RiskManager(settings)
        self.ohlcv_buffer = pd.DataFrame()
        self.features = pd.DataFrame()
        self.trades: list[dict] = []
        self.last_signal_time = None
        self._last_processed_ts: pd.Timestamp | None = None
        self._prev_latest_ts: pd.Timestamp | None = None
        # Trader-owned open-position meta (position_id -> levels/risk). The
        # broker-agnostic source for persistence and OANDA R reconstruction.
        self._open_meta: dict[str, dict] = {}
        self._daily_pnl = 0.0
        self._daily_pnl_date: date | None = None
        self._peak_equity_30d = 0.0
        self._last_data_ts = datetime.now(UTC)
        self._stale_warning_logged = False
        self._snapshot_counter = 0
        self._init_paper_db()

        # Restore persistent state from DB before starting
        self._restore_state()

        # Load recent data
        self._refresh_data()

        # Write initial health file
        self._write_health_file(account=self._account_state())

        account = self._account_state()
        broker_mode = "oanda (real OANDA practice/live account)" if self.execution.broker.server_managed_sl_tp else "paper (PaperBroker handles SL/TP natively)"
        print(f"D4 Paper Trader initialized")
        print(f"  Instrument: XAU/USD")
        print(f"  Market cache: {self.market_db}")
        print(f"  Strategy: Donchian 20, BUY+SELL, 2R exit")
        print(f"  Risk: {RISK_PCT*100:.2f}% per trade")
        print(f"  Broker: {broker_mode}")
        print(f"  Restored equity: ${account.equity:.2f}")
        print(f"  Trade history: {len(self.trades)} trades")

    def _init_paper_db(self):
        """Create paper_trading.sqlite3 schema if it doesn't exist."""
        self._paper_db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(self._paper_db))) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            # Migrate: add entry_time if column missing (safe repeated run)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_time TEXT,
                    exit_time TEXT,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    units INTEGER NOT NULL,
                    risk_amount REAL,
                    r_multiple REAL,
                    net_pnl REAL,
                    spread_cost REAL,
                    slippage_cost REAL,
                    exit_reason TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # Add entry_time / risk_amount columns if upgrading from old schema
            for col in ("entry_time", "exit_time", "risk_amount", "spread_cost", "slippage_cost"):
                try:
                    conn.execute(f"ALTER TABLE trades ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass  # column already exists
            # Migrate: position_id (OANDA trade ID) for cross-restart dedup
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN position_id TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS account_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    equity REAL NOT NULL,
                    balance REAL NOT NULL DEFAULT 0,
                    peak_equity REAL NOT NULL DEFAULT 0,
                    daily_pnl REAL NOT NULL DEFAULT 0,
                    position_count INTEGER DEFAULT 0,
                    trade_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # Add columns for older schema
            for col in ("balance", "peak_equity", "daily_pnl", "trade_count"):
                try:
                    conn.execute(f"ALTER TABLE account_snapshots ADD COLUMN {col} REAL DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS open_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position_id TEXT NOT NULL UNIQUE,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    units REAL NOT NULL,
                    lot_size REAL NOT NULL DEFAULT 0,
                    intended_entry_price REAL,
                    entry_slippage REAL DEFAULT 0,
                    entry_slippage_cost REAL DEFAULT 0,
                    open_time TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # Migrate: risk_amount per open position (needed to reconstruct
            # r_multiple for broker-detected closes after a restart).
            try:
                conn.execute("ALTER TABLE open_positions ADD COLUMN risk_amount REAL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # column already exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS missed_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    price REAL,
                    reason TEXT NOT NULL,
                    at_entry REAL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.commit()

    def _restore_state(self):
        """Read persistent state from DB so restart doesn't reset equity."""
        if not self._paper_db.exists():
            return
        try:
            with closing(sqlite3.connect(str(self._paper_db))) as conn:
                # Probe schema for old-style timestamp column
                cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
                has_old_timestamp = "timestamp" in cols

                # 1. Restore trades into broker's trade history
                if has_old_timestamp:
                    rows = conn.execute(
                        "SELECT timestamp, entry_time, exit_time, direction, entry_price, "
                        "exit_price, stop_loss, take_profit, units, risk_amount, r_multiple, "
                        "net_pnl, spread_cost, slippage_cost, exit_reason, position_id FROM trades "
                        "ORDER BY id"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT entry_time, exit_time, direction, entry_price, exit_price, "
                        "stop_loss, take_profit, units, risk_amount, r_multiple, net_pnl, "
                        "spread_cost, slippage_cost, exit_reason, position_id FROM trades "
                        "ORDER BY id"
                    ).fetchall()
                # When has_old_timestamp, SELECT has a leading timestamp column
                # that shifts all column indices by 1
                off = 1 if has_old_timestamp else 0
                for row in rows:
                    trade = {
                        "open_time": row[0 + off] or "",
                        "closed_at": row[1 + off] or "",
                        "direction": row[2 + off],
                        "entry": row[3 + off],
                        "actual_entry": row[3 + off],
                        "exit": row[4 + off] or 0.0,
                        "actual_exit": row[4 + off] or 0.0,
                        "stop_loss": row[5 + off],
                        "take_profit": row[6 + off],
                        "units": row[7 + off],
                        "risk_amount": row[8 + off] or 0.0,
                        "r": row[9 + off] or 0.0,
                        "r_multiple": row[9 + off] or 0.0,
                        "net_pnl": row[10 + off] or 0.0,
                        "pnl": row[10 + off] or 0.0,
                        "pnl_after_fees": row[10 + off] or 0.0,
                        "spread_cost": row[11 + off] or 0.0,
                        "total_slippage_cost": row[12 + off] or 0.0,
                        "reason": row[13 + off] or "",
                        "position_id": row[14 + off] if len(row) > 14 + off else None,
                    }
                    self.trades.append(trade)

                # 2. Restore equity/peak/daily from most recent snapshot.
                # Daily P&L is DERIVED from today's closed trades rather than
                # trusted from the snapshot: snapshots written before the
                # 2026-09-10 fix carry a corrupted all-time accumulator, and
                # deriving is self-correcting either way. Equity/peak seed the
                # paper broker (via restore_state_from_snapshot below); for a
                # server-managed broker (OANDA) the platform is the source of
                # truth and only daily P&L / peak are tracked trader-side.
                snap = conn.execute(
                    "SELECT equity, balance, peak_equity FROM account_snapshots "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
                snap_equity = float(snap[0]) if snap is not None else float(
                    self.settings.get("broker", {}).get("paper_initial_equity", 10000.0))
                snap_balance = float(snap[1]) if snap is not None else snap_equity
                self._peak_equity_30d = float(snap[2]) if snap is not None else snap_equity
                self._daily_pnl_date = datetime.now(UTC).date()
                today_pnl = conn.execute(
                    "SELECT COALESCE(SUM(net_pnl), 0) FROM trades "
                    "WHERE exit_time IS NOT NULL AND date(exit_time) = date('now')"
                ).fetchone()
                self._daily_pnl = float(today_pnl[0]) if today_pnl else 0.0

                # 3. Restore last_processed_ts from settings table
                last_ts = conn.execute(
                    "SELECT value FROM settings WHERE key='last_processed_ts'"
                ).fetchone()
                if last_ts is not None and last_ts[0]:
                    try:
                        self._last_processed_ts = pd.Timestamp(last_ts[0], tz="UTC")
                    except Exception:
                        pass
                # 4. Restore missed signal log
                missed_rows = conn.execute(
                    "SELECT timestamp, direction, price, reason FROM missed_signals ORDER BY id"
                ).fetchall()
                for row in missed_rows:
                    self._missed_signal_log.append({
                        "timestamp": row[0],
                        "direction": row[1],
                        "price": row[2],
                        "reason": row[3],
                    })
                    self._missed_signals += 1

                # 3. Restore open positions: build open-meta (levels/risk) for
                # broker-agnostic closes and PositionRecords for the paper broker.
                open_rows = conn.execute(
                    "SELECT position_id, direction, entry_price, current_price, stop_loss, "
                    "take_profit, units, lot_size, intended_entry_price, entry_slippage, "
                    "entry_slippage_cost, open_time, risk_amount FROM open_positions ORDER BY id"
                ).fetchall()
                restored_positions: list[PositionRecord] = []
                for row in open_rows:
                    open_time = datetime.fromisoformat(row[11]) if row[11] else datetime.now(UTC)
                    self._open_meta[row[0]] = {
                        "position_id": row[0],
                        "direction": row[1],
                        "intended_entry": float(row[8]) if row[8] is not None else float(row[2]),
                        "actual_entry": float(row[2]),
                        "stop_loss": float(row[4]),
                        "take_profit": float(row[5]),
                        "units": float(row[6]),
                        "lot_size": float(row[7]),
                        "entry_slippage": float(row[9]) if row[9] is not None else 0.0,
                        "entry_slippage_cost": float(row[10]) if row[10] is not None else 0.0,
                        "risk_amount": float(row[12]) if len(row) > 12 and row[12] is not None else 0.0,
                        "open_time": open_time,
                    }
                    restored_positions.append(PositionRecord(
                        position_id=row[0],
                        instrument="XAU_USD",
                        direction=row[1],
                        open_price=float(row[2]),
                        current_price=float(row[3]),
                        stop_loss=float(row[4]),
                        take_profit=float(row[5]),
                        units=float(row[6]),
                        lot_size=float(row[7]),
                        intended_entry_price=float(row[8]) if row[8] is not None else float(row[2]),
                        entry_slippage=float(row[9]) if row[9] is not None else 0,
                        entry_slippage_cost=float(row[10]) if row[10] is not None else 0,
                        open_time=open_time,
                        unrealised_pnl=0.0,
                        broker="paper",
                    ))
                    print(f"  Restored open position: {row[1]} @ ${row[2]:.2f} SL=${row[4]:.2f} TP=${row[5]:.2f}")

                # Seed broker-internal state (PaperBroker consumes; OandaBroker
                # no-ops since the platform is the source of truth).
                self.execution.broker.restore_state_from_snapshot({
                    "equity": snap_equity,
                    "balance": snap_balance,
                    "peak_equity_30d": self._peak_equity_30d,
                    "daily_pnl": self._daily_pnl,
                    "daily_pnl_date": self._daily_pnl_date,
                    "trade_history": self.trades,
                    "positions": restored_positions,
                })
        except Exception as exc:
            print(f"  State restore error: {exc}")

    def _account_state(self) -> AccountState:
        """Broker account state, merged with trader-side daily P&L / peak.

        Server-managed brokers (OANDA) report daily_pnl=0.0 and peak=equity from
        their summary endpoint, which would silently disable the daily-loss and
        drawdown kill switches. For those, merge the trader's own daily P&L
        (derived from today's closed trades) and tracked peak equity.
        """
        raw = self.execution.broker.get_account_state()
        if not self.execution.broker.server_managed_sl_tp:
            return raw
        today = datetime.now(UTC).date().isoformat()
        daily = sum(
            float(t.get("net_pnl", 0.0))
            for t in self.trades
            if (t.get("closed_at") or "").startswith(today)
        )
        peak = max(raw.equity, self._peak_equity_30d)
        return AccountState(
            equity=raw.equity,
            balance=raw.balance,
            open_trade_count=raw.open_trade_count,
            daily_pnl=daily,
            peak_equity_30d=peak,
            current_spread_pips=raw.current_spread_pips,
            open_risk_pct=raw.open_risk_pct,
        )

    def _save_snapshot(self):
        """Persist current account state to account_snapshots."""
        try:
            account = self._account_state()
            with closing(sqlite3.connect(str(self._paper_db))) as conn:
                conn.execute("""
                    INSERT INTO account_snapshots
                        (timestamp, equity, balance, peak_equity, daily_pnl,
                         position_count, trade_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    datetime.now(UTC).isoformat(),
                    round(account.equity, 2),
                    round(account.balance, 2),
                    round(account.peak_equity_30d, 2),
                    round(account.daily_pnl, 2),
                    account.open_trade_count,
                    len(self.trades),
                ))
                conn.commit()
        except Exception as exc:
            print(f"  Snapshot error: {exc}")

    def _save_open_positions(self):
        """Persist open positions (from trader-owned _open_meta) so they survive restart.

        Broker-agnostic: both PaperBroker and OandaBroker feed the same _open_meta
        keyed by position_id, so SL/TP and risk_amount survive a restart and an
        OANDA close can be reconstructed with the correct r_multiple.
        """
        try:
            with closing(sqlite3.connect(str(self._paper_db))) as conn:
                # Clear stale entries first
                conn.execute("DELETE FROM open_positions")
                for pid, meta in self._open_meta.items():
                    intended_entry = meta.get("intended_entry")
                    open_time = meta.get("open_time", datetime.now(UTC))
                    conn.execute("""
                        INSERT OR REPLACE INTO open_positions
                            (position_id, direction, entry_price, current_price,
                             stop_loss, take_profit, units, lot_size,
                             intended_entry_price, entry_slippage, entry_slippage_cost,
                             risk_amount, open_time)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        pid,
                        meta.get("direction", ""),
                        round(float(meta.get("actual_entry", intended_entry or 0.0)), 2),
                        round(float(meta.get("actual_entry", intended_entry or 0.0)), 2),
                        round(float(meta.get("stop_loss", 0.0)), 2),
                        round(float(meta.get("take_profit", 0.0)), 2),
                        round(float(meta.get("units", 0.0)), 4),
                        round(float(meta.get("lot_size", 0.0)), 4),
                        round(float(intended_entry), 2) if intended_entry else None,
                        round(float(meta.get("entry_slippage", 0.0)), 4),
                        round(float(meta.get("entry_slippage_cost", 0.0)), 4),
                        round(float(meta.get("risk_amount", 0.0)), 4),
                        open_time.isoformat() if hasattr(open_time, "isoformat") else str(open_time),
                    ))
                conn.commit()
        except Exception as exc:
            print(f"  DB open-positions error: {exc}")

    def _clear_open_positions(self):
        """Remove all open positions from DB (called after trade close)."""
        try:
            with closing(sqlite3.connect(str(self._paper_db))) as conn:
                conn.execute("DELETE FROM open_positions")
                conn.commit()
        except Exception as exc:
            print(f"  Clear-positions error: {exc}")

    def _save_last_processed_ts(self, ts: pd.Timestamp):
        """Persist last processed timestamp so restart resumes correctly."""
        try:
            with closing(sqlite3.connect(str(self._paper_db))) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("last_processed_ts", ts.isoformat()),
                )
                conn.commit()
        except Exception as exc:
            print(f"  Settings save error: {exc}")

    def _save_missed_signal(self, ts: str, direction: str, price: float | None, reason: str):
        """Persist a missed signal to the SQLite database."""
        try:
            with closing(sqlite3.connect(str(self._paper_db))) as conn:
                conn.execute(
                    "INSERT INTO missed_signals (timestamp, direction, price, reason) VALUES (?, ?, ?, ?)",
                    (ts, direction, price, reason),
                )
                conn.commit()
        except Exception as exc:
            print(f"  DB missed-signal error: {exc}")

    def _missed_signal_report(self) -> list[dict]:
        """Return a concise summary of missed signals by reason (last 24h only)."""
        cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
        recent = [m for m in self._missed_signal_log if m.get("timestamp", "") >= cutoff]
        by_reason: dict[str, int] = {}
        for m in recent:
            r = m.get("reason", "unknown")
            by_reason[r] = by_reason.get(r, 0) + 1
        return [{"reason": k, "count": v} for k, v in sorted(by_reason.items(), key=lambda x: -x[1])]

    def _write_health_file(self, account: Any = None):
        """Write a lightweight JSON health file for external monitoring."""
        try:
            if account is None:
                account = self._account_state()
            positions = self.execution.broker.get_open_positions()
            avg_slip = sum(self._slippage_history[-100:]) / max(len(self._slippage_history[-100:]), 1)
            avg_exit_slip = sum(self._exit_slippage_history[-100:]) / max(len(self._exit_slippage_history[-100:]), 1)
            avg_spread = sum(self._spread_history[-100:]) / max(len(self._spread_history[-100:]), 1)
            avg_latency = (self._total_latency / self._latency_count) if self._latency_count > 0 else 0
            min_latency = self._latency_min if math.isfinite(self._latency_min) else 0
            health = {
                "timestamp": datetime.now(UTC).isoformat(),
                "pid": os.getpid(),
                "uptime_seconds": (datetime.now(UTC) - self._start_time).total_seconds(),
                "equity": round(account.equity, 2),
                "peak_equity": round(account.peak_equity_30d, 2),
                "drawdown_pct": round((account.peak_equity_30d - account.equity) / account.peak_equity_30d * 100, 2) if account.peak_equity_30d > 0 else 0,
                "balance": round(account.balance, 2),
                "daily_pnl": round(account.daily_pnl, 2),
                "open_positions": account.open_trade_count,
                "trade_count": len(self.trades),
                "signals_seen": self._signals_seen,
                "missed_signals": self._missed_signals,
                "missed_signal_reasons": self._missed_signal_report(),
                "avg_entry_slippage_units": round(avg_slip, 4),
                "avg_exit_slippage_units": round(avg_exit_slip, 4),
                "avg_spread_pips": round(avg_spread, 2),
                "avg_latency_seconds": round(avg_latency, 3),
                "min_latency_seconds": round(min_latency, 3),
                "max_latency_seconds": round(self._latency_max, 3),
                "market_latest_candle_age_minutes": round((datetime.now(UTC) - self._last_data_ts).total_seconds() / 60.0, 1),
            }
            self.health_file.parent.mkdir(parents=True, exist_ok=True)
            self.health_file.write_text(json.dumps(health, indent=2, default=str))
        except Exception as exc:
            print(f"  Health file error (non-critical): {exc}")

    def _refresh_data(self):
        """Read latest M15 candles from the forward shadow market cache."""
        try:
            if not self.market_db.exists():
                print(f"  Market cache not found: {self.market_db}")
                return

            raw = load_ohlcv("M15", self.market_db)
            if raw.empty:
                print(f"  No M15 data in market cache")
                return

            if "timestamp" in raw.columns:
                raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True)
                raw = raw.set_index("timestamp")

            raw = raw.sort_index()
            new_latest = raw.index[-1]

            # Stale data detection: alert if latest candle > 2 hours old during market hours
            now = datetime.now(UTC)
            age_minutes = (now - new_latest.to_pydatetime().replace(tzinfo=UTC)).total_seconds() / 60.0
            is_weekend = now.weekday() >= 5 or (now.weekday() == 4 and now.hour >= 22) or (now.weekday() == 0 and now.hour < 1)
            if age_minutes > 120 and not is_weekend:
                if not self._stale_warning_logged:
                    print(f"  WARNING: Stale market data — latest candle is {age_minutes:.0f} minutes old ({new_latest})")
                    self._stale_warning_logged = True
                    self._send_alert("stale_data", f"Market data {age_minutes:.0f} min stale, latest: {new_latest}")
            else:
                self._stale_warning_logged = False
            self._last_data_ts = now

            # Only reprocess if we have new data
            if self._prev_latest_ts is not None and new_latest <= self._prev_latest_ts:
                return

            self._prev_latest_ts = new_latest
            self.ohlcv_buffer = raw.tail(300).copy()

            if len(self.ohlcv_buffer) >= LOOKBACK + 5:
                self.features = build_research_features(self.ohlcv_buffer)
        except Exception as exc:
            print(f"  Data refresh error: {exc}")

    def _persist_trade(self, trade: dict):
        """Write a completed trade to the paper_trading SQLite database."""
        try:
            risk_amt = float(trade.get("risk_amount", trade.get("risk_amt", 0)))
            r_val = float(trade.get("r", trade.get("r_multiple", 0)))
            pnl = float(trade.get("pnl", trade.get("net_pnl", 0)))
            spread = float(trade.get("spread_cost", trade.get("fee", 0)))
            slip = float(trade.get("total_slippage_cost", 0))
            entry_ts = trade.get("open_time", "")
            exit_ts = trade.get("closed_at", "")

            with closing(sqlite3.connect(str(self._paper_db))) as conn:
                # Probe schema: if the old `timestamp` column exists, include it
                cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
                has_old_timestamp = "timestamp" in cols

                if has_old_timestamp:
                    conn.execute("""
                        INSERT INTO trades
                            (timestamp, entry_time, exit_time, direction, entry_price,
                             exit_price, stop_loss, take_profit, units, risk_amount,
                             r_multiple, net_pnl, spread_cost, slippage_cost, exit_reason,
                             position_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        entry_ts or exit_ts,  # populate old timestamp column
                        entry_ts,
                        exit_ts,
                        trade["direction"],
                        float(trade.get("entry", trade.get("actual_entry", 0))),
                        float(trade.get("exit", trade.get("actual_exit", 0))),
                        float(trade.get("stop_loss", 0)),
                        float(trade.get("take_profit", 0)),
                        int(trade.get("units", 1)),
                        round(risk_amt, 2) if risk_amt else None,
                        round(r_val, 4),
                        round(pnl, 2),
                        round(spread, 2),
                        round(slip, 2),
                        trade["reason"],
                        trade.get("position_id")
                    ))
                else:
                    conn.execute("""
                        INSERT INTO trades
                            (entry_time, exit_time, direction, entry_price, exit_price,
                             stop_loss, take_profit, units, risk_amount, r_multiple,
                             net_pnl, spread_cost, slippage_cost, exit_reason, position_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        entry_ts,
                        exit_ts,
                        trade["direction"],
                        float(trade.get("entry", trade.get("actual_entry", 0))),
                        float(trade.get("exit", trade.get("actual_exit", 0))),
                        float(trade.get("stop_loss", 0)),
                        float(trade.get("take_profit", 0)),
                        int(trade.get("units", 1)),
                        round(risk_amt, 2) if risk_amt else None,
                        round(r_val, 4),
                        round(pnl, 2),
                        round(spread, 2),
                        round(slip, 2),
                        trade["reason"],
                        trade.get("position_id")
                    ))
                conn.commit()
        except Exception as exc:
            print(f"  DB persist error: {exc}")

    def _new_trades(self):
        """Return trades closed since the last poll (broker-agnostic).

        PaperBroker drains its simulated history; OandaBroker reconstructs closes
        from the platform. Dedup is by position_id so restarts are idempotent.
        """
        known_ids = {str(t.get("position_id")) for t in self.trades if t.get("position_id")}
        return self.execution.broker.poll_closed_trades(known_ids=known_ids, open_meta=self._open_meta)

    def process_candle(self, row: pd.Series, ts: pd.Timestamp, bar_idx: int):
        """Process one completed M15 candle, then check entries.

        PaperBroker simulates SL/TP exits natively; server-managed brokers
        (OANDA) enforce them platform-side and the trader polls closes via
        poll_closed_trades.
        """
        # Step 1: Let PaperBroker check SL/TP natively (handles slippage, spread, logging)
        candle_row = CandleRow(
            timestamp=ts.to_pydatetime(),
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row["volume"]),
            atr_14=max(1e-9, float(row["high"] - row["low"])),
            adx_14=0.0, ema_9=0.0, ema_20=0.0,
            session_london=1, session_ny=0, session_overlap=0,
        )
        if not self.execution.broker.server_managed_sl_tp:
            self.execution.broker.update_prices(candle_row)

        # Step 2: Persist newly closed trades
        for trade in self._new_trades():
            self.trades.append(trade)
            self._persist_trade(trade)
            d = trade.get("direction", "?")
            r = trade.get("r", trade.get("r_multiple", 0))
            pnl = trade.get("pnl", trade.get("net_pnl", 0))
            reason = trade.get("reason", "unknown")
            # Track exit slippage
            intended_exit = trade.get("intended_exit", 0)
            actual_exit = trade.get("actual_exit", trade.get("exit", 0))
            if intended_exit and actual_exit:
                exit_slip = actual_exit - intended_exit if d == "BUY" else intended_exit - actual_exit
                self._exit_slippage_history.append(exit_slip)
            print(f"  EXIT {d} R={r:+.3f} PnL=${pnl:+.2f} | {reason}")
            # Position closed — drop its open-meta and clear DB open positions
            self._open_meta.pop(trade.get("position_id"), None)
            self._clear_open_positions()

        # Cap memory: trim trades list to prevent unbounded growth
        if len(self.trades) > TRADE_HISTORY_MAX:
            excess = len(self.trades) - TRADE_HISTORY_MAX
            self.trades = self.trades[excess:]

        # Step 3: Check for new entry (only if flat). The trader-side _open_meta
        # gate covers API lag where get_open_positions() has not caught up yet.
        if self.execution.broker.get_open_positions() or self._open_meta:
            return
        if self.features.empty or ts not in self.features.index:
            return

        feat = self.features.loc[ts]
        atr = float(feat["atr_14"])
        if not math.isfinite(atr) or atr <= 0:
            return

        # Donchian breakout: close > 20-bar high (BUY) or close < 20-bar low (SELL)
        high_20 = float(self.ohlcv_buffer["high"].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1).loc[ts]) if ts in self.ohlcv_buffer.index else float(feat.get("close", 0))
        low_20 = float(self.ohlcv_buffer["low"].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1).loc[ts]) if ts in self.ohlcv_buffer.index else float(feat.get("close", 0))
        close = float(row["close"])

        direction = None
        entry_price = None
        stop_loss = None

        if close > high_20 and math.isfinite(high_20):
            direction = "BUY"
            entry_price = float(row["open"])  # PaperBroker handles slippage
            stop_loss = entry_price - 2.0 * atr
        elif close < low_20 and math.isfinite(low_20):
            direction = "SELL"
            entry_price = float(row["open"])  # PaperBroker handles slippage
            stop_loss = entry_price + 2.0 * atr

        if direction is None or stop_loss is None:
            return
        if (direction == "BUY" and stop_loss >= entry_price) or (direction == "SELL" and stop_loss <= entry_price):
            return

        # A breakout signal was detected — count it. Note this happens before the
        # risk manager runs, so "seen" includes signals later rejected/skipped.
        self._signals_seen += 1

        # Risk distance from entry price (PaperBroker adds Gaussian slippage on fill)
        # 2R exit: TP at +2x risk distance, SL at -1x risk distance
        raw_entry = float(row["open"])
        raw_stop = raw_entry - 2.0 * atr if direction == "BUY" else raw_entry + 2.0 * atr
        risk_dist = abs(raw_entry - raw_stop)
        take_profit = raw_entry + 2.0 * risk_dist if direction == "BUY" else raw_entry - 2.0 * risk_dist

        # Route through risk manager and execution engine
        account = self._account_state()
        current_spread = account.current_spread_pips
        self._spread_history.append(current_spread)

        instruction = TradeInstruction(
            timestamp=ts.to_pydatetime(), direction=direction, entry_price=entry_price,
            stop_loss=stop_loss, take_profit=take_profit, atr_at_entry=atr,
            signal_score=1.0, regime="TRENDING_UP" if direction == "BUY" else "TRENDING_DOWN",
            confidence=0.75, machine_mode=STRATEGY)

        # Data-integrity guard for server-managed brokers: reject if the cached
        # candle close diverges from the live OANDA mid-price (stale cache would
        # otherwise feed a far-off entry that the broker collar then rejects).
        if self.execution.broker.server_managed_sl_tp:
            live_price = self.execution.broker._current_market_price()
            collar = float(self.settings.get("execution", {}).get("oanda_price_collar_pct", 5.0))
            if live_price and abs(float(close) - live_price) / live_price * 100.0 > collar:
                self._missed_signals += 1
                ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S+00:00") if hasattr(ts, "strftime") else str(ts)
                self._missed_signal_log.append({
                    "timestamp": ts_str, "direction": direction,
                    "price": round(entry_price, 2), "reason": "cache_vs_live_collar",
                })
                self._save_missed_signal(ts_str, direction, round(entry_price, 2), "cache_vs_live_collar")
                print(f"  SKIP {direction} — stale cache (close {float(close):.2f} vs live {live_price:.2f})")
                return

        risk_order = self.risk_mgr.evaluate(instruction, account, list(self.trades))
        if not risk_order.approved:
            self._missed_signals += 1
            rejection_reason = risk_order.rejection_reason or "unknown"
            ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S+00:00") if hasattr(ts, "strftime") else str(ts)
            entry = {
                "timestamp": ts_str,
                "direction": direction,
                "price": round(entry_price, 2),
                "reason": rejection_reason,
            }
            self._missed_signal_log.append(entry)
            self._save_missed_signal(ts_str, direction, round(entry_price, 2), rejection_reason)
            print(f"  SKIP {direction} at ${entry_price:.2f} — {rejection_reason}")
            return

        l_at_entry = datetime.now(UTC)
        result = self.execution.execute(risk_order)
        latency = (datetime.now(UTC) - l_at_entry).total_seconds()
        self._total_latency += latency
        self._latency_count += 1
        if latency < self._latency_min:
            self._latency_min = latency
        if latency > self._latency_max:
            self._latency_max = latency

        if not result.success:
            return

        # Track slippage: intended vs actual fill
        slip = 0.0
        if result.fill_price is not None:
            slip = result.fill_price - instruction.entry_price
            self._slippage_history.append(slip)

        # Track open-position meta keyed by position_id (OANDA trade ID where
        # available) so closes can be reconstructed and risk survives restart.
        raw_resp = result.raw_response or {}
        position_id = str(raw_resp.get("tradeID") or result.order_id or "")
        if not position_id:
            position_id = f"{STRATEGY}_{result.fill_time.strftime('%Y%m%d_%H%M%S')}"
        units_value = float(risk_order.units) if risk_order.units else float(risk_order.lot_size or 0.0)
        self._open_meta[position_id] = {
            "position_id": position_id,
            "direction": direction,
            "intended_entry": float(instruction.entry_price),
            "actual_entry": float(result.fill_price) if result.fill_price is not None else float(instruction.entry_price),
            "stop_loss": float(stop_loss),
            "take_profit": float(take_profit),
            "units": units_value,
            "lot_size": float(risk_order.lot_size) if risk_order.lot_size else 0.0,
            "risk_amount": float(risk_order.risk_amount) if risk_order.risk_amount else 0.0,
            "entry_slippage": float(slip),
            "entry_slippage_cost": float(slip) * units_value * self.spec.ounces_per_unit,
            "open_time": datetime.now(UTC),
        }
        self.last_signal_time = ts
        self._last_entry_time = datetime.now(UTC)
        self._last_direction = direction
        self._save_open_positions()
        print(f"  ENTRY {direction} @ ${result.fill_price:.2f} | SL=${stop_loss:.2f} TP=${take_profit:.2f} | Units={risk_order.units} | Slippage=${slip:.3f} | Latency={latency:.3f}s")

    def run_once(self):
        """Process all new candles since the last check."""
        self._refresh_data()
        if self.ohlcv_buffer.empty or len(self.ohlcv_buffer) < LOOKBACK + 5:
            return

        # Determine which candles are new
        if self._last_processed_ts is None:
            # First run: only process the very latest completed candle
            self._last_processed_ts = self.ohlcv_buffer.index[-2]  # leave current as incomplete
            self.process_candle(self.ohlcv_buffer.iloc[-2], pd.Timestamp(self.ohlcv_buffer.index[-2]), len(self.ohlcv_buffer) - 2)
        else:
            # Process all candles newer than last processed
            new_mask = self.ohlcv_buffer.index > self._last_processed_ts
            new_indices = self.ohlcv_buffer.index[new_mask]

            # Don't process the last index (current candle may still be forming)
            if len(new_indices) > 1:
                for i in range(len(new_indices) - 1):
                    ts = new_indices[i]
                    idx = self.ohlcv_buffer.index.get_loc(ts)
                    try:
                        self.process_candle(self.ohlcv_buffer.iloc[idx], pd.Timestamp(ts), idx)
                    except Exception as exc:
                        print(f"  Candle processing error at {ts}: {exc}")
                self._last_processed_ts = new_indices[-2]
                self._save_last_processed_ts(new_indices[-2])
            elif len(new_indices) == 1:
                # At most 1 new bar, likely the current incomplete one — leave it
                pass

    def run_loop(self, poll_seconds: float = 60.0):
        """Continuous trading loop. Polls for new candles every `poll_seconds`."""
        print(f"\nStarting continuous paper trading loop (poll every {poll_seconds}s)")
        print(f"Press Ctrl+C to stop\n")
        # Snapshot at start of loop
        self._save_snapshot()

        while not self.stop_requested.is_set():
            try:
                self.run_once()
                self._print_status()
                self._write_health_file()
                # Persist open positions every cycle for restart safety
                self._save_open_positions()
                self._snapshot_counter += 1
                if self._snapshot_counter >= SNAPSHOT_INTERVAL_CYCLES:
                    self._save_snapshot()
                    self._snapshot_counter = 0
            except Exception as exc:
                print(f"  Error in trading loop: {exc}")

            self.stop_requested.wait(poll_seconds)

        self._save_snapshot()
        self._write_health_file()
        self._print_summary()

    def _send_alert(self, title: str, message: str) -> None:
        """Send a critical alert via webhook if ALERT_WEBHOOK_URL is configured."""
        webhook_url = os.getenv("ALERT_WEBHOOK_URL")
        if not webhook_url:
            return
        try:
            import requests
            requests.post(webhook_url, json={"text": f"[{STRATEGY}] {title}: {message}"}, timeout=5)
        except Exception:
            print(f"  Alert webhook failed (non-critical)")

    def _print_status(self):
        """Print current status line with spread and metrics."""
        account = self._account_state()
        positions = self.execution.broker.get_open_positions()
        if positions:
            p = positions[0]
            pos_info = f" | {p.direction} @ ${p.open_price:.2f} SL=${p.stop_loss:.2f} TP=${p.take_profit:.2f}"
        else:
            pos_info = " | NO POSITION"
        dd = (account.peak_equity_30d - account.equity) / account.peak_equity_30d * 100 if account.peak_equity_30d > 0 else 0
        spread_str = f" Sprd={account.current_spread_pips:.1f}p"
        print(f"  [{datetime.now(UTC).strftime('%H:%M:%S')}] EQ=${account.equity:.2f} DD={dd:.1f}%{pos_info}{spread_str}")

        # Periodic observability summary
        if self._snapshot_counter > 0 and self._snapshot_counter % OBSERVABILITY_REPORT_INTERVAL == 0:
            self._print_observability_report()

    def _print_observability_report(self):
        """Print a structured summary of all observability metrics."""
        uptime = (datetime.now(UTC) - self._start_time).total_seconds()
        account = self._account_state()
        avg_slip = sum(self._slippage_history) / max(len(self._slippage_history), 1)
        avg_exit_slip = sum(self._exit_slippage_history) / max(len(self._exit_slippage_history), 1)
        avg_spread = sum(self._spread_history) / max(len(self._spread_history), 1)
        avg_lat = (self._total_latency / self._latency_count) if self._latency_count > 0 else 0
        min_lat = self._latency_min if math.isfinite(self._latency_min) else 0
        reasons = self._missed_signal_report()
        reasons_str = ", ".join(f"{r['reason']}:{r['count']}" for r in reasons[:5]) if reasons else "none"

        print(f"  {'=' * 60}")
        print(f"  [OBSERVABILITY REPORT] ─ Uptime: {uptime/3600:.1f}h")
        print(f"    Signals: {self._signals_seen} seen, {self._missed_signals} missed ({reasons_str})")
        print(f"    Trades: {len(self.trades)} closed")
        print(f"    Entry Slippage: avg={avg_slip:+.4f}  ({len(self._slippage_history)} samples)")
        print(f"    Exit Slippage:  avg={avg_exit_slip:+.4f}  ({len(self._exit_slippage_history)} samples)")
        print(f"    Spread:         avg={avg_spread:.2f}p  ({len(self._spread_history)} samples)")
        print(f"    Latency:        avg={avg_lat:.4f}s  min={min_lat:.4f}s  max={self._latency_max:.4f}s")
        print(f"    Latest candle:  {self._prev_latest_ts}")
        print(f"  {'=' * 60}")

    def _print_summary(self):
        """Print trade summary."""
        print(f"\n{'='*60}")
        print(f"D4 PAPER TRADER — SESSION SUMMARY")
        print(f"{'='*60}")
        account = self._account_state()
        print(f"Final equity: ${account.equity:.2f}")
        print(f"Peak equity: ${account.peak_equity_30d:.2f}")
        dd = (account.peak_equity_30d - account.equity) / account.peak_equity_30d * 100 if account.peak_equity_30d > 0 else 0
        print(f"Drawdown: {dd:.2f}%")
        print(f"Trades: {len(self.trades)}")
        if self.trades:
            r_vals = []
            for t in self.trades:
                r = t.get("r", t.get("r_multiple", t.get("net_pnl", 0)))
                r_vals.append(r)
            wins = sum(1 for r in r_vals if r > 0)
            losses = sum(1 for r in r_vals if r < 0)
            gain = sum(abs(r) for r in r_vals if r > 0)
            loss = sum(abs(r) for r in r_vals if r < 0)
            pf = gain / loss if loss > 0 else 0
            print(f"WR: {wins}/{wins+losses} = {wins/len(r_vals)*100:.1f}%")
            print(f"PF: {pf:.4f}")
            print(f"Net R: {sum(r_vals):+.2f}")
            print(f"Net PnL: ${sum(t.get('pnl', t.get('net_pnl', 0)) for t in self.trades):+.2f}")
            reasons = [t.get("reason", "unknown") for t in self.trades]
            print(f"Exits: {dict(Counter(reasons))}")
        print(f"{'='*60}\n")


def _acquire_pid_lock(pid_file: Path | None = None, force: bool = False) -> bool:
    """Create PID file. Return True if acquired, False if another instance is running.

    `pid_file` allows a distinct lock per instance (e.g. the paper shadow alongside
    the OANDA practice trader). `force` bypasses the live-process check.
    """
    lock = pid_file or PID_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if lock.exists() and not force:
            pid_str = lock.read_text().strip()
            if pid_str:
                try:
                    pid = int(pid_str)
                    # Check if process is alive (Unix: kill 0)
                    os.kill(pid, 0)
                    print(f"ERROR: Another D4 process is running (PID {pid}). Use --force to override.")
                    return False
                except (OSError, ValueError):
                    # Stale PID file — process is dead
                    pass
        lock.write_text(str(os.getpid()))
        return True
    except Exception as exc:
        print(f"WARNING: Could not acquire PID lock: {exc}")
        # Non-fatal: proceed without lock
        return True


def _release_pid_lock(pid_file: Path | None = None):
    """Remove PID file if owned by this process."""
    lock = pid_file or PID_FILE
    try:
        if lock.exists() and lock.read_text().strip() == str(os.getpid()):
            lock.unlink()
    except Exception:
        pass  # PID file removal is best-effort


def main():
    p = argparse.ArgumentParser(description="D4 Paper Trader")
    p.add_argument("--poll-seconds", type=float, default=60.0)
    p.add_argument("--run-once", action="store_true", help="Process once and exit")
    p.add_argument("--force", action="store_true", help="Override PID lock if stale")
    p.add_argument("--broker", choices=["paper", "oanda"], default="paper",
                   help="Execution broker: paper (in-memory sim, default) or oanda "
                        "(real OANDA practice/live account). OANDA mode requires "
                        "ALLOW_OANDA_ORDERS=true (and ALLOW_LIVE_TRADING=true + "
                        "OANDA_ENV=live for live).")
    p.add_argument("--pid-file", type=Path, default=None,
                   help="Override the PID-lock file (default run/d4_paper_trader.pid). "
                        "Lets a paper shadow run alongside the OANDA practice trader.")
    p.add_argument("--health-file", type=Path, default=None,
                   help="Override the health file (default run/d4_paper_trader_health.json).")
    args = p.parse_args()

    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    pt = settings.setdefault("paper_trading", {})
    if args.pid_file:
        pt["pid_file"] = str(args.pid_file)
    if args.health_file:
        pt["health_file"] = str(args.health_file)
    pid_file = Path(pt["pid_file"]) if pt.get("pid_file") else None

    # Single-instance protection (per-instance pid file for the shadow)
    if not _acquire_pid_lock(pid_file, force=args.force):
        return 1

    if args.broker == "paper":
        # Ensure paper mode
        settings.setdefault("broker", {})["paper_trade"] = True
        settings.setdefault("broker", {}).setdefault("oanda", {})
        settings["broker"]["oanda"]["default_environment"] = "practice"
    else:
        # OANDA practice/live: real broker path. Interlocks are enforced by
        # OandaBroker.__init__ (_assert_oanda_interlocks). Default the record to
        # its own DB so the paper evidence trail stays clean.
        settings.setdefault("broker", {})["paper_trade"] = False
        settings.setdefault("broker", {}).setdefault("oanda", {})
        settings["broker"]["oanda"]["default_environment"] = "practice"
        pt = settings.setdefault("paper_trading", {})
        if not pt.get("db_path"):
            pt["db_path"] = str(ROOT / "aurum1" / "data" / "oanda_practice.sqlite3")

    # Fail-fast: requested broker must match engine routing
    if bool(settings.get("broker", {}).get("paper_trade", True)) != (args.broker == "paper"):
        print("ERROR: broker routing mismatch — requested --broker %s" % args.broker, file=sys.stderr)
        _release_pid_lock(pid_file)
        return 1

    trader = D4PaperTrader(settings)

    def signal_handler(signum, frame):
        print("\nShutdown requested...")
        trader.stop_requested.set()
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        if args.run_once:
            trader.run_once()
            trader._print_summary()
        else:
            trader.run_loop(poll_seconds=args.poll_seconds)
    finally:
        _release_pid_lock(pid_file)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
