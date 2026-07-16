"""D2 forward shadow — Donchian signals, fixed 1R exit + session/vol filters.

Runs alongside the existing raw_donchian_fixed_2r shadow to compare:
  D2: Donchian signals + 1R exit + filter(high_vol, london_session)
  Raw: Donchian signals + 2R exit + no filters

Safe: never submits OANDA orders, never enables SELL.
"""

from __future__ import annotations

import argparse
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

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.donchian_research_runner import donchian_signals
from scripts.research_edge_prototypes import build_research_features

STRATEGY_NAME = "donchian_d2_1r_filtered"
LOOKBACK = 20
RISK_PER_TRADE_PCT = 0.0025
DEFAULT_D2_DB = ROOT / "reports" / "forward_shadow" / "donchian_d2_shadow.sqlite3"
DEFAULT_REPORT_DIR = ROOT / "reports" / "forward_shadow"
DEFAULT_LOG_FILE = ROOT / "logs" / "forward_shadow_donchian_d2.log"
DEFAULT_MARKET_DB = ROOT / "aurum1" / "data" / "forward_shadow_market_cache.sqlite3"
LOGGER = logging.getLogger("aurum1.forward_shadow_d2")


@dataclass
class D2Signal:
    raw_signal_id: str
    timestamp: str
    direction: str
    entry_price: float
    stop_loss: float
    target_1r: float
    risk_distance: float
    volatility_regime: str
    session: str
    weekday: str
    d2_decision: str
    blocked_reason: str | None
    atr: float
    units: float


@dataclass
class D2Trade:
    signal_id: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    exit_reason: str
    r_multiple: float
    net_pnl: float
    holding_bars: int
    volatility_regime: str
    session: str
    weekday: str


def classify_volatility(atr_value: float, high_threshold: float | None = None) -> str:
    if high_threshold is None or atr_value >= high_threshold:
        return "high"
    return "normal"


def classify_session(ts: pd.Timestamp) -> str:
    hour = int(ts.hour)
    if 0 <= hour < 7:
        return "asia"
    if 7 <= hour < 12:
        return "london"
    if 12 <= hour < 16:
        return "london_ny_overlap"
    if 16 <= hour < 21:
        return "new_york"
    return "rollover"


def d2_should_take(direction: str, vol: str, session: str) -> tuple[bool, str | None]:
    if direction != "BUY":
        return False, "short_side_disabled"
    if vol == "high":
        return False, "high_volatility"
    if session == "london":
        return False, "london_session"
    return True, None


