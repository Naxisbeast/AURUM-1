"""Forward shadow runner for the locked raw Donchian fixed-2R candidate.

This script prepares and maintains a shadow ledger only. It never creates an
OANDA broker, never submits orders, never enables SELL logic, and never changes
strategy parameters. It reads closed M15 candles from a local SQLite market
cache and records the trade decisions that the locked research candidate would
have made.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import math
import os
import signal
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

REQUIRED_PYTHON = (3, 12)
if sys.version_info[:2] != REQUIRED_PYTHON:
    sys.stderr.write(
        "AURUM-1 forward shadow requires Python 3.12. "
        "Run with the project .venv or Python 3.12 interpreter, not user-site Python "
        f"{sys.version_info.major}.{sys.version_info.minor}.\n"
    )
    raise SystemExit(2)

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import AurumDataIngestor, load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.donchian_research_runner import donchian_signals
from scripts.research_edge_prototypes import build_research_features

STRATEGY_NAME = "raw_donchian_fixed_2r"
LOOKBACK = 20
RISK_PER_TRADE_PCT = 0.0025
MIN_DURATION_MONTHS = 3
DEFAULT_SHADOW_DB = ROOT / "reports" / "forward_shadow" / "donchian_shadow.sqlite3"
DEFAULT_REPORT_DIR = ROOT / "reports" / "forward_shadow"
DEFAULT_LOG_FILE = ROOT / "logs" / "forward_shadow_donchian.log"
DEFAULT_MARKET_DB = ROOT / "aurum1" / "data" / "forward_shadow_market_cache.sqlite3"
STALE_CANDLE_MINUTES = 45.0
EXPECTED_HEARTBEAT_SECONDS = 300.0
LOGGER = logging.getLogger("aurum1.forward_shadow")


@dataclass
class ShadowSignal:
    signal_time: str
    entry_time: str
    strategy: str
    direction: str
    status: str
    skip_reason: str | None
    entry_price: float
    stop_loss: float
    take_profit: float
    atr: float
    units: float
    risk_amount: float
    target_risk_amount: float
    spread_estimate: float
    slippage_estimate: float
    exit_time: str | None = None
    exit_reason: str | None = None


@dataclass
class ShadowTrade:
    signal_time: str
    entry_time: str
    exit_time: str
    strategy: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    units: float
    risk_amount: float
    spread_estimate: float
    entry_slippage_estimate: float
    exit_slippage_estimate: float
    exit_price: float
    exit_reason: str
    gross_pnl: float
    net_pnl: float
    r_multiple: float
    holding_bars: int


@dataclass
class ShadowCandle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    signal_decision: str
    notes: str


@dataclass
class ShadowState:
    equity_curve: list[tuple[str, float, float]]
    signals: list[ShadowSignal]
    trades: list[ShadowTrade]
    candles: list[ShadowCandle]
    skipped: Counter[str]


@dataclass
class OpenShadowPosition:
    signal_time: str
    entry_time: str
    entry_bar: int
    entry_price: float
    stop_loss: float
    take_profit: float
    atr: float
    units: float
    risk_amount: float
    target_risk_amount: float
    spread_estimate: float
    entry_slippage_estimate: float
    slippage_distance: float


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(getattr(args, "log_file", DEFAULT_LOG_FILE))
    enforce_runtime_environment(args.command)
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    assert_shadow_safety(settings)
    if args.command == "init":
        init_shadow_db(args.shadow_db, settings)
        print(f"Initialized forward shadow DB: {args.shadow_db}")
        return 0
    if args.command == "run-once":
        state = run_shadow_once(settings, args.market_db, args.start_date)
        init_shadow_db(args.shadow_db, settings)
        write_shadow_state(args.shadow_db, state, settings, args.start_date)
        print_run_summary(state, args.shadow_db)
        return 0
    if args.command == "service":
        run_service(args, settings)
        return 0
    if args.command == "status":
        report = status_report(args.shadow_db)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            print_status(report)
        return 0
    if args.command == "weekly-report":
        report = weekly_report(args.shadow_db, args.report_dir, args.as_of)
        print_weekly_report(report)
        return 0
    raise RuntimeError(f"Unsupported command: {args.command}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Forward shadow runner for locked raw Donchian fixed-2R.")
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create the forward shadow ledger tables.")
    init.set_defaults(market_db=None, start_date=None, report_dir=DEFAULT_REPORT_DIR, as_of=None)

    run_once = sub.add_parser("run-once", help="Update the shadow ledger from cached closed candles.")
    run_once.add_argument("--market-db", type=Path, default=None)
    run_once.add_argument("--start-date", default=None, help="UTC start date/time for the 3-month frozen shadow window.")
    run_once.set_defaults(report_dir=DEFAULT_REPORT_DIR, as_of=None)

    service = sub.add_parser("service", help="Run the continuous cloud forward-shadow loop.")
    service.add_argument("--market-db", type=Path, default=None)
    service.add_argument("--start-date", default=None, help="UTC start date/time for the frozen 3-month shadow window.")
    service.add_argument("--poll-seconds", type=float, default=60.0)
    service.add_argument("--heartbeat-seconds", type=float, default=300.0)
    service.add_argument("--fetch-lookback-minutes", type=float, default=180.0)
    service.add_argument("--no-fetch", action="store_true", help="Process existing cache only; intended for tests/offline diagnosis.")
    service.set_defaults(report_dir=DEFAULT_REPORT_DIR, as_of=None)

    status = sub.add_parser("status", help="Print forward-shadow health/status from SQLite.")
    status.add_argument("--json", action="store_true")
    status.set_defaults(market_db=None, start_date=None, report_dir=DEFAULT_REPORT_DIR, as_of=None)

    report = sub.add_parser("weekly-report", help="Write the latest weekly shadow report.")
    report.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    report.add_argument("--as-of", default=None, help="UTC report timestamp. Defaults to now.")
    report.set_defaults(market_db=None, start_date=None)
    return parser.parse_args(argv)


def assert_shadow_safety(settings: dict[str, Any]) -> None:
    if not bool(settings.get("broker", {}).get("paper_trade", True)):
        raise RuntimeError("Forward shadow requires broker.paper_trade=true")
    if _truthy_env("ALLOW_OANDA_ORDERS"):
        raise RuntimeError("Forward shadow requires ALLOW_OANDA_ORDERS=false/unset")
    if _truthy_env("ALLOW_LIVE_TRADING"):
        raise RuntimeError("Forward shadow requires ALLOW_LIVE_TRADING=false/unset")
    configured = settings.get("forward_shadow", {})
    if configured and configured.get("strategy") not in {None, STRATEGY_NAME}:
        raise RuntimeError(f"Forward shadow strategy must remain {STRATEGY_NAME}")
    if configured and int(configured.get("lookback", LOOKBACK)) != LOOKBACK:
        raise RuntimeError(f"Forward shadow Donchian lookback is locked at {LOOKBACK}")
    if configured and str(configured.get("exit_mode", "FIXED")).upper() != "FIXED":
        raise RuntimeError("Forward shadow exit_mode is locked to FIXED")
    if configured and str(configured.get("direction", "BUY_ONLY")).upper() != "BUY_ONLY":
        raise RuntimeError("Forward shadow direction is locked to BUY_ONLY")
    if configured and abs(float(configured.get("risk_per_trade_pct", RISK_PER_TRADE_PCT)) - RISK_PER_TRADE_PCT) > 1e-12:
        raise RuntimeError("Forward shadow risk_per_trade_pct is locked at 0.25%")
    if configured and bool(configured.get("allow_oanda_orders", False)):
        raise RuntimeError("Forward shadow config must keep allow_oanda_orders=false")
    if str(os.getenv("OANDA_ENV", "practice")).lower() == "live":
        raise RuntimeError("Forward shadow data mode requires OANDA_ENV=practice/unset, never live")


def run_service(args: argparse.Namespace, settings: dict[str, Any]) -> None:
    stop_requested = {"value": False}

    def _request_stop(signum: int, _frame: Any) -> None:
        stop_requested["value"] = True
        LOGGER.info("graceful_shutdown_requested signal=%s", signum)
        record_event(args.shadow_db, "shutdown_requested", "INFO", f"signal={signum}", {})

    for signum in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if signum is not None:
            signal.signal(signum, _request_stop)

    init_shadow_db(args.shadow_db, settings)
    record_event(
        args.shadow_db,
        "service_start",
        "INFO",
        "forward shadow service started",
        {"strategy": STRATEGY_NAME, "start_date": args.start_date, "no_fetch": bool(args.no_fetch)},
    )
    LOGGER.info("service_start strategy=%s poll_seconds=%s no_fetch=%s", STRATEGY_NAME, args.poll_seconds, args.no_fetch)
    last_heartbeat = 0.0
    while not stop_requested["value"]:
        loop_started = time.time()
        try:
            if not args.no_fetch:
                try:
                    refresh = refresh_market_cache(settings, args.market_db, lookback_minutes=float(args.fetch_lookback_minutes))
                    record_event(args.shadow_db, "data_refresh", "INFO", "market cache refresh completed", refresh)
                    LOGGER.info("data_refresh rows=%s start=%s end=%s", refresh.get("rows"), refresh.get("start"), refresh.get("end"))
                except Exception as exc:
                    LOGGER.exception("data_refresh_error")
                    record_event(args.shadow_db, "data_error", "ERROR", str(exc), {"type": type(exc).__name__})
            state = run_shadow_once(settings, args.market_db, args.start_date)
            write_shadow_state(args.shadow_db, state, settings, args.start_date)
            record_event(
                args.shadow_db,
                "shadow_update",
                "INFO",
                "shadow ledger update completed",
                {"signals": len(state.signals), "trades": len(state.trades), "skipped": sum(state.skipped.values())},
            )
        except Exception as exc:  # pragma: no cover - exercised in integration/cloud.
            LOGGER.exception("shadow_loop_error")
            record_event(args.shadow_db, "runtime_error", "ERROR", str(exc), {"type": type(exc).__name__})

        now = time.time()
        if now - last_heartbeat >= float(args.heartbeat_seconds):
            heartbeat = status_report(args.shadow_db)
            record_event(args.shadow_db, "heartbeat", "INFO", "forward shadow heartbeat", heartbeat)
            LOGGER.info(
                "heartbeat latest_candle=%s signals=%s trades=%s errors_24h=%s",
                heartbeat.get("latest_candle"),
                heartbeat.get("signal_count"),
                heartbeat.get("trade_count"),
                heartbeat.get("errors_24h"),
            )
            last_heartbeat = now
        sleep_for = max(1.0, float(args.poll_seconds) - (time.time() - loop_started))
        end_sleep = time.time() + sleep_for
        while time.time() < end_sleep and not stop_requested["value"]:
            time.sleep(min(1.0, end_sleep - time.time()))

    record_event(args.shadow_db, "service_stop", "INFO", "forward shadow service stopped", {})
    LOGGER.info("service_stop")


def refresh_market_cache(settings: dict[str, Any], market_db_arg: Path | None, *, lookback_minutes: float) -> dict[str, Any]:
    market_db = resolve_market_db(settings, market_db_arg)
    if not os.getenv(str(settings.get("broker", {}).get("oanda", {}).get("api_key_env", "OANDA_API_KEY"))):
        raise RuntimeError("Missing OANDA_API_KEY; cannot refresh forward-shadow market cache")
    fetch_settings = copy.deepcopy(settings)
    fetch_settings.setdefault("data", {})
    fetch_settings["data"]["db_path"] = str(market_db)
    fetch_settings.setdefault("broker", {}).setdefault("oanda", {})
    fetch_settings["broker"]["oanda"]["default_environment"] = "practice"
    end = datetime.now(UTC)
    existing = safe_load_ohlcv("M15", market_db)
    if existing.empty:
        start = end - timedelta(minutes=max(float(lookback_minutes), 60.0 * 24.0 * 5.0))
    else:
        start_ts = pd.Timestamp(existing.index.max()).to_pydatetime()
        start = min(end - timedelta(minutes=15), start_ts - timedelta(minutes=max(float(lookback_minutes), 30.0)))
    ingestor = AurumDataIngestor(fetch_settings)
    frame = ingestor.fetch_ohlcv_range("M15", start, end)
    if frame.empty:
        raise RuntimeError("OANDA returned no complete M15 candles during refresh")
    validate_raw_fetch(frame)
    before = len(existing)
    ingestor.persist_ohlcv("M15", frame)
    after = len(safe_load_ohlcv("M15", market_db))
    return {
        "rows": int(len(frame)),
        "stored_before": int(before),
        "stored_after": int(after),
        "start": pd.to_datetime(frame["timestamp"], utc=True).min().isoformat(),
        "end": pd.to_datetime(frame["timestamp"], utc=True).max().isoformat(),
        "market_db": str(market_db),
    }


def run_shadow_once(settings: dict[str, Any], market_db_arg: Path | None, start_date: str | None) -> ShadowState:
    market_db = resolve_market_db(settings, market_db_arg)
    ohlcv = safe_load_ohlcv("M15", market_db)
    if ohlcv.empty:
        raise RuntimeError(f"No M15 candles available in {market_db}")
    ohlcv = validate_ohlcv(ohlcv).sort_index()
    features = build_research_features(ohlcv)
    start_ts = parse_start_date(start_date, settings)
    signals = [signal for signal in donchian_signals(ohlcv, features, lookback=LOOKBACK, htf_filter=False) if pd.Timestamp(signal.signal_time) >= start_ts]
    return simulate_locked_shadow(ohlcv, features, settings, signals, start_ts)


def simulate_locked_shadow(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    signals: list[Any],
    start_ts: pd.Timestamp,
) -> ShadowState:
    spec = InstrumentSpec.from_settings(settings)
    initial_equity = float(settings.get("broker", {}).get("paper_initial_equity", 10000.0))
    spread_pips = float(settings.get("execution", {}).get("paper_spread_pips", 1.5))
    slippage_pips = float(settings.get("execution", {}).get("slippage_std_pips", 0.5))
    slippage_distance = slippage_pips * spec.pip_size
    signals_by_entry: dict[int, list[Any]] = {}
    for signal in signals:
        signals_by_entry.setdefault(int(signal.entry_bar), []).append(signal)

    equity = initial_equity
    peak = initial_equity
    position: OpenShadowPosition | None = None
    signal_rows: list[ShadowSignal] = []
    trade_rows: list[ShadowTrade] = []
    candle_rows: list[ShadowCandle] = []
    equity_rows: list[tuple[str, float, float]] = []
    skipped: Counter[str] = Counter()

    for bar_index, (timestamp, row) in enumerate(ohlcv.iterrows()):
        ts = pd.Timestamp(timestamp)
        if ts < start_ts:
            continue
        decisions: list[str] = []
        if position is not None and bar_index > position.entry_bar:
            maybe_trade = maybe_close_position(row, ts, bar_index, position, spec, slippage_distance)
            if maybe_trade is not None:
                equity += maybe_trade.net_pnl
                trade_rows.append(maybe_trade)
                signal_rows = update_signal_exit(signal_rows, maybe_trade)
                position = None
                decisions.append(f"exit:{maybe_trade.exit_reason}")
        for signal in signals_by_entry.get(bar_index, []):
            if position is not None:
                skipped["open_position_skip"] += 1
                signal_rows.append(
                    make_signal_row(signal, settings, spec, equity, spread_pips, slippage_distance, "skipped", "open_position_skip")
                )
                decisions.append("skipped:open_position_skip")
                continue
            shadow_signal = make_signal_row(signal, settings, spec, equity, spread_pips, slippage_distance, "entered", None)
            signal_rows.append(shadow_signal)
            decisions.append("entered")
            position = OpenShadowPosition(
                signal_time=shadow_signal.signal_time,
                entry_time=shadow_signal.entry_time,
                entry_bar=int(signal.entry_bar),
                entry_price=shadow_signal.entry_price,
                stop_loss=shadow_signal.stop_loss,
                take_profit=shadow_signal.take_profit,
                atr=shadow_signal.atr,
                units=shadow_signal.units,
                risk_amount=shadow_signal.risk_amount,
                target_risk_amount=shadow_signal.target_risk_amount,
                spread_estimate=shadow_signal.spread_estimate,
                entry_slippage_estimate=shadow_signal.slippage_estimate / 2.0,
                slippage_distance=slippage_distance,
            )
        peak = max(peak, equity)
        drawdown = (equity - peak) / peak if peak > 0.0 else 0.0
        candle_rows.append(
            ShadowCandle(
                timestamp=ts.isoformat(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0)),
                signal_decision=";".join(decisions) if decisions else "no_signal",
                notes="",
            )
        )
        equity_rows.append((ts.isoformat(), float(equity), float(drawdown)))

    return ShadowState(equity_curve=equity_rows, signals=signal_rows, trades=trade_rows, candles=candle_rows, skipped=skipped)


def make_signal_row(
    signal: Any,
    settings: dict[str, Any],
    spec: InstrumentSpec,
    equity: float,
    spread_pips: float,
    slippage_distance: float,
    status: str,
    skip_reason: str | None,
) -> ShadowSignal:
    intended_entry = float(signal.entry_price)
    stop_distance = abs(intended_entry - float(signal.stop_loss))
    target_risk = float(equity) * RISK_PER_TRADE_PCT
    raw_units = target_risk / (stop_distance * spec.ounces_per_unit) if stop_distance > 0.0 and spec.ounces_per_unit > 0.0 else spec.min_units
    units = spec.lots_to_units(spec.round_lots(spec.units_to_lots(raw_units)))
    entry_price = intended_entry + slippage_distance
    stop_loss = entry_price - stop_distance
    take_profit = entry_price + 2.0 * stop_distance
    actual_risk = abs(entry_price - stop_loss) * units * spec.ounces_per_unit
    spread_estimate = 2.0 * spread_pips * spec.pip_value_per_unit * units
    total_slippage_estimate = 2.0 * slippage_distance * units * spec.ounces_per_unit
    return ShadowSignal(
        signal_time=str(signal.signal_time),
        entry_time=str(signal.entry_time),
        strategy=STRATEGY_NAME,
        direction="BUY",
        status=status,
        skip_reason=skip_reason,
        entry_price=float(entry_price),
        stop_loss=float(stop_loss),
        take_profit=float(take_profit),
        atr=float(signal.atr_at_signal),
        units=float(units),
        risk_amount=float(actual_risk),
        target_risk_amount=float(target_risk),
        spread_estimate=float(spread_estimate),
        slippage_estimate=float(total_slippage_estimate),
    )


def maybe_close_position(
    row: pd.Series,
    timestamp: pd.Timestamp,
    bar_index: int,
    position: OpenShadowPosition,
    spec: InstrumentSpec,
    slippage_distance: float,
) -> ShadowTrade | None:
    open_price = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    intended_exit: float | None = None
    reason: str | None = None
    if open_price <= position.stop_loss:
        intended_exit = open_price
        reason = "stop_loss_gap"
    elif low <= position.stop_loss:
        intended_exit = position.stop_loss
        reason = "stop_loss"
    elif high >= position.take_profit:
        intended_exit = position.take_profit
        reason = "take_profit"
    if intended_exit is None or reason is None:
        return None
    actual_exit = intended_exit - slippage_distance
    gross = spec.pnl("BUY", position.entry_price, actual_exit, position.units)
    exit_slip_cost = slippage_distance * position.units * spec.ounces_per_unit
    net = gross - position.spread_estimate
    r_multiple = net / position.risk_amount if position.risk_amount > 0.0 else 0.0
    return ShadowTrade(
        signal_time=position.signal_time,
        entry_time=position.entry_time,
        exit_time=timestamp.isoformat(),
        strategy=STRATEGY_NAME,
        direction="BUY",
        entry_price=float(position.entry_price),
        stop_loss=float(position.stop_loss),
        take_profit=float(position.take_profit),
        units=float(position.units),
        risk_amount=float(position.risk_amount),
        spread_estimate=float(position.spread_estimate),
        entry_slippage_estimate=float(position.entry_slippage_estimate),
        exit_slippage_estimate=float(exit_slip_cost),
        exit_price=float(actual_exit),
        exit_reason=reason,
        gross_pnl=float(gross),
        net_pnl=float(net),
        r_multiple=float(r_multiple),
        holding_bars=int(bar_index - position.entry_bar),
    )


def update_signal_exit(signals: list[ShadowSignal], trade: ShadowTrade) -> list[ShadowSignal]:
    for signal in signals:
        if signal.signal_time == trade.signal_time:
            signal.exit_time = trade.exit_time
            signal.exit_reason = trade.exit_reason
            break
    return signals


def init_shadow_db(path: Path, settings: dict[str, Any]) -> None:
    path = ROOT / path if not path.is_absolute() else path
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS shadow_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shadow_signals (
                signal_time TEXT PRIMARY KEY,
                entry_time TEXT NOT NULL,
                strategy TEXT NOT NULL,
                direction TEXT NOT NULL,
                status TEXT NOT NULL,
                skip_reason TEXT,
                entry_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                atr REAL NOT NULL,
                units REAL NOT NULL,
                risk_amount REAL NOT NULL,
                target_risk_amount REAL NOT NULL,
                spread_estimate REAL NOT NULL,
                slippage_estimate REAL NOT NULL,
                exit_time TEXT,
                exit_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS shadow_trades (
                signal_time TEXT PRIMARY KEY,
                entry_time TEXT NOT NULL,
                exit_time TEXT NOT NULL,
                strategy TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                units REAL NOT NULL,
                risk_amount REAL NOT NULL,
                spread_estimate REAL NOT NULL,
                entry_slippage_estimate REAL NOT NULL,
                exit_slippage_estimate REAL NOT NULL,
                exit_price REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                gross_pnl REAL NOT NULL,
                net_pnl REAL NOT NULL,
                r_multiple REAL NOT NULL,
                holding_bars INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shadow_equity_curve (
                timestamp TEXT PRIMARY KEY,
                equity REAL NOT NULL,
                drawdown REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shadow_candles (
                timestamp TEXT PRIMARY KEY,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                signal_decision TEXT NOT NULL,
                notes TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shadow_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_time TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shadow_run_log (
                run_at TEXT PRIMARY KEY,
                strategy TEXT NOT NULL,
                signal_count INTEGER NOT NULL,
                trade_count INTEGER NOT NULL,
                skipped_count INTEGER NOT NULL,
                notes TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shadow_audit_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                record_type TEXT NOT NULL,
                record_key TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE(record_type, record_key, payload_hash)
            );
            """
        )
        config = shadow_config_payload(settings)
        conn.executemany(
            "INSERT OR REPLACE INTO shadow_config(key, value) VALUES (?, ?)",
            [(key, json.dumps(value, sort_keys=True)) for key, value in config.items()],
        )
        write_audit_snapshots(conn, "config", [{"key": key, "value": value} for key, value in config.items()], key_field="key")


