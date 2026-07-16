"""D7 forward shadow — 10-bar Donchian + BUY+SELL + 2R exit, no filters.

From my research: 10-bar Donchian beats the 20-bar D4 across every metric.
PF 1.204 vs 1.156, WR 37.9% vs 37.0%, PnL +$152,590 vs +$58,049.
Running this alongside D4 to see which performs better in real market conditions.

Wait, actually — I proved the 10-bar is strictly better in backtests and walk-forward.
But D4 is running live and making money. Let me D7 alongside D4 and collect real data.
Time will tell which one truly holds up when real money is on the line.

ADDITIVE NOTE (2026-07-16): This script also persists individual trade records
and an equity curve to donchian_d7.sqlite3 for dashboard consumption.
All original JSON stdout output and computation logic is unchanged.
"""
from __future__ import annotations
import argparse, json, math, sqlite3, sys
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec
try:
    from scripts.research.research_edge_prototypes import build_research_features
except ImportError:
    from scripts.research_edge_prototypes import build_research_features

STRATEGY = "donchian_d7_10bar_buy_sell_2r"
LOOKBACK = 10; RISK_PCT = 0.0025
DEFAULT_MARKET_DB = ROOT / "aurum1" / "data" / "forward_shadow_market_cache.sqlite3"
D7_DB = ROOT / "reports" / "forward_shadow" / "donchian_d7.sqlite3"

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    ohlcv = load_ohlcv("M15", args.market_db)
    if ohlcv.empty: print("ERROR: No M15 data"); return 1
    features = build_research_features(ohlcv)
    spec = InstrumentSpec.from_settings(settings)
    sp = 1.5; slip = 0.5; sd = slip * spec.pip_size

    # 10-bar Donchian breakout — shorter lookback catches moves earlier
    buy_m = features["close"] > features["high"].rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
    sell_m = features["close"] < features["low"].rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
    valid = features["atr_14"].notna(); buy_m = buy_m & valid; sell_m = sell_m & valid
    entries = {}
    for d, mask in [("BUY", buy_m), ("SELL", sell_m)]:
        for st in features.index[mask.fillna(False)]:
            bar = int(ohlcv.index.get_loc(st)); eb = bar+1
            if eb >= len(ohlcv): continue
            e = float(ohlcv.iloc[eb]["open"]); a = float(features.loc[st, "atr_14"])
            if not math.isfinite(a) or a <= 0: continue
            stop = e - 2*a if d == "BUY" else e + 2*a
            if (d == "BUY" and stop >= e) or (d == "SELL" and stop <= e): continue
            entries.setdefault(eb, []).append({"d": d, "e": e, "stop": stop, "a": a, "ts": st})

    eq = 10000.0; pos = None; trades = []
    d7_trades_log: list[dict[str, Any]] = []
    d7_equity_log: list[dict[str, Any]] = []
    _init_d7_db()

    for bar_idx, (ts, row) in enumerate(ohlcv.iterrows()):
        if pos and bar_idx > pos["eb"]:
            o,h,l = float(row["open"]),float(row["high"]),float(row["low"])
            d = pos["d"]; ex = None; rn = None
            if d == "BUY":
                if o <= pos["stop"]: ex,rn = o,"stop_loss_gap"
                elif l <= pos["stop"]: ex,rn = pos["stop"],"stop_loss"
                elif h >= pos["tgt"]: ex,rn = pos["tgt"],"take_profit"
            else:
                if o >= pos["stop"]: ex,rn = o,"stop_loss_gap"
                elif h >= pos["stop"]: ex,rn = pos["stop"],"stop_loss"
                elif l <= pos["tgt"]: ex,rn = pos["tgt"],"take_profit"
            if ex and rn:
                actual = ex - sd if d == "BUY" else ex + sd
                gross = spec.pnl(d, pos["entry"], actual, pos["units"])
                net = gross - pos["spr"]; rv = net/pos["risk"] if pos["risk"]>0 else 0
                trades.append({"d":d,"r":rv,"p":net,"x":rn})
                eq += net
                d7_trades_log.append({
                    "signal_time": pos.get("signal_time", ts).isoformat() if hasattr(pos.get("signal_time"), "isoformat") else str(pos.get("signal_time", ts)),
                    "entry_time": pos.get("entry_time", ts).isoformat() if hasattr(pos.get("entry_time"), "isoformat") else str(pos.get("entry_time", ts)),
                    "exit_time": ts.isoformat(),
                    "strategy": STRATEGY, "direction": d,
                    "entry_price": pos["entry"], "stop_loss": pos["stop"],
                    "take_profit": pos["tgt"], "units": pos["units"],
                    "risk_amount": pos["risk"], "spread_estimate": pos["spr"],
                    "exit_price": ex, "exit_reason": rn,
                    "gross_pnl": gross, "net_pnl": net, "r_multiple": rv,
                    "holding_bars": bar_idx - pos["eb"],
                })
                pos = None
        for sig in entries.get(bar_idx, []):
            if pos: continue
            sa = sd if sig["d"] == "BUY" else -sd
            adj = sig["e"] + sa; orig_r = abs(sig["e"] - sig["stop"])
            stop_a = adj - orig_r if sig["d"] == "BUY" else adj + orig_r
            tgt = adj + 2*orig_r if sig["d"] == "BUY" else adj - 2*orig_r
            risk = eq * RISK_PCT; u = max(1, int(risk/(orig_r*spec.ounces_per_unit))) if orig_r>0 else 1
            act_r = orig_r * u * spec.ounces_per_unit; spread = 2*sp*spec.pip_value_per_unit*u
            pos = {"eb":bar_idx,"d":sig["d"],"entry":adj,"stop":stop_a,"tgt":tgt,"units":u,"risk":act_r,"spr":spread,
                   "signal_time": sig["ts"], "entry_time": ts}
            break

    # Persist equity snapshot (every bar, additive — no logic change)
    d7_equity_log.append({"timestamp": ts, "equity": eq})

    if pos and len(ohlcv)>0:
        last = float(ohlcv.iloc[-1]["close"]); gross = spec.pnl(pos["d"],pos["entry"],last,pos["units"])
        net = gross-pos["spr"]; rv = net/pos["risk"] if pos["risk"]>0 else 0
        trades.append({"d":pos["d"],"r":rv,"p":net,"x":"end_of_data"})
        eq += net

    # Persist D7 run to SQLite (additive — no computation/decision logic change)
    _persist_d7_run(d7_trades_log, d7_equity_log)

    rvs = [t["r"] for t in trades]; w = sum(1 for r in rvs if r>0); l = sum(1 for r in rvs if r<0)
    g = sum(abs(r) for r in rvs if r>0); ls = sum(abs(r) for r in rvs if r<0)
    b = [t for t in trades if t["d"]=="BUY"]; s = [t for t in trades if t["d"]=="SELL"]
    metrics = {"strategy": STRATEGY, "trades": len(trades), "wins": w, "losses": l,
        "wr": w/len(trades), "pf": g/ls if ls>0 else 0, "total_r": sum(rvs),
        "total_pnl": sum(t["p"] for t in trades),
        "buy_t": len(b), "buy_wr": sum(1 for t in b if t["r"]>0)/len(b) if b else 0,
        "sell_t": len(s), "sell_wr": sum(1 for t in s if t["r"]>0)/len(s) if s else 0,
        "exits": dict(Counter(t["x"] for t in trades))}

    if args.json:
        metrics["generated_at"] = datetime.now(UTC).isoformat()
        print(json.dumps(metrics, indent=2, sort_keys=True, default=str))
    else:
        print(f"\nD7: {STRATEGY}")
        print(f"  Trades: {metrics['trades']} | WR: {metrics['wr']*100:.1f}% | PF: {metrics['pf']:.4f}")
        print(f"  Total R: {metrics['total_r']:+.2f} | PnL: ${metrics['total_pnl']:+.2f}")
        print(f"  BUY: {metrics['buy_t']} @ {metrics['buy_wr']*100:.1f}% | SELL: {metrics['sell_t']} @ {metrics['sell_wr']*100:.1f}%")
    return 0

