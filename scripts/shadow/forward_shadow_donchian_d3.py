"""D3 forward shadow — Donchian signals, BUY+SELL, 1R exit, vol/session filter.

Runs alongside D2 (BUY-only) to compare:
  D3: Donchian + BUY+SELL + 1R exit + filter(high_vol, london_session)
  D2: Donchian + BUY-only + 1R exit + filter(high_vol, london_session)
  Raw: Donchian + BUY-only + 2R exit + no filters

Safe: never submits OANDA orders.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
from scripts.research_edge_prototypes import build_research_features

STRATEGY_NAME = "donchian_d3_buy_sell_1r_filtered"
LOOKBACK = 20
RISK_PER_TRADE_PCT = 0.0025
DEFAULT_MARKET_DB = ROOT / "aurum1" / "data" / "forward_shadow_market_cache.sqlite3"


@dataclass
class D3Trade:
    direction: str
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


def classify_vol(atr: float, threshold: float | None) -> str:
    return "high" if threshold is None or atr >= threshold else "normal"


def classify_session(ts: pd.Timestamp) -> str:
    h = int(ts.hour)
    if 0 <= h < 7: return "asia"
    if 7 <= h < 12: return "london"
    if 12 <= h < 16: return "london_ny_overlap"
    if 16 <= h < 21: return "new_york"
    return "rollover"


def should_take(direction: str, vol: str, session: str) -> tuple[bool, str | None]:
    if vol == "high":
        return False, "high_volatility"
    if session == "london":
        return False, "london_session"
    return True, None


def run_d3(ohlcv: pd.DataFrame, features: pd.DataFrame, settings: dict[str, Any]) -> dict[str, Any]:
    spec = InstrumentSpec.from_settings(settings)
    spread_pips = float(settings.get("execution", {}).get("paper_spread_pips", 1.5))
    slip_pips = float(settings.get("execution", {}).get("slippage_std_pips", 0.5))
    slip_dist = slip_pips * spec.pip_size
    atr_vals = features["atr_14"].dropna()
    vol_threshold = float(atr_vals.quantile(0.66)) if len(atr_vals) >= 10 else None

    # Generate both BUY and SELL signals
    buy_mask = features["close"] > features["high"].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
    sell_mask = features["close"] < features["low"].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
    valid = features["atr_14"].notna()
    buy_mask = buy_mask & valid
    sell_mask = sell_mask & valid

    # Build entry map with both directions
    entries: dict[int, list[dict]] = {}
    for direction, mask in [("BUY", buy_mask), ("SELL", sell_mask)]:
        for signal_time in features.index[mask.fillna(False)]:
            bar = int(ohlcv.index.get_loc(signal_time))
            entry_bar = bar + 1
            if entry_bar >= len(ohlcv):
                continue
            entry_price = float(ohlcv.iloc[entry_bar]["open"])
            atr = float(features.loc[signal_time, "atr_14"])
            if not math.isfinite(atr) or atr <= 0:
                continue
            stop = entry_price - 2.0 * atr if direction == "BUY" else entry_price + 2.0 * atr
            if (direction == "BUY" and stop >= entry_price) or (direction == "SELL" and stop <= entry_price):
                continue
            entries.setdefault(entry_bar, []).append({
                "direction": direction, "entry_price": entry_price, "stop": stop,
                "atr": atr, "signal_time": signal_time, "reason": f"donchian_{LOOKBACK}"
            })

    equity = 10000.0
    peak = equity
    position: dict | None = None
    trades: list[D3Trade] = []
    skipped_count = 0
    blocked: Counter = Counter()
    taken_count = 0

    for bar_idx, (ts, row) in enumerate(ohlcv.iterrows()):
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")

        # Check exit for open position
        if position is not None and bar_idx > position["entry_bar"]:
            o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
            entry = position["entry_price"]
            sl = position["stop"]
            tp = position["target"]
            d = position["direction"]
            exit_p = None
            reason = None

            if d == "BUY":
                if o <= sl: exit_p, reason = o, "stop_loss_gap"
                elif l <= sl: exit_p, reason = sl, "stop_loss"
                elif h >= tp: exit_p, reason = tp, "take_profit"
            else:
                if o >= sl: exit_p, reason = o, "stop_loss_gap"
                elif h >= sl: exit_p, reason = sl, "stop_loss"
                elif l <= tp: exit_p, reason = tp, "take_profit"

            if exit_p is not None and reason is not None:
                actual_exit = exit_p - slip_dist if d == "BUY" else exit_p + slip_dist
                gross = spec.pnl(d, entry, actual_exit, position["units"])
                net = gross - position["spread_est"]
                r_val = net / position["risk_amt"] if position["risk_amt"] > 0 else 0.0
                trades.append(D3Trade(direction=d, entry_time=position["entry_time"],
                    exit_time=ts.isoformat(), entry_price=entry, exit_price=actual_exit,
                    exit_reason=reason, r_multiple=r_val, net_pnl=net,
                    holding_bars=bar_idx - position["entry_bar"],
                    volatility_regime=position["vol"], session=position["session"],
                    weekday=position["wd"]))
                equity += net
                position = None

        # Process new signals
        for sig in entries.get(bar_idx, []):
            d = sig["direction"]
            ts_sig = pd.Timestamp(sig["signal_time"])
            if ts_sig.tzinfo is None: ts_sig = ts_sig.tz_localize("UTC")
            vol = classify_vol(sig["atr"], vol_threshold)
            sess = classify_session(ts_sig)
            wd_ = ts_sig.day_name()
            take, reason_blocked = should_take(d, vol, sess)

            if not take:
                skipped_count += 1
                blocked[f"blocked_{d}_{reason_blocked}"] += 1
                continue

            if position is not None:
                skipped_count += 1
                blocked[f"open_position_{d}"] += 1
                continue

            taken_count += 1
            slip_adj = slip_dist if d == "BUY" else -slip_dist
            adjusted_entry = sig["entry_price"] + slip_adj
            orig_risk = abs(sig["entry_price"] - sig["stop"])
            adjusted_stop = adjusted_entry - orig_risk if d == "BUY" else adjusted_entry + orig_risk
            target_1r = adjusted_entry + orig_risk if d == "BUY" else adjusted_entry - orig_risk

            target_risk = equity * RISK_PER_TRADE_PCT
            raw_units = target_risk / (orig_risk * spec.ounces_per_unit) if orig_risk > 0 else spec.min_units
            units = spec.lots_to_units(spec.round_lots(spec.units_to_lots(raw_units)))
            actual_risk = orig_risk * units * spec.ounces_per_unit
            spread = 2.0 * spread_pips * spec.pip_value_per_unit * units

            position = {"direction": d, "entry_bar": bar_idx, "entry_time": sig["signal_time"],
                "entry_price": adjusted_entry, "stop": adjusted_stop, "target": target_1r,
                "units": units, "risk_amt": actual_risk, "spread_est": spread,
                "vol": vol, "session": sess, "wd": wd_}

        peak = max(peak, equity)

    if position is not None and len(ohlcv) > 0:
        last = float(ohlcv.iloc[-1]["close"])
        gross = spec.pnl(position["direction"], position["entry_price"], last, position["units"])
        net = gross - position["spread_est"]
        r_val = net / position["risk_amt"] if position["risk_amt"] > 0 else 0.0
        trades.append(D3Trade(direction=position["direction"], entry_time=position["entry_time"],
            exit_time=ohlcv.index[-1].isoformat(), entry_price=position["entry_price"],
            exit_price=last, exit_reason="end_of_data", r_multiple=r_val, net_pnl=net,
            holding_bars=0, volatility_regime=position["vol"], session=position["session"],
            weekday=position["wd"]))

    return {"trades": trades, "taken": taken_count, "skipped": skipped_count,
        "blocked": dict(blocked), "final_equity": equity}


def compute(label: str, result: dict[str, Any]) -> dict[str, Any]:
    trades = result["trades"]
    r_vals = [t.r_multiple for t in trades]
    if not trades:
        return {"variant": label, "trades": 0}
    wins = sum(1 for r in r_vals if r > 0)
    losses = sum(1 for r in r_vals if r < 0)
    gain = sum(abs(r) for r in r_vals if r > 0)
    loss = sum(abs(r) for r in r_vals if r < 0)
    pf = gain / loss if loss > 0 else (10.0 if gain > 0 else 0.0)
    buy_t = [t for t in trades if t.direction == "BUY"]
    sell_t = [t for t in trades if t.direction == "SELL"]
    return {
        "variant": label, "trades": len(trades), "wins": wins, "losses": losses,
        "win_rate": wins / len(trades), "profit_factor": pf,
        "total_r": sum(r_vals), "avg_r": sum(r_vals) / len(r_vals),
        "total_pnl": sum(t.net_pnl for t in trades),
        "final_equity": result["final_equity"],
        "taken": result["taken"], "skipped": result["skipped"],
        "buy_trades": len(buy_t), "buy_wr": sum(1 for t in buy_t if t.r_multiple > 0) / len(buy_t) if buy_t else 0,
        "sell_trades": len(sell_t), "sell_wr": sum(1 for t in sell_t if t.r_multiple > 0) / len(sell_t) if sell_t else 0,
        "exits": dict(Counter(t.exit_reason for t in trades)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D3 forward shadow runner.")
    parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    ohlcv = load_ohlcv("M15", args.market_db)
    if ohlcv.empty:
        print("ERROR: No M15 data")
        return 1
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=548)
    ohlcv = ohlcv[ohlcv.index >= cutoff].copy()
    features = build_research_features(ohlcv)
    result = run_d3(ohlcv, features, settings)
    metrics = compute(STRATEGY_NAME, result)

    if args.json:
        import datetime as dt
        serializable = dict(metrics)
        serializable["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        serializable["strategy"] = STRATEGY_NAME
        print(json.dumps(serializable, indent=2, sort_keys=True, default=str))
        return 0

    print(f"AURUM-1 D3 Shadow ({STRATEGY_NAME})")
    print("=" * 60)
    print(f"Data: {len(ohlcv)} M15 candles")
    print(f"Signals taken: {metrics['taken']}, skipped: {metrics['skipped']}")
    print(f"Closed trades: {metrics['trades']}")
    print(f"\nPerformance:")
    print(f"  Win Rate: {metrics['wins']}/{metrics['trades']} = {metrics['win_rate']*100:.1f}%")
    print(f"  Profit Factor: {metrics['profit_factor']:.4f}")
    print(f"  Total R: {metrics['total_r']:.2f}")
    print(f"  Total PnL: ${metrics['total_pnl']:.2f}")
    print(f"  Final Equity: ${metrics['final_equity']:.2f}")
    print(f"\n  BUY:  {metrics['buy_trades']} trades, WR={metrics['buy_wr']*100:.1f}%")
    print(f"  SELL: {metrics['sell_trades']} trades, WR={metrics['sell_wr']*100:.1f}%")
    print(f"  Exits: {metrics['exits']}")

    # Compare with D2 and Raw
    shadow_db = ROOT / "reports" / "forward_shadow" / "donchian_shadow.sqlite3"
    print(f"\n{'─'*65}")
    print(f"{'':<25} {'D3 (SELL+1R+fil)':<20} {'D2 (BUY+1R+fil)':<20}")
    print(f"{'─'*25} {'─'*20} {'─'*20}")
    print(f"{'Trades':<25} {metrics['trades']:<20} {'543 (from D2)':<20}")
    print(f"{'WR':<25} {metrics['win_rate']*100:.1f}%{'':<17} 57.6%{'':<17}")
    print(f"{'PF':<25} {metrics['profit_factor']:<.4f}{'':<15} 1.3270{'':<15}")
    print(f"{'Total R':<25} {metrics['total_r']:<+20.2f} +76.87{'':<15}")
    print(f"{'Total PnL':<25} ${metrics['total_pnl']:<+19.2f} $2183.87{'':<15}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