def simulate_d2(
    ohlcv: pd.DataFrame,
    features: pd.DataFrame,
    settings: dict[str, Any],
    signals: list[Any] | None = None,
) -> dict[str, Any]:
    if signals is None:
        signals = donchian_signals(ohlcv, features, lookback=LOOKBACK, htf_filter=False)
    spec = InstrumentSpec.from_settings(settings)
    initial_equity = float(settings.get("broker", {}).get("paper_initial_equity", 10000.0))
    spread_pips = float(settings.get("execution", {}).get("paper_spread_pips", 1.5))
    slippage_pips = float(settings.get("execution", {}).get("slippage_std_pips", 0.5))
    slippage_distance = slippage_pips * spec.pip_size

    atr_values = features["atr_14"].dropna()
    high_threshold = float(atr_values.quantile(0.66)) if len(atr_values) >= 10 else None

    signals_by_entry: dict[int, list[Any]] = {}
    for signal in signals:
        signals_by_entry.setdefault(int(signal.entry_bar), []).append(signal)

    equity = initial_equity
    peak = initial_equity
    position: dict[str, Any] | None = None
    d2_signals: list[D2Signal] = []
    d2_trades: list[D2Trade] = []
    equity_curve: list[tuple[str, float, float]] = []
    skipped_count = 0
    take_count = 0

    for bar_index, (timestamp, row) in enumerate(ohlcv.iterrows()):
        ts = pd.Timestamp(timestamp)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")

        if position is not None and bar_index > position["entry_bar"]:
            open_p, high, low = float(row["open"]), float(row["high"]), float(row["low"])
            entry = position["entry_price"]
            stop = position["stop_loss"]
            target = position["target_1r"]
            exit_p = None
            reason = None
            if open_p <= stop:
                exit_p, reason = open_p, "stop_loss_gap"
            elif low <= stop:
                exit_p, reason = stop, "stop_loss"
            elif high >= target:
                exit_p, reason = target, "take_profit"
            if exit_p is not None and reason is not None:
                actual_exit = exit_p - slippage_distance
                gross = spec.pnl("BUY", entry, actual_exit, position["units"])
                exit_slip = slippage_distance * position["units"] * spec.ounces_per_unit
                net = gross - position["spread_estimate"]
                r = net / position["risk_amount"] if position["risk_amount"] > 0 else 0.0
                d2_trades.append(D2Trade(
                    signal_id=position["signal_id"], entry_time=position["entry_time"],
                    exit_time=ts.isoformat(), entry_price=entry, exit_price=actual_exit,
                    exit_reason=reason, r_multiple=r, net_pnl=net,
                    holding_bars=bar_index - position["entry_bar"],
                    volatility_regime=position["vol_regime"], session=position["session"],
                    weekday=position["weekday"]))
                equity += net
                position = None

        for signal in signals_by_entry.get(bar_index, []):
            signal_time = pd.Timestamp(signal.signal_time)
            if signal_time.tzinfo is None:
                signal_time = signal_time.tz_localize("UTC")
            vol = classify_volatility(signal.atr_at_signal, high_threshold)
            sess = classify_session(signal_time)
            wd = signal_time.day_name()
            should_take, block_reason = d2_should_take("BUY", vol, sess)

            if should_take and position is not None:
                skipped_count += 1
                d2_signals.append(D2Signal(raw_signal_id=signal_time.isoformat(),
                    timestamp=signal_time.isoformat(), direction="BUY",
                    entry_price=signal.entry_price, stop_loss=signal.stop_loss,
                    target_1r=signal.entry_price + abs(signal.entry_price - signal.stop_loss),
                    risk_distance=abs(signal.entry_price - signal.stop_loss),
                    volatility_regime=vol, session=sess, weekday=wd,
                    d2_decision="SKIP_OPEN_POSITION", blocked_reason="open_position",
                    atr=signal.atr_at_signal, units=0.0))
                continue

            if not should_take:
                skipped_count += 1
                d2_signals.append(D2Signal(raw_signal_id=signal_time.isoformat(),
                    timestamp=signal_time.isoformat(), direction="BUY",
                    entry_price=signal.entry_price, stop_loss=signal.stop_loss,
                    target_1r=signal.entry_price + abs(signal.entry_price - signal.stop_loss),
                    risk_distance=abs(signal.entry_price - signal.stop_loss),
                    volatility_regime=vol, session=sess, weekday=wd,
                    d2_decision="HOLD", blocked_reason=block_reason,
                    atr=signal.atr_at_signal, units=0.0))
                continue

            take_count += 1
            # Bug fix: keep risk distance (2*ATR) invariant under slippage
            original_risk_distance = abs(signal.entry_price - signal.stop_loss)
            adjusted_entry = signal.entry_price + slippage_distance
            adjusted_stop = adjusted_entry - original_risk_distance
            target_1r = adjusted_entry + original_risk_distance
            target_risk = equity * RISK_PER_TRADE_PCT
            raw_units = target_risk / (original_risk_distance * spec.ounces_per_unit) if original_risk_distance > 0 else spec.min_units
            units = spec.lots_to_units(spec.round_lots(spec.units_to_lots(raw_units)))
            actual_risk = original_risk_distance * units * spec.ounces_per_unit
            spread_est = 2.0 * spread_pips * spec.pip_value_per_unit * units

            d2_signals.append(D2Signal(raw_signal_id=signal_time.isoformat(),
                timestamp=signal_time.isoformat(), direction="BUY",
                entry_price=adjusted_entry, stop_loss=adjusted_stop,
                target_1r=target_1r, risk_distance=original_risk_distance,
                volatility_regime=vol, session=sess, weekday=wd,
                d2_decision="TAKE", blocked_reason=None,
                atr=signal.atr_at_signal, units=units))
            position = {"signal_id": signal_time.isoformat(), "entry_time": signal.entry_time,
                "entry_bar": bar_index, "entry_price": adjusted_entry, "stop_loss": adjusted_stop,
                "target_1r": target_1r, "units": units, "risk_amount": actual_risk,
                "spread_estimate": spread_est, "vol_regime": vol, "session": sess, "weekday": wd}

        peak = max(peak, equity)
        drawdown = (equity - peak) / peak if peak > 0 else 0.0
        equity_curve.append((ts.isoformat(), equity, drawdown))

    if position is not None and len(ohlcv) > 0:
        last_row = ohlcv.iloc[-1]
        close_price = float(last_row["close"])
        gross = spec.pnl("BUY", position["entry_price"], close_price, position["units"])
        net = gross - position["spread_estimate"]
        r = net / position["risk_amount"] if position["risk_amount"] > 0 else 0.0
        d2_trades.append(D2Trade(signal_id=position["signal_id"],
            entry_time=position["entry_time"], exit_time=ohlcv.index[-1].isoformat(),
            entry_price=position["entry_price"], exit_price=close_price,
            exit_reason="end_of_data", r_multiple=r, net_pnl=net,
            holding_bars=len(ohlcv) - position["entry_bar"],
            volatility_regime=position["vol_regime"], session=position["session"],
            weekday=position["weekday"]))
        equity += net

    return {"equity_curve": equity_curve, "signals": d2_signals, "trades": d2_trades,
        "final_equity": equity, "total_signals": len(d2_signals),
        "take_count": take_count, "skipped_count": skipped_count, "trade_count": len(d2_trades)}