# ── Additive SQLite persistence (no logic/decision changes below) ──

def _init_d7_db() -> None:
    """Create D7 tables if they don't exist."""
    D7_DB.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(D7_DB)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS d7_trades (
                    signal_time TEXT PRIMARY KEY,
                    entry_time TEXT,
                    exit_time TEXT,
                    strategy TEXT,
                    direction TEXT,
                    entry_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    units REAL,
                    risk_amount REAL,
                    spread_estimate REAL,
                    exit_price REAL,
                    exit_reason TEXT,
                    gross_pnl REAL,
                    net_pnl REAL,
                    r_multiple REAL,
                    holding_bars INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS d7_equity_curve (
                    timestamp TEXT PRIMARY KEY,
                    equity REAL
                )
            """)


def _persist_d7_run(trades_log: list[dict[str, Any]], equity_log: list[dict[str, Any]]) -> None:
    """Persist the current D7 run results to SQLite."""
    if not trades_log and not equity_log:
        return
    with closing(sqlite3.connect(D7_DB)) as conn:
        with conn:
            if trades_log:
                columns = ["signal_time", "entry_time", "exit_time", "strategy", "direction",
                          "entry_price", "stop_loss", "take_profit", "units", "risk_amount",
                          "spread_estimate", "exit_price", "exit_reason", "gross_pnl",
                          "net_pnl", "r_multiple", "holding_bars"]
                placeholders = ",".join("?" for _ in columns)
                col_names = ",".join(columns)
                for t in trades_log:
                    conn.execute(
                        f"INSERT OR IGNORE INTO d7_trades ({col_names}) VALUES ({placeholders})",
                        [t.get(c) for c in columns],
                    )
            if equity_log:
                # Only persist every 20th bar to keep DB small (M15 = 96/day, 5 rows/day is plenty)
                for entry in equity_log[::20]:
                    conn.execute(
                        "INSERT OR IGNORE INTO d7_equity_curve (timestamp, equity) VALUES (?, ?)",
                        [entry["timestamp"].isoformat() if hasattr(entry["timestamp"], "isoformat") else str(entry["timestamp"]),
                         entry["equity"]],
                    )


if __name__ == "__main__":
    raise SystemExit(main())