def write_shadow_state(path: Path, state: ShadowState, settings: dict[str, Any], start_date: str | None) -> None:
    path = ROOT / path if not path.is_absolute() else path
    with sqlite3.connect(path) as conn:
        signal_payloads = [asdict(signal) for signal in state.signals]
        trade_payloads = [asdict(trade) for trade in state.trades]
        candle_payloads = [asdict(candle) for candle in state.candles]
        equity_payloads = [{"timestamp": ts, "equity": equity, "drawdown": drawdown} for ts, equity, drawdown in state.equity_curve]
        conn.executemany(
            """
            INSERT OR REPLACE INTO shadow_signals (
                signal_time, entry_time, strategy, direction, status, skip_reason,
                entry_price, stop_loss, take_profit, atr, units, risk_amount,
                target_risk_amount, spread_estimate, slippage_estimate, exit_time, exit_reason
            ) VALUES (
                :signal_time, :entry_time, :strategy, :direction, :status, :skip_reason,
                :entry_price, :stop_loss, :take_profit, :atr, :units, :risk_amount,
                :target_risk_amount, :spread_estimate, :slippage_estimate, :exit_time, :exit_reason
            )
            """,
            signal_payloads,
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO shadow_trades (
                signal_time, entry_time, exit_time, strategy, direction, entry_price,
                stop_loss, take_profit, units, risk_amount, spread_estimate,
                entry_slippage_estimate, exit_slippage_estimate, exit_price, exit_reason,
                gross_pnl, net_pnl, r_multiple, holding_bars
            ) VALUES (
                :signal_time, :entry_time, :exit_time, :strategy, :direction, :entry_price,
                :stop_loss, :take_profit, :units, :risk_amount, :spread_estimate,
                :entry_slippage_estimate, :exit_slippage_estimate, :exit_price, :exit_reason,
                :gross_pnl, :net_pnl, :r_multiple, :holding_bars
            )
            """,
            trade_payloads,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO shadow_equity_curve(timestamp, equity, drawdown) VALUES (?, ?, ?)",
            state.equity_curve,
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO shadow_candles(timestamp, open, high, low, close, volume, signal_decision, notes)
            VALUES (:timestamp, :open, :high, :low, :close, :volume, :signal_decision, :notes)
            """,
            candle_payloads,
        )
        run_payload = {
            "run_at": datetime.now(UTC).isoformat(),
            "strategy": STRATEGY_NAME,
            "signal_count": len(state.signals),
            "trade_count": len(state.trades),
            "skipped_count": sum(state.skipped.values()),
            "notes": {"start_date": start_date, "safety": shadow_config_payload(settings)},
        }
        conn.execute(
            """
            INSERT OR REPLACE INTO shadow_run_log(run_at, strategy, signal_count, trade_count, skipped_count, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_payload["run_at"],
                run_payload["strategy"],
                run_payload["signal_count"],
                run_payload["trade_count"],
                run_payload["skipped_count"],
                json.dumps(run_payload["notes"], sort_keys=True),
            ),
        )
        write_audit_snapshots(conn, "signal", signal_payloads, key_field="signal_time")
        write_audit_snapshots(conn, "trade", trade_payloads, key_field="signal_time")
        write_audit_snapshots(conn, "candle", candle_payloads, key_field="timestamp")
        write_audit_snapshots(conn, "equity", equity_payloads, key_field="timestamp")
        write_audit_snapshots(conn, "run", [run_payload], key_field="run_at")


def write_audit_snapshots(conn: sqlite3.Connection, record_type: str, records: list[dict[str, Any]], *, key_field: str) -> None:
    if not records:
        return
    observed_at = datetime.now(UTC).isoformat()
    rows: list[tuple[str, str, str, str, str]] = []
    for record in records:
        payload = json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
        rows.append((observed_at, record_type, str(record[key_field]), hashlib.sha256(payload.encode("utf-8")).hexdigest(), payload))
    conn.executemany(
        """
        INSERT OR IGNORE INTO shadow_audit_snapshots(observed_at, record_type, record_key, payload_hash, payload)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )


def record_event(path: Path, event_type: str, severity: str, message: str, details: dict[str, Any]) -> None:
    path = ROOT / path if not path.is_absolute() else path
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_time TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO shadow_events(event_time, event_type, severity, message, details)
            VALUES (?, ?, ?, ?, ?)
            """,
            (datetime.now(UTC).isoformat(), event_type, severity, message, json.dumps(details, sort_keys=True, default=str)),
        )


def status_report(shadow_db: Path, *, as_of: pd.Timestamp | str | None = None) -> dict[str, Any]:
    shadow_db = ROOT / shadow_db if not shadow_db.is_absolute() else shadow_db
    as_of_ts = utc_timestamp(as_of) if as_of is not None else pd.Timestamp.now(tz="UTC")
    if not shadow_db.exists():
        return {
            "status": "not_initialized",
            "strategy": STRATEGY_NAME,
            "paper_readiness": "failed",
            "live_readiness": "failed",
            "shadow_db": str(shadow_db),
            "runtime_environment": runtime_environment_status(),
        }
    with sqlite3.connect(shadow_db) as conn:
        def scalar(query: str, default: Any = None, params: tuple[Any, ...] = ()) -> Any:
            row = conn.execute(query, params).fetchone()
            return row[0] if row and row[0] is not None else default

        latest_event = conn.execute(
            "SELECT event_time, event_type, severity, message FROM shadow_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        latest_run = conn.execute("SELECT run_at, signal_count, trade_count, skipped_count FROM shadow_run_log ORDER BY run_at DESC LIMIT 1").fetchone()
        since_24h = (as_of_ts - pd.Timedelta(hours=24)).isoformat()
        latest_candle = scalar("SELECT MAX(timestamp) FROM shadow_candles")
        stale = stale_data_report(latest_candle, as_of_ts)
        error_count = int(scalar("SELECT COUNT(*) FROM shadow_events WHERE severity='ERROR' AND event_time >= ?", 0, (since_24h,)))
        status = "ok"
        if stale["is_stale"] or error_count > 0:
            status = "unhealthy"
        return {
            "status": status,
            "strategy": STRATEGY_NAME,
            "classification": "research-only",
            "paper_readiness": "failed",
            "live_readiness": "failed",
            "shadow_db": str(shadow_db),
            "latest_candle": latest_candle,
            "latest_equity": scalar("SELECT equity FROM shadow_equity_curve ORDER BY timestamp DESC LIMIT 1", 0.0),
            "latest_drawdown": scalar("SELECT drawdown FROM shadow_equity_curve ORDER BY timestamp DESC LIMIT 1", 0.0),
            "signal_count": int(scalar("SELECT COUNT(*) FROM shadow_signals", 0)),
            "skipped_count": int(scalar("SELECT COUNT(*) FROM shadow_signals WHERE status='skipped'", 0)),
            "trade_count": int(scalar("SELECT COUNT(*) FROM shadow_trades", 0)),
            "event_count": int(scalar("SELECT COUNT(*) FROM shadow_events", 0)),
            "audit_snapshot_count": int(safe_table_count(conn, "shadow_audit_snapshots")),
            "errors_24h": error_count,
            "stale_data": stale,
            "data_gaps": data_gap_report(conn),
            "runtime_environment": runtime_environment_status(),
            "latest_event": tuple(latest_event) if latest_event else None,
            "latest_run": tuple(latest_run) if latest_run else None,
        }


def data_gap_report(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT timestamp FROM shadow_candles ORDER BY timestamp").fetchall()
    if len(rows) < 2:
        return {"count": 0, "max_gap_minutes": 0.0, "latest_gap": None}
    times = [pd.Timestamp(row[0]) for row in rows]
    gaps = [(b - a).total_seconds() / 60.0 for a, b in zip(times, times[1:])]
    large = [gap for gap in gaps if gap > 30.0]
    return {
        "count": len(large),
        "max_gap_minutes": max(gaps) if gaps else 0.0,
        "latest_gap": large[-1] if large else None,
    }


def safe_table_count(conn: sqlite3.Connection, table_name: str) -> int:
    exists = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    if not exists or int(exists[0]) == 0:
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def stale_data_report(latest_candle: str | None, as_of_ts: pd.Timestamp, *, threshold_minutes: float = STALE_CANDLE_MINUTES) -> dict[str, Any]:
    if latest_candle is None:
        return {
            "is_stale": True,
            "latest_candle": None,
            "age_minutes": None,
            "threshold_minutes": threshold_minutes,
            "market_pause": False,
            "reason": "no_candles_logged",
        }
    latest_ts = utc_timestamp(latest_candle)
    age_minutes = max(0.0, (as_of_ts - latest_ts).total_seconds() / 60.0)
    market_pause = is_weekend_market_pause(as_of_ts)
    is_stale = bool(age_minutes > threshold_minutes and not market_pause)
    return {
        "is_stale": is_stale,
        "latest_candle": latest_ts.isoformat(),
        "age_minutes": age_minutes,
        "threshold_minutes": threshold_minutes,
        "market_pause": market_pause,
        "reason": "stale_candle" if is_stale else "ok",
    }


def is_weekend_market_pause(ts: pd.Timestamp) -> bool:
    timestamp = utc_timestamp(ts)
    weekday = timestamp.weekday()
    hour = timestamp.hour
    if weekday == 5 or weekday == 6:
        return True
    if weekday == 4 and hour >= 22:
        return True
    if weekday == 0 and hour < 1:
        return True
    return False


def print_status(report: dict[str, Any]) -> None:
    print("AURUM-1 Forward Shadow Status")
    print("=" * 72)
    print(f"Status:          {report.get('status')}")
    print(f"Strategy:        {report.get('strategy')}")
    print(f"Latest candle:   {report.get('latest_candle')}")
    print(f"Latest equity:   {float(report.get('latest_equity') or 0.0):.2f}")
    print(f"Latest DD:       {float(report.get('latest_drawdown') or 0.0):.2%}")
    print(f"Signals:         {report.get('signal_count')}")
    print(f"Trades:          {report.get('trade_count')}")
    print(f"Skipped:         {report.get('skipped_count')}")
    print(f"Errors 24h:      {report.get('errors_24h')}")
    print(f"Stale data:      {report.get('stale_data')}")
    print(f"Data gaps:       {report.get('data_gaps')}")
    print(f"Runtime env:     {report.get('runtime_environment')}")
    print(f"Latest event:    {report.get('latest_event')}")
    print("Final verdict:   research-only until forward evidence is collected")


def weekly_report(shadow_db: Path, report_dir: Path, as_of: str | None) -> dict[str, Any]:
    shadow_db = ROOT / shadow_db if not shadow_db.is_absolute() else shadow_db
    report_dir = ROOT / report_dir if not report_dir.is_absolute() else report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    as_of_ts = utc_timestamp(as_of) if as_of else pd.Timestamp.now(tz="UTC")
    start_ts = as_of_ts - pd.Timedelta(days=7)
    with sqlite3.connect(shadow_db) as conn:
        trades = pd.read_sql_query("SELECT * FROM shadow_trades", conn)
        signals = pd.read_sql_query("SELECT * FROM shadow_signals", conn)
        equity = pd.read_sql_query("SELECT * FROM shadow_equity_curve", conn)
        events = pd.read_sql_query("SELECT * FROM shadow_events", conn)
        candles = pd.read_sql_query("SELECT * FROM shadow_candles", conn)
    if not trades.empty:
        trades["exit_time"] = pd.to_datetime(trades["exit_time"], utc=True)
        trades = trades[(trades["exit_time"] >= start_ts) & (trades["exit_time"] <= as_of_ts)].copy()
    if not signals.empty:
        signals["signal_time"] = pd.to_datetime(signals["signal_time"], utc=True)
        signals = signals[(signals["signal_time"] >= start_ts) & (signals["signal_time"] <= as_of_ts)].copy()
    if not equity.empty:
        equity["timestamp"] = pd.to_datetime(equity["timestamp"], utc=True)
        equity = equity[(equity["timestamp"] >= start_ts) & (equity["timestamp"] <= as_of_ts)].copy()
    if not events.empty:
        events["event_time"] = pd.to_datetime(events["event_time"], utc=True)
        events = events[(events["event_time"] >= start_ts) & (events["event_time"] <= as_of_ts)].copy()
    if not candles.empty:
        candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True)
        candles = candles[(candles["timestamp"] >= start_ts) & (candles["timestamp"] <= as_of_ts)].copy()
    report = build_weekly_report(trades, signals, equity, events, candles, start_ts, as_of_ts)
    report["health"] = status_report(shadow_db, as_of=as_of_ts)
    stamp = as_of_ts.strftime("%Y%m%d_%H%M%S")
    output = report_dir / f"donchian_shadow_weekly_{stamp}.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report["report_path"] = str(output)
    return report


def build_weekly_report(
    trades: pd.DataFrame,
    signals: pd.DataFrame,
    equity: pd.DataFrame,
    events: pd.DataFrame,
    candles: pd.DataFrame,
    start_ts: pd.Timestamp,
    as_of_ts: pd.Timestamp,
) -> dict[str, Any]:
    pnl = trades["net_pnl"].astype(float).tolist() if not trades.empty else []
    gross_pnl = float(trades["gross_pnl"].astype(float).sum()) if not trades.empty else 0.0
    wins = [value for value in pnl if value > 0.0]
    losses = [value for value in pnl if value <= 0.0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    r_values = trades["r_multiple"].astype(float).tolist() if not trades.empty else []
    equity_values = equity["equity"].astype(float).tolist() if not equity.empty else []
    sharpe = estimate_sharpe(equity) if not equity.empty else 0.0
    max_dd = abs(float(equity["drawdown"].min())) if not equity.empty else 0.0
    skipped_count = int((signals["status"] == "skipped").sum()) if not signals.empty else 0
    best_trade = row_to_dict(trades.loc[trades["net_pnl"].idxmax()]) if not trades.empty else None
    worst_trade = row_to_dict(trades.loc[trades["net_pnl"].idxmin()]) if not trades.empty else None
    event_types = dict(Counter(events["event_type"].astype(str).tolist())) if not events.empty else {}
    errors = events[events["severity"].astype(str).eq("ERROR")] if not events.empty else pd.DataFrame()
    return {
        "strategy": STRATEGY_NAME,
        "classification": "research-only",
        "paper_readiness": "failed",
        "live_readiness": "failed",
        "period_start": start_ts.isoformat(),
        "period_end": as_of_ts.isoformat(),
        "gross_pnl": gross_pnl,
        "net_pnl": float(sum(pnl)),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else (10.0 if gross_profit > 0.0 else 0.0),
        "sharpe_estimate": float(sharpe),
        "max_drawdown": float(max_dd),
        "trade_count": int(len(pnl)),
        "win_rate": len(wins) / len(pnl) if pnl else 0.0,
        "average_r": float(np.mean(r_values)) if r_values else 0.0,
        "median_r": float(np.median(r_values)) if r_values else 0.0,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "skipped_signals": skipped_count,
        "execution_logging_issues": errors["message"].astype(str).tolist() if not errors.empty else [],
        "runtime_errors": int((events["event_type"].astype(str) == "runtime_error").sum()) if not events.empty else 0,
        "api_failures": int((events["event_type"].astype(str) == "data_error").sum()) if not events.empty else 0,
        "event_counts": event_types,
        "heartbeat_events": int((events["event_type"].astype(str) == "heartbeat").sum()) if not events.empty else 0,
        "uptime_downtime": uptime_downtime(events, start_ts, as_of_ts),
        "data_gaps": candle_gap_report(candles),
        "historical_expectations": {
            "historical_pf": 1.15,
            "historical_sharpe": 0.91,
            "historical_max_drawdown": 0.0779,
            "historical_trade_rate_per_week": 9.8,
            "historical_win_rate": 0.378,
        },
        "forward_shadow_failure_criteria": [
            "execution/logging bugs",
            "strategy takes trades different from intended raw Donchian fixed-2R rules",
            "drawdown exceeds 10-15%",
            "PF collapses below 1.0",
            "trade count wildly differs from historical rate",
            "repeated data/feed issues",
        ],
        "forward_shadow_success_criteria_after_3_months": [
            "net P&L positive or near-flat with acceptable drawdown",
            "PF >= 1.10",
            "Sharpe not catastrophically worse than backtest",
            "max drawdown <= 10%",
            "no execution realism issues",
            "trade count broadly consistent with historical expectation",
            "no manual intervention needed",
        ],
    }


def estimate_sharpe(equity: pd.DataFrame) -> float:
    if equity.empty or "timestamp" not in equity or "equity" not in equity:
        return 0.0
    series = equity.set_index("timestamp")["equity"].astype(float).sort_index()
    daily = series.resample("1D").last().dropna()
    returns = daily.pct_change().dropna()
    if len(returns) < 2 or float(returns.std(ddof=1)) == 0.0:
        return 0.0
    return float((returns.mean() / returns.std(ddof=1)) * math.sqrt(252.0))


def candle_gap_report(candles: pd.DataFrame) -> dict[str, Any]:
    if candles.empty or len(candles) < 2:
        return {"count": 0, "max_gap_minutes": 0.0}
    times = candles["timestamp"].sort_values().tolist()
    gaps = [(b - a).total_seconds() / 60.0 for a, b in zip(times, times[1:])]
    large = [gap for gap in gaps if gap > 30.0]
    return {"count": len(large), "max_gap_minutes": max(gaps) if gaps else 0.0}


def uptime_downtime(events: pd.DataFrame, start_ts: pd.Timestamp, as_of_ts: pd.Timestamp) -> dict[str, Any]:
    period_seconds = max(0.0, (as_of_ts - start_ts).total_seconds())
    if events.empty:
        return {"heartbeat_count": 0, "estimated_uptime_seconds": 0.0, "estimated_downtime_seconds": period_seconds, "heartbeat_gap_count": 0}
    heartbeats = [utc_timestamp(value) for value in events[events["event_type"].astype(str).eq("heartbeat")]["event_time"].sort_values().tolist()]
    if not heartbeats:
        return {"heartbeat_count": 0, "estimated_uptime_seconds": 0.0, "estimated_downtime_seconds": period_seconds, "heartbeat_gap_count": 0}
    checkpoints = [start_ts, *heartbeats, as_of_ts]
    max_allowed_gap = EXPECTED_HEARTBEAT_SECONDS * 2.5
    downtime = 0.0
    gap_count = 0
    for earlier, later in zip(checkpoints, checkpoints[1:]):
        gap = max(0.0, (later - earlier).total_seconds())
        if gap > max_allowed_gap:
            downtime += gap - EXPECTED_HEARTBEAT_SECONDS
            gap_count += 1
    downtime = min(period_seconds, max(0.0, downtime))
    return {
        "heartbeat_count": len(heartbeats),
        "expected_heartbeat_seconds": EXPECTED_HEARTBEAT_SECONDS,
        "heartbeat_gap_count": gap_count,
        "estimated_uptime_seconds": max(0.0, period_seconds - downtime),
        "estimated_downtime_seconds": downtime,
    }


def print_run_summary(state: ShadowState, shadow_db: Path) -> None:
    print("AURUM-1 Forward Shadow Update")
    print("=" * 72)
    print(f"Strategy:              {STRATEGY_NAME}")
    print("Classification:        research-only")
    print("Paper readiness:       failed")
    print("Live readiness:        failed")
    print("OANDA orders sent:     no")
    print(f"Signals logged:        {len(state.signals)}")
    print(f"Trades closed:         {len(state.trades)}")
    print(f"Skipped signals:       {sum(state.skipped.values())}")
    print(f"Equity points:         {len(state.equity_curve)}")
    print(f"Shadow DB:             {shadow_db}")


def print_weekly_report(report: dict[str, Any]) -> None:
    print("AURUM-1 Forward Shadow Weekly Report")
    print("=" * 72)
    print(f"Strategy:        {report['strategy']}")
    print(f"Period:          {report['period_start']} -> {report['period_end']}")
    print(f"Gross P&L:       {report['gross_pnl']:.2f}")
    print(f"Net P&L:         {report['net_pnl']:.2f}")
    print(f"PF:              {report['profit_factor']:.2f}")
    print(f"Sharpe estimate: {report['sharpe_estimate']:.2f}")
    print(f"Max drawdown:    {report['max_drawdown']:.2%}")
    print(f"Trades:          {report['trade_count']}")
    print(f"Win rate:        {report['win_rate']:.2%}")
    print(f"Average R:       {report['average_r']:.3f}")
    print(f"Median R:        {report['median_r']:.3f}")
    print(f"Skipped signals: {report['skipped_signals']}")
    print(f"API failures:    {report['api_failures']}")
    print(f"Runtime errors:  {report['runtime_errors']}")
    print(f"Data gaps:       {report['data_gaps']}")
    print(f"Health:          {report.get('health', {}).get('status')}")
    print(f"Report:          {report['report_path']}")
    print("Final verdict:   research-only until forward evidence is collected")


def shadow_config_payload(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy": STRATEGY_NAME,
        "paper_trade": True,
        "allow_oanda_orders": False,
        "allow_live_trading": False,
        "risk_per_trade_pct": RISK_PER_TRADE_PCT,
        "duration_months_min": MIN_DURATION_MONTHS,
        "lookback": LOOKBACK,
        "exit_mode": "FIXED",
        "direction": "BUY_ONLY",
        "parameter_freeze": True,
        "market_data_db_path": str(resolve_market_db(settings, None)),
        "paper_initial_equity": float(settings.get("broker", {}).get("paper_initial_equity", 10000.0)),
    }


def resolve_market_db(settings: dict[str, Any], market_db_arg: Path | None) -> Path:
    raw = market_db_arg
    if raw is None:
        configured = settings.get("forward_shadow", {}).get("market_data_db_path")
        raw = Path(str(configured)) if configured else DEFAULT_MARKET_DB
    return ROOT / raw if not raw.is_absolute() else raw


def safe_load_ohlcv(timeframe: str, market_db: Path) -> pd.DataFrame:
    try:
        return load_ohlcv(timeframe, market_db)
    except Exception as exc:
        raise RuntimeError(f"Failed to load {timeframe} market cache {market_db}: {exc}") from exc


def validate_raw_fetch(frame: pd.DataFrame) -> None:
    required = {"timestamp", "open", "high", "low", "close", "source", "instrument"}
    missing = required.difference(frame.columns)
    if missing:
        raise RuntimeError(f"OANDA fetch missing columns: {sorted(missing)}")
    if set(frame["source"].astype(str).str.lower()) != {"oanda"}:
        raise RuntimeError("Forward shadow accepts real OANDA candles only")
    if set(frame["instrument"].astype(str)) != {"XAU_USD"}:
        raise RuntimeError("Forward shadow accepts XAU_USD candles only")


def validate_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise RuntimeError(f"Market cache missing OHLCV columns: {sorted(missing)}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise RuntimeError("Market cache must use a DatetimeIndex")
    if frame.index.tz is None:
        raise RuntimeError("Market cache timestamps must be timezone-aware UTC")
    cleaned = frame[~frame.index.duplicated(keep="last")].copy()
    for column in required:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="raise")
    bad = cleaned[(cleaned["high"] < cleaned[["open", "close", "low"]].max(axis=1)) | (cleaned["low"] > cleaned[["open", "close", "high"]].min(axis=1))]
    if not bad.empty:
        raise RuntimeError(f"Malformed OHLCV rows detected: {len(bad)}")
    return cleaned.sort_index()


def parse_start_date(value: str | None, settings: dict[str, Any]) -> pd.Timestamp:
    configured = settings.get("forward_shadow", {}).get("start_date")
    raw = value or configured or datetime.now(UTC).date().isoformat()
    ts = pd.Timestamp(raw)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def row_to_dict(row: pd.Series) -> dict[str, Any]:
    return {key: (float(value) if isinstance(value, (np.floating, float)) else value) for key, value in row.to_dict().items()}


def utc_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def runtime_environment_status() -> dict[str, Any]:
    in_venv = bool(os.getenv("VIRTUAL_ENV")) or sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    return {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "required_python": f"{REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}",
        "python_ok": sys.version_info[:2] == REQUIRED_PYTHON,
        "in_virtualenv": in_venv,
        "virtualenv": os.getenv("VIRTUAL_ENV"),
        "executable": sys.executable,
    }


def enforce_runtime_environment(command: str) -> None:
    status = runtime_environment_status()
    if not status["python_ok"]:
        raise RuntimeError(f"Forward shadow requires Python {status['required_python']}")
    if command == "service" and not status["in_virtualenv"] and not _truthy_env("AURUM1_ALLOW_SYSTEM_PYTHON"):
        raise RuntimeError("Forward shadow service must run from .venv; set AURUM1_ALLOW_SYSTEM_PYTHON=true only for controlled diagnostics")
    if not status["in_virtualenv"]:
        LOGGER.warning("runtime_environment_not_virtualenv executable=%s", status["executable"])


def setup_logging(log_file: Path) -> None:
    path = ROOT / log_file if not log_file.is_absolute() else log_file
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    LOGGER.setLevel(logging.INFO)
    if not LOGGER.handlers:
        LOGGER.addHandler(handler)
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        LOGGER.addHandler(stream)


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


if __name__ == "__main__":
    raise SystemExit(main())