def compute_metrics(result: dict[str, Any]) -> dict[str, Any]:
    trades = result["trades"]
    r_vals = [t.r_multiple for t in trades]
    pnls = [t.net_pnl for t in trades]
    if not trades:
        return {"trade_count": 0, "final_equity": result["final_equity"]}
    wins = sum(1 for r in r_vals if r > 0)
    losses = sum(1 for r in r_vals if r < 0)
    gain = sum(abs(r) for r in r_vals if r > 0)
    loss = sum(abs(r) for r in r_vals if r < 0)
    pf = gain / loss if loss > 0 else float("inf") if gain > 0 else 0.0
    return {"trade_count": len(trades), "win_count": wins, "loss_count": losses,
        "win_rate": wins / len(trades) if trades else 0,
        "profit_factor": pf, "total_r": sum(r_vals),
        "avg_r": sum(r_vals) / len(r_vals) if r_vals else 0,
        "total_pnl": sum(pnls), "final_equity": result["final_equity"],
        "take_count": result["take_count"], "skipped_count": result["skipped_count"]}


def run_once(settings: dict[str, Any], market_db: Path) -> dict[str, Any]:
    ohlcv = load_ohlcv("M15", market_db)
    if ohlcv.empty:
        raise RuntimeError("No M15 data available")
    features = build_research_features(ohlcv)
    # Filter to last 18 months for fair comparison
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=548)
    ohlcv = ohlcv[ohlcv.index >= cutoff].copy()
    features = features.loc[ohlcv.index].copy()
    result = simulate_d2(ohlcv, features, settings)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D2 forward shadow runner.")
    parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args(argv)
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    result = run_once(settings, args.market_db)
    metrics = compute_metrics(result)

    if args.json:
        import datetime as dt
        serializable = dict(metrics)
        serializable["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        serializable["strategy"] = STRATEGY_NAME
        print(json.dumps(serializable, indent=2, sort_keys=True, default=str))
        return 0

    print(f"AURUM-1 D2 Shadow ({STRATEGY_NAME})")
    print("=" * 60)
    print(f"Last 18 months of data")
    print(f"\nSignals: {metrics['take_count'] + metrics['skipped_count']}")
    print(f"  TAKE (entered): {metrics['take_count']}")
    print(f"  HOLD (filtered): {metrics['skipped_count']}")
    print(f"  Closed trades: {metrics['trade_count']}")
    print(f"\nPerformance:")
    print(f"  Win Rate: {metrics['win_count']}/{metrics['trade_count']} = {metrics['win_rate']*100:.1f}%")
    print(f"  Profit Factor: {metrics['profit_factor']:.4f}")
    print(f"  Total R: {metrics['total_r']:.2f}")
    print(f"  Avg R: {metrics['avg_r']:.4f}")
    print(f"  Total PnL: ${metrics['total_pnl']:.2f}")
    print(f"  Final Equity: ${metrics['final_equity']:.2f}")

    exits = Counter(t.exit_reason for t in result["trades"])
    print(f"\nExit breakdown:")
    for reason, count in exits.most_common():
        print(f"  {reason}: {count}")

    sessions = Counter(t.session for t in result["trades"])
    print(f"\nBy session:")
    for sess, count in sessions.most_common():
        st = [t for t in result["trades"] if t.session == sess]
        sw = sum(1 for t in st if t.r_multiple > 0)
        sr = sum(t.r_multiple for t in st)
        print(f"  {sess}: {count} trades, {sw}W/{count-sw}L, R={sr:.2f}")

    # Compare with raw Donchian 2R
    shadow_db = ROOT / "reports" / "forward_shadow" / "donchian_shadow.sqlite3"
    if shadow_db.exists():
        conn = sqlite3.connect(shadow_db)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(r_multiple),0), COALESCE(SUM(net_pnl),0) FROM shadow_trades")
        raw_cnt, raw_r, raw_pnl = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM shadow_trades WHERE r_multiple > 0")
        raw_wins = cur.fetchone()[0]
        conn.close()
        raw_wr = raw_wins / raw_cnt * 100 if raw_cnt else 0
        print(f"\n{'─'*60}")
        print(f"{'':<30} {'D2 (1R+filter)':<18} {'Raw (2R)':<18}")
        print(f"{'─'*30} {'─'*18} {'─'*18}")
        print(f"{'Trades':<30} {metrics['trade_count']:<18} {raw_cnt:<18}")
        print(f"{'Win Rate':<30} {metrics['win_rate']*100:.1f}%{'':<14} {raw_wr:.1f}%{'':<14}")
        print(f"{'Total R':<30} {metrics['total_r']:<+18.2f} {raw_r:<+18.2f}")
        print(f"{'Total PnL':<30} ${metrics['total_pnl']:<+17.2f} ${raw_pnl:<+17.2f}")
        # Compute raw PF from the shadow database (not from D2 trades)
        conn2 = sqlite3.connect(shadow_db)
        cur2 = conn2.cursor()
        cur2.execute("SELECT COALESCE(SUM(CASE WHEN r_multiple>0 THEN r_multiple ELSE 0 END), 0), COALESCE(SUM(CASE WHEN r_multiple<0 THEN ABS(r_multiple) ELSE 0 END), 0) FROM shadow_trades")
        raw_gain_r, raw_loss_r = cur2.fetchone()
        conn2.close()
        raw_pf = raw_gain_r / raw_loss_r if raw_loss_r > 0 else (10.0 if raw_gain_r > 0 else 0.0)
        print(f"{'PF':<30} {metrics['profit_factor']:<.4f}{'':<13} {raw_pf:<.4f}{'':<18}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
