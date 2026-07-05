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
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.execution import ExecutionEngine
from aurum1.execution.broker import PositionRecord
from aurum1.instruments import InstrumentSpec
from aurum1.risk import RiskManager
from aurum1.signals import CandleRow, TradeInstruction
from scripts.research_edge_prototypes import build_research_features

STRATEGY = "d4_paper_trader"
LOOKBACK = 20
RISK_PCT = 0.0025
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
        self.market_db = MARKET_DB
        self.spec = InstrumentSpec.from_settings(settings)
        self.sp = 1.5
        self.slip_pips = 0.5
        self.slip_dist = self.slip_pips * self.spec.pip_size
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
        self._last_trade_count = 0
        self._paper_db = ROOT / "aurum1" / "data" / "paper_trading.sqlite3"
        self._last_data_ts = datetime.now(UTC)
        self._stale_warning_logged = False
        self._snapshot_counter = 0
        self._init_paper_db()

        # Restore persistent state from DB before starting
        self._restore_state()

        # Load recent data
        self._refresh_data()

        # Write initial health file
        self._write_health_file(account=self.execution.broker.get_account_state())

        account = self.execution.broker.get_account_state()
        print(f"D4 Paper Trader initialized")
        print(f"  Instrument: XAU/USD")
        print(f"  Market cache: {self.market_db}")
        print(f"  Strategy: Donchian 20, BUY+SELL, 2R exit")
        print(f"  Risk: {RISK_PCT*100:.2f}% per trade")
        print(f"  Broker: paper (PaperBroker handles SL/TP natively)")
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
                # 1. Restore trades into broker's trade history
                rows = conn.execute(
                    "SELECT entry_time, exit_time, direction, entry_price, exit_price, "
                    "stop_loss, take_profit, units, risk_amount, r_multiple, net_pnl, "
                    "spread_cost, slippage_cost, exit_reason FROM trades "
                    "ORDER BY id"
                ).fetchall()
                for row in rows:
                    trade = {
                        "open_time": row[0] or "",
                        "closed_at": row[1] or "",
                        "direction": row[2],
                        "entry": row[3],
                        "actual_entry": row[3],
                        "exit": row[4] or 0.0,
                        "actual_exit": row[4] or 0.0,
                        "stop_loss": row[5],
                        "take_profit": row[6],
                        "units": row[7],
                        "risk_amount": row[8] or 0.0,
                        "r": row[9] or 0.0,
                        "r_multiple": row[9] or 0.0,
                        "net_pnl": row[10] or 0.0,
                        "pnl": row[10] or 0.0,
                        "pnl_after_fees": row[10] or 0.0,
                        "spread_cost": row[11] or 0.0,
                        "total_slippage_cost": row[12] or 0.0,
                        "reason": row[13] or "",
                    }
                    self.trades.append(trade)
                    self.execution.broker._trade_history.append(trade)

                # 2. Restore last known equity from most recent snapshot
                snap = conn.execute(
                    "SELECT equity, balance, peak_equity, daily_pnl FROM account_snapshots "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if snap is not None:
                    self.execution.broker._equity = float(snap[0])
                    self.execution.broker._balance = float(snap[1])
                    self.execution.broker._peak_equity_30d = float(snap[2])
                    self.execution.broker._daily_pnl = float(snap[3])
                    self._last_trade_count = len(self.execution.broker._trade_history)

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

                # 5. Restore open positions into PaperBroker
                open_rows = conn.execute(
                    "SELECT position_id, direction, entry_price, current_price, stop_loss, "
                    "take_profit, units, lot_size, intended_entry_price, entry_slippage, "
                    "entry_slippage_cost, open_time FROM open_positions ORDER BY id"
                ).fetchall()
                for row in open_rows:
                    open_time = datetime.fromisoformat(row[11]) if row[11] else datetime.now(UTC)
                    pos = PositionRecord(
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
                    )
                    self.execution.broker._positions[row[0]] = pos
                    print(f"  Restored open position: {row[1]} @ ${row[2]:.2f} SL=${row[4]:.2f} TP=${row[5]:.2f}")
        except Exception as exc:
            print(f"  State restore error: {exc}")

    def _save_snapshot(self):
        """Persist current account state to account_snapshots."""
        try:
            account = self.execution.broker.get_account_state()
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
                    len(self.execution.broker._trade_history),
                ))
                conn.commit()
        except Exception as exc:
            print(f"  Snapshot error: {exc}")

    def _save_open_positions(self):
        """Persist any open positions to the DB so they survive restart."""
        try:
            positions = self.execution.broker.get_open_positions()
            with closing(sqlite3.connect(str(self._paper_db))) as conn:
                # Clear stale entries first
                conn.execute("DELETE FROM open_positions")
                for pos in positions:
                    conn.execute("""
                        INSERT OR REPLACE INTO open_positions
                            (position_id, direction, entry_price, current_price,
                             stop_loss, take_profit, units, lot_size,
                             intended_entry_price, entry_slippage, entry_slippage_cost,
                             open_time)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        pos.position_id,
                        pos.direction,
                        round(pos.open_price, 2),
                        round(pos.current_price, 2),
                        round(pos.stop_loss, 2),
                        round(pos.take_profit, 2),
                        round(pos.units, 4),
                        round(pos.lot_size, 4),
                        round(pos.intended_entry_price, 2) if pos.intended_entry_price is not None else None,
                        round(pos.entry_slippage, 4),
                        round(pos.entry_slippage_cost, 4),
                        pos.open_time.isoformat() if hasattr(pos.open_time, "isoformat") else str(pos.open_time),
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
            pass

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
                account = self.execution.broker.get_account_state()
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
                "trade_count": len(self.execution.broker._trade_history),
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
            HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
            HEALTH_FILE.write_text(json.dumps(health, indent=2, default=str))
        except Exception as exc:
            pass  # health file is non-critical

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

            with closing(sqlite3.connect(str(self._paper_db))) as conn:
                conn.execute("""
                    INSERT INTO trades
                        (entry_time, exit_time, direction, entry_price, exit_price,
                         stop_loss, take_profit, units, risk_amount, r_multiple,
                         net_pnl, spread_cost, slippage_cost, exit_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    trade.get("open_time", ""),
                    trade.get("closed_at", ""),
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
                    trade["reason"]
                ))
                conn.commit()
        except Exception as exc:
            print(f"  DB persist error: {exc}")

    def _new_trades(self):
        """Return trades completed since last check via PaperBroker."""
        history = self.execution.broker._trade_history
        new_count = len(history) - self._last_trade_count
        self._last_trade_count = len(history)
        return history[-new_count:] if new_count > 0 else []

    def process_candle(self, row: pd.Series, ts: pd.Timestamp, bar_idx: int):
        """Process one completed M15 candle via PaperBroker for exits, then check entries."""
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
            # Clear open positions from DB since trade just closed
            self._clear_open_positions()

        # Cap memory: trim trades list to prevent unbounded growth
        if len(self.trades) > TRADE_HISTORY_MAX:
            excess = len(self.trades) - TRADE_HISTORY_MAX
            self.trades = self.trades[excess:]

        # Step 3: Check for new entry (only if no open positions)
        if self.execution.broker.get_open_positions():
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
            entry_price = float(row["open"]) + self.slip_dist
            stop_loss = entry_price - 2.0 * atr
            self._signals_seen += 1
        elif close < low_20 and math.isfinite(low_20):
            direction = "SELL"
            entry_price = float(row["open"]) - self.slip_dist
            stop_loss = entry_price + 2.0 * atr
            self._signals_seen += 1

        if direction is None or stop_loss is None:
            return
        if (direction == "BUY" and stop_loss >= entry_price) or (direction == "SELL" and stop_loss <= entry_price):
            return

        risk_dist = abs(entry_price - stop_loss)
        take_profit = entry_price + 2.0 * risk_dist if direction == "BUY" else entry_price - 2.0 * risk_dist

        # Route through risk manager and execution engine
        account = self.execution.broker.get_account_state()
        current_spread = account.current_spread_pips
        self._spread_history.append(current_spread)
        self._signals_seen += 1

        instruction = TradeInstruction(
            timestamp=ts.to_pydatetime(), direction=direction, entry_price=entry_price,
            stop_loss=stop_loss, take_profit=take_profit, atr_at_entry=atr,
            signal_score=1.0, regime="TRENDING_UP" if direction == "BUY" else "TRENDING_DOWN",
            confidence=0.75, machine_mode=STRATEGY)
        risk_order = self.risk_mgr.evaluate(instruction, account, list(self.execution.broker._trade_history))
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

    def _print_status(self):
        """Print current status line with spread and metrics."""
        account = self.execution.broker.get_account_state()
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
        account = self.execution.broker.get_account_state()
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
        account = self.execution.broker.get_account_state()
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


def _acquire_pid_lock() -> bool:
    """Create PID file. Return True if acquired, False if another instance is running."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        if PID_FILE.exists():
            pid_str = PID_FILE.read_text().strip()
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
        PID_FILE.write_text(str(os.getpid()))
        return True
    except Exception as exc:
        print(f"WARNING: Could not acquire PID lock: {exc}")
        # Non-fatal: proceed without lock
        return True


def _release_pid_lock():
    """Remove PID file if owned by this process."""
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except Exception:
        pass


def main():
    p = argparse.ArgumentParser(description="D4 Paper Trader")
    p.add_argument("--poll-seconds", type=float, default=60.0)
    p.add_argument("--run-once", action="store_true", help="Process once and exit")
    p.add_argument("--force", action="store_true", help="Override PID lock if stale")
    args = p.parse_args()

    # Single-instance protection
    if not _acquire_pid_lock():
        return 1

    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    # Ensure paper mode
    settings.setdefault("broker", {})["paper_trade"] = True
    settings.setdefault("broker", {}).setdefault("oanda", {})
    settings["broker"]["oanda"]["default_environment"] = "practice"

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
        _release_pid_lock()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
