"""D4 Paper Trader — autonomous paper trading using the best strategy.

D4 (Donchian 20, BUY+SELL, 2R exit) running as a continuous service with:
  - Candle processing via local market cache (forward shadow keeps it populated)
  - PaperBroker for order execution (no real money)
  - RiskManager for position sizing
  - Trade logging to paper_trading.sqlite3

This reads from the forward_shadow_market_cache.sqlite3 that the
aurum1-forward-shadow.service maintains, so no OANDA/yfinance API key is needed.
"""

from __future__ import annotations
import argparse, json, math, os, signal, sqlite3, sys, threading, time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.execution import ExecutionEngine
from aurum1.instruments import InstrumentSpec
from aurum1.risk import RiskManager
from aurum1.signals import CandleRow, TradeInstruction
from scripts.research_edge_prototypes import build_research_features

STRATEGY = "d4_paper_trader"
LOOKBACK = 20
RISK_PCT = 0.0025
MARKET_DB = ROOT / "aurum1" / "data" / "forward_shadow_market_cache.sqlite3"


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

        # Core components (no AurumDataIngestor — we read from local cache)
        self.execution = ExecutionEngine(settings)
        self.risk_mgr = RiskManager(settings)
        self.ohlcv_buffer = pd.DataFrame()
        self.features = pd.DataFrame()
        self.position = None
        self._prev_latest_ts: pd.Timestamp | None = None
        self.trades: list[dict] = []
        self.last_signal_time = None
        self._last_processed_ts: pd.Timestamp | None = None
        self._paper_db = ROOT / "aurum1" / "data" / "paper_trading.sqlite3"
        self._init_paper_db()

        # Load recent data
        self._refresh_data()

        print(f"D4 Paper Trader initialized")
        print(f"  Instrument: XAU/USD")
        print(f"  Market cache: {self.market_db}")
        print(f"  Strategy: Donchian 20, BUY+SELL, 2R exit")
        print(f"  Risk: {RISK_PCT*100:.2f}% per trade")
        print(f"  Broker: paper")
        account = self.execution.broker.get_account_state()
        print(f"  Starting equity: ${account.equity:.2f}")

    def _init_paper_db(self):
        """Create paper_trading.sqlite3 schema if it doesn't exist."""
        self._paper_db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(self._paper_db))) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    units INTEGER NOT NULL,
                    r_multiple REAL,
                    net_pnl REAL,
                    exit_reason TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS account_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    equity REAL NOT NULL,
                    position_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.commit()

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
            with closing(sqlite3.connect(str(self._paper_db))) as conn:
                conn.execute("""
                    INSERT INTO trades (timestamp, direction, entry_price, exit_price,
                        stop_loss, take_profit, units, r_multiple, net_pnl, exit_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    trade["time"], trade["direction"],
                    float(trade["entry"]), float(trade["exit"]),
                    float(self.position["stop"]), float(self.position["target"]),
                    int(self.position["units"]),
                    float(trade["r"]), float(trade["pnl"]), trade["reason"]
                ))
                # Also snapshot equity
                account = self.execution.broker.get_account_state()
                conn.execute("""
                    INSERT INTO account_snapshots (timestamp, equity, position_count)
                    VALUES (?, ?, ?)
                """, (trade["time"], account.equity, int(self.position is not None)))
                conn.commit()
        except Exception as exc:
            print(f"  DB persist error: {exc}")

    def _check_exits(self, row: pd.Series, ts: pd.Timestamp, bar_idx: int):
        """Check if current position should be closed."""
        if self.position is None or bar_idx <= self.position["entry_bar"]:
            return

        o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
        d = self.position["direction"]
        ex_price = None; reason = None

        if d == "BUY":
            if o <= self.position["stop"]: ex_price, reason = o, "stop_loss_gap"
            elif l <= self.position["stop"]: ex_price, reason = self.position["stop"], "stop_loss"
            elif h >= self.position["target"]: ex_price, reason = self.position["target"], "take_profit"
        else:
            if o >= self.position["stop"]: ex_price, reason = o, "stop_loss_gap"
            elif h >= self.position["stop"]: ex_price, reason = self.position["stop"], "stop_loss"
            elif l <= self.position["target"]: ex_price, reason = self.position["target"], "take_profit"

        if ex_price and reason:
            actual_exit = ex_price - self.slip_dist if d == "BUY" else ex_price + self.slip_dist
            gross = self.spec.pnl(d, self.position["entry"], actual_exit, self.position["units"])
            net = gross - self.position["spread"]
            r_val = net / self.position["risk_amt"] if self.position["risk_amt"] > 0 else 0.0
            trade_record = {"direction": d, "entry": self.position["entry"], "exit": actual_exit,
                "r": r_val, "pnl": net, "reason": reason, "time": ts.isoformat()}
            self.trades.append(trade_record)
            self._persist_trade(trade_record)
            # Also close via execution engine for logging
            for pos in self.execution.broker.get_open_positions():
                self.execution.broker._close_position_at_price(pos.position_id, actual_exit, reason)
            print(f"  EXIT {d} R={r_val:+.3f} PnL=${net:+.2f} | {reason}")
            self.position = None

    def _check_entries(self, row: pd.Series, ts: pd.Timestamp, bar_idx: int):
        """Check for new Donchian breakout signals and enter if valid."""
        if self.position is not None:
            return
        if self.features.empty or ts not in self.features.index:
            return

        feat = self.features.loc[ts]
        atr = float(feat["atr_14"])
        if not math.isfinite(atr) or atr <= 0:
            return

        # Check BUY signal: close > 20-bar high
        high_20 = float(self.ohlcv_buffer["high"].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1).loc[ts]) if ts in self.ohlcv_buffer.index else float(feat.get("close", 0))
        # Check SELL signal: close < 20-bar low
        low_20 = float(self.ohlcv_buffer["low"].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1).loc[ts]) if ts in self.ohlcv_buffer.index else float(feat.get("close", 0))
        close = float(row["close"])

        direction = None
        entry_price = None
        stop_loss = None

        if close > high_20 and math.isfinite(high_20):
            direction = "BUY"
            entry_price = float(row["open"]) + self.slip_dist
            stop_loss = entry_price - 2.0 * atr
        elif close < low_20 and math.isfinite(low_20):
            direction = "SELL"
            entry_price = float(row["open"]) - self.slip_dist
            stop_loss = entry_price + 2.0 * atr

        if direction is None or stop_loss is None:
            return
        if (direction == "BUY" and stop_loss >= entry_price) or (direction == "SELL" and stop_loss <= entry_price):
            return

        risk_dist = abs(entry_price - stop_loss)
        take_profit = entry_price + 2.0 * risk_dist if direction == "BUY" else entry_price - 2.0 * risk_dist

        # Create instruction and route through risk manager
        account = self.execution.broker.get_account_state()
        instruction = TradeInstruction(
            timestamp=ts.to_pydatetime(), direction=direction, entry_price=entry_price,
            stop_loss=stop_loss, take_profit=take_profit, atr_at_entry=atr,
            signal_score=1.0, regime="TRENDING_UP" if direction == "BUY" else "TRENDING_DOWN",
            confidence=0.75, machine_mode=STRATEGY)
        risk_order = self.risk_mgr.evaluate(instruction, account, list(self.execution.broker._trade_history))
        if not risk_order.approved:
            return

        result = self.execution.broker.submit_order(risk_order)
        if not result.success:
            return

        eq = account.equity
        spread = 2.0 * self.sp * self.spec.pip_value_per_unit * risk_order.units
        actual_risk = risk_dist * risk_order.units * self.spec.ounces_per_unit

        self.position = {"direction": direction, "entry": float(result.fill_price),
            "stop": stop_loss, "target": take_profit, "entry_bar": bar_idx,
            "units": risk_order.units, "risk_amt": actual_risk, "spread": spread}
        self.last_signal_time = ts
        print(f"  ENTRY {direction} @ ${result.fill_price:.2f} | SL=${stop_loss:.2f} TP=${take_profit:.2f} | Units={risk_order.units}")

    def process_candle(self, row: pd.Series, ts: pd.Timestamp, bar_idx: int):
        """Process one completed M15 candle."""
        self._check_exits(row, ts, bar_idx)
        self._check_entries(row, ts, bar_idx)

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
            elif len(new_indices) == 1:
                # At most 1 new bar, likely the current incomplete one — leave it
                pass

    def run_loop(self, poll_seconds: float = 60.0):
        """Continuous trading loop. Polls for new candles every `poll_seconds`."""
        print(f"\nStarting continuous paper trading loop (poll every {poll_seconds}s)")
        print(f"Press Ctrl+C to stop\n")

        while not self.stop_requested.is_set():
            try:
                self.run_once()
                self._print_status()
            except Exception as exc:
                print(f"  Error in trading loop: {exc}")

            self.stop_requested.wait(poll_seconds)

        self._print_summary()

    def _print_status(self):
        """Print current status line."""
        account = self.execution.broker.get_account_state()
        pos_info = f" | POS: {self.position['direction']} @ ${self.position['entry']:.2f}" if self.position else " | NO POSITION"
        print(f"  [{datetime.now(UTC).strftime('%H:%M:%S')}] EQ=${account.equity:.2f}{pos_info}")

    def _print_summary(self):
        """Print trade summary."""
        print(f"\n{'='*60}")
        print(f"D4 PAPER TRADER — SESSION SUMMARY")
        print(f"{'='*60}")
        account = self.execution.broker.get_account_state()
        print(f"Final equity: ${account.equity:.2f}")
        print(f"Trades: {len(self.trades)}")
        if self.trades:
            r_vals = [t["r"] for t in self.trades]
            wins = sum(1 for r in r_vals if r > 0)
            losses = sum(1 for r in r_vals if r < 0)
            gain = sum(abs(r) for r in r_vals if r > 0)
            loss = sum(abs(r) for r in r_vals if r < 0)
            pf = gain / loss if loss > 0 else 0
            print(f"WR: {wins}/{wins+losses} = {wins/len(r_vals)*100:.1f}%")
            print(f"PF: {pf:.4f}")
            print(f"Net R: {sum(r_vals):+.2f}")
            print(f"Net PnL: ${sum(t['pnl'] for t in self.trades):+.2f}")
            print(f"Exits: {dict(Counter(t['reason'] for t in self.trades))}")


def main():
    p = argparse.ArgumentParser(description="D4 Paper Trader")
    p.add_argument("--poll-seconds", type=float, default=60.0)
    p.add_argument("--run-once", action="store_true", help="Process once and exit")
    args = p.parse_args()

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

    if args.run_once:
        trader.run_once()
        trader._print_summary()
    else:
        trader.run_loop(poll_seconds=args.poll_seconds)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
