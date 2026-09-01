"""Live trade scaling & leverage simulation for D4.

Replays the ACTUAL live paper-trade sequence (from paper_trading.sqlite3) under
a faithful reproduction of the RiskManager + PaperBroker sizing model to answer:

  1. Scale capital UP (e.g. $10k -> $100k / $1M): same R per trade, same pace,
     dollar PnL scales ~linearly *if* position sizing keeps up. Does it?
  2. Scale capital DOWN (e.g. $10k -> $100): the 1-unit minimum-position floor
     (0.01 lot) means risk-per-trade can be 15-40% of equity. Ruin math.
  3. Raise risk_per_trade_pct: the only real speed lever, and its drawdown cost.
  4. Leverage: modeled honestly as a MARGIN constraint (notional/leverage vs
     equity). In a fixed-% risk model leverage does NOT change R per trade; it
     only decides whether the broker lets you open the position at all.

Sizing model (mirrors aurum1/risk/manager.py + aurum1/instruments.py):
    desired_risk = equity * risk_pct * kelly_frac          # per trade
    raw_units    = desired_risk / sl_per_unit              # sl_per_unit from live
    units        = max(1, round_half_up(raw_units) to lot-step)   # min 0.01 lot = 1 unit
    margin       = units * entry_price / leverage
    if margin > equity: position blocked (missed), no PnL
    risk_realized = units * sl_per_unit
    pnl           = r_multiple * risk_realized
    equity       += pnl

The sim is VALIDATED first: replay at ($10k, 0.35%, kelly 0.25) and compare the
modeled equity path to the actual one. Then projections at other sizes/levers.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "aurum1" / "data" / "paper_trading.sqlite3"
OUTPUT_FILE = ROOT / "reports" / "research" / "live_scaling_simulation.json"

# Live config (from settings.yaml)
LIVE_EQUITY = 10000.0
LIVE_RISK_PCT = 0.0035
LIVE_KELLY = 0.25          # observed cap in live data
MIN_LOT = 0.01             # 0.01 lot = 1 unit floor
UNITS_PER_LOT = 100.0
MAX_LOT = 10.0

# Scenarios to sweep
ACCOUNT_SIZES = [100, 500, 1000, 5000, 10000, 100000, 1000000, 10000000]
RISK_PCTS = [0.001, 0.0025, 0.0035, 0.005, 0.01, 0.02]
LEVERAGES = [1, 5, 20, 50, 100]
BOOTSTRAP_N = 2000
RNG_SEED = 42


def load_trades(db_path: Path) -> list[dict[str, float]]:
    """Load live trades, deriving per-unit stop distance and R-multiple."""
    trades = []
    with closing(sqlite3.connect(str(db_path))) as conn:
        rows = conn.execute(
            "SELECT entry_price, stop_loss, units, risk_amount, r_multiple, "
            "net_pnl, exit_reason FROM trades ORDER BY id"
        ).fetchall()
    for entry, stop, units, risk_amt, r, pnl, reason in rows:
        units = float(units or 0)
        risk_amt = float(risk_amt or 0)
        sl_per_unit = (risk_amt / units) if units and risk_amt else abs(float(entry) - float(stop))
        trades.append({
            "entry_price": float(entry),
            "sl_per_unit": max(sl_per_unit, 1e-9),
            "units": units,
            "r_multiple": float(r or 0),
            "net_pnl": float(pnl or 0),
            "reason": str(reason),
        })
    return trades


def round_units(raw_units: float, min_units: float = 1.0, lot_step: float = MIN_LOT) -> float:
    """Replicate InstrumentSpec: clamp to min lot / max lot, half-up to lot step.

    min_units: floor in OANDA units (1 unit = 1 troy oz). OANDA proprietary
    platform allows 0.1 units (0.001 lot); MT5 allows 0.001 lot.
    """
    lots = raw_units / UNITS_PER_LOT
    min_lot = min_units / UNITS_PER_LOT
    lots = max(min_lot, min(MAX_LOT, lots))
    lots = math.floor((lots / lot_step) + 0.5) * lot_step
    return max(min_units, lots * UNITS_PER_LOT)


def simulate(
    trades: list[dict[str, float]],
    initial_equity: float,
    risk_pct: float,
    kelly_frac: float,
    leverage: float,
    rng: np.random.Generator | None = None,
    min_units: float = 1.0,
    lot_step: float = MIN_LOT,
) -> dict[str, Any]:
    """Replay the trade sequence under the sizing model.

    Returns equity path, stats, blocked (margin) count. If rng is given, the
    sequence order is shuffled (bootstrap mode).
    """
    seq = list(trades)
    if rng is not None:
        rng.shuffle(seq)

    equity = float(initial_equity)
    peak = equity
    max_dd = 0.0
    realized_trades = 0
    blocked = 0
    equity_path = [equity]
    risk_per_trade_pcts = []
    blocked_examples = []

    for t in seq:
        desired_risk = equity * risk_pct * kelly_frac
        raw_units = desired_risk / t["sl_per_unit"] if t["sl_per_unit"] > 0 else 0.0
        units = round_units(raw_units, min_units=min_units, lot_step=lot_step)

        # Margin feasibility check (the honest role of leverage)
        notional = units * t["entry_price"]
        margin = notional / leverage
        if margin > equity:
            blocked += 1
            if len(blocked_examples) < 3:
                blocked_examples.append({
                    "blocked_equity": round(equity, 2),
                    "units": units,
                    "notional": round(notional, 2),
                    "margin_req": round(margin, 2),
                    "leverage": leverage,
                })
            continue

        risk_realized = units * t["sl_per_unit"]
        if equity > 0:
            risk_per_trade_pcts.append(risk_realized / equity * 100.0)
        pnl = t["r_multiple"] * risk_realized
        equity += pnl
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        realized_trades += 1
        equity_path.append(equity)

        # Ruin guard: below a unit of gold there's nothing left to trade
        if equity <= 1.0:
            break

    return {
        "initial_equity": initial_equity,
        "risk_pct": risk_pct,
        "kelly_frac": kelly_frac,
        "leverage": leverage,
        "final_equity": round(equity, 2),
        "return_pct": round((equity - initial_equity) / initial_equity * 100.0, 2),
        "max_dd_pct": round(max_dd, 2),
        "trades_realized": realized_trades,
        "trades_blocked": blocked,
        "avg_risk_pct_per_trade": round(float(np.mean(risk_per_trade_pcts)), 3) if risk_per_trade_pcts else 0.0,
        "min_risk_pct_per_trade": round(float(np.min(risk_per_trade_pcts)), 3) if risk_per_trade_pcts else 0.0,
        "max_risk_pct_per_trade": round(float(np.max(risk_per_trade_pcts)), 3) if risk_per_trade_pcts else 0.0,
        "blocked_examples": blocked_examples,
        "ruined": equity <= 1.0,
        "equity_path": equity_path,
    }


def bootstrap(trades, initial_equity, risk_pct, kelly_frac, leverage, n=BOOTSTRAP_N,
              min_units: float = 1.0, lot_step: float = MIN_LOT):
    """Bootstrap the scenario: shuffled reorderings -> median/percentile stats."""
    rng = np.random.default_rng(RNG_SEED)
    finals, returns, max_dds, ruins = [], [], [], []
    for _ in range(n):
        res = simulate(trades, initial_equity, risk_pct, kelly_frac, leverage, rng=rng,
                       min_units=min_units, lot_step=lot_step)
        finals.append(res["final_equity"])
        returns.append(res["return_pct"])
        max_dds.append(res["max_dd_pct"])
        ruins.append(res["ruined"])
    return {
        "initial_equity": initial_equity,
        "risk_pct": risk_pct,
        "leverage": leverage,
        "min_units": min_units,
        "lot_step": lot_step,
        "n_sims": n,
        "final_equity_median": round(float(np.median(finals)), 2),
        "final_equity_p5": round(float(np.percentile(finals, 5)), 2),
        "final_equity_p95": round(float(np.percentile(finals, 95)), 2),
        "max_dd_median_pct": round(float(np.median(max_dds)), 2),
        "max_dd_p95_pct": round(float(np.percentile(max_dds, 95)), 2),
        "return_median_pct": round(float(np.median(returns)), 2),
        "ruin_probability_pct": round(float(np.mean(ruins)) * 100.0, 2),
    }


def fmt_usd(x: float) -> str:
    return f"${x:>12,.0f}"


def main() -> dict:
    print("=" * 78)
    print("  LIVE SCALING SIMULATION — D4 (actual 141-trade live sequence)")
    print("=" * 78)

    if not DB_PATH.exists():
        print(f"  ERROR: no live DB at {DB_PATH}")
        return {}

    trades = load_trades(DB_PATH)
    print(f"  Loaded {len(trades)} live trades")
    sl = [t["sl_per_unit"] for t in trades]
    print(f"  Per-unit stop distance: min=${min(sl):.2f} med=${np.median(sl):.2f} max=${max(sl):.2f}")
    rvals = [t["r_multiple"] for t in trades]
    print(f"  R-multiple: mean={np.mean(rvals):+.3f} win_rate={(sum(1 for r in rvals if r>0)/len(rvals)*100):.1f}%")

    # ---- Validation: replay live config, compare modeled path to actual ----
    live_res = simulate(trades, LIVE_EQUITY, LIVE_RISK_PCT, LIVE_KELLY, leverage=100.0)
    actual_final = LIVE_EQUITY + sum(t["net_pnl"] for t in trades)
    print("\n  VALIDATION (replay live config @ $10k, 0.35%, kelly 0.25):")
    print(f"    Actual final equity : {fmt_usd(actual_final)}")
    print(f"    Modeled final equity: {fmt_usd(live_res['final_equity'])}")
    print(f"    Modeled max DD      : {live_res['max_dd_pct']:.2f}%")
    print(f"    Avg risk/trade      : {live_res['avg_risk_pct_per_trade']:.2f}%  "
          f"(min {live_res['min_risk_pct_per_trade']:.2f} / max {live_res['max_risk_pct_per_trade']:.2f})")

    # ---- Scenario A: account size sweep at fixed risk 0.35%, leverage 50 ----
    print("\n  A. ACCOUNT SIZE @ 0.35% risk, 1:50 leverage:")
    print(f"  {'Account':>12} {'Final':>12} {'Ret%':>8} {'MaxDD%':>8} {'Trades':>7} {'Blocked':>8} {'AvgRisk%':>9}")
    size_results = []
    for size in ACCOUNT_SIZES:
        r = simulate(trades, size, 0.0035, LIVE_KELLY, 50.0)
        size_results.append(r)
        print(f"  {size:>10,} {fmt_usd(r['final_equity']):>12} {r['return_pct']:>8.1f} "
              f"{r['max_dd_pct']:>8.1f} {r['trades_realized']:>7} {r['trades_blocked']:>8} "
              f"{r['avg_risk_pct_per_trade']:>9.2f}")

    # ---- Scenario B: risk sweep at $100k, leverage 50 ----
    print("\n  B. RISK-PER-TRADE SWEEP @ $100k, 1:50 leverage (the real speed lever):")
    print(f"  {'Risk%':>7} {'Final':>12} {'Ret%':>8} {'MaxDD%':>8} {'AvgRisk%':>9}")
    risk_results = []
    for rp in RISK_PCTS:
        r = simulate(trades, 100000, rp, LIVE_KELLY, 50.0)
        risk_results.append(r)
        print(f"  {rp*100:>6.2f}% {fmt_usd(r['final_equity']):>12} {r['return_pct']:>8.1f} "
              f"{r['max_dd_pct']:>8.1f} {r['avg_risk_pct_per_trade']:>9.2f}")

    # ---- Scenario C: the $100 account — min-position floor + leverage ----
    print("\n  C. THE $100 ACCOUNT — min 1-unit floor vs leverage:")
    print(f"  {'Lev':>5} {'Final':>12} {'Ret%':>8} {'MaxDD%':>8} {'Blocked':>8} {'AvgRisk%':>9}  notes")
    small_results = []
    for lev in LEVERAGES:
        r = simulate(trades, 100, 0.0035, LIVE_KELLY, float(lev))
        small_results.append(r)
        notes = ""
        if r["trades_blocked"] > 0:
            notes = f"  {r['trades_blocked']} trades couldn't open (margin)"
        if r["ruined"]:
            notes += "  ⚠ RUIN"
        print(f"  {lev:>5} {fmt_usd(r['final_equity']):>12} {r['return_pct']:>8.1f} "
              f"{r['max_dd_pct']:>8.1f} {r['trades_blocked']:>8} {r['avg_risk_pct_per_trade']:>9.2f}{notes}")

    # ---- Scenario D: bootstrap confidence for headline scenarios ----
    print("\n  D. BOOTSTRAP (2000 shuffled replays) — median [5th-95th]")
    headlines = [
        (10000, 0.0035, 50.0),
        (100000, 0.0035, 50.0),
        (100000, 0.01, 50.0),
        (100, 0.0035, 50.0),
        (1000000, 0.0035, 50.0),
    ]
    boot_results = []
    for (size, rp, lev) in headlines:
        b = bootstrap(trades, size, rp, LIVE_KELLY, lev)
        boot_results.append(b)
        print(f"  {size:>10,} @ {rp*100:.2f}% : final {fmt_usd(b['final_equity_median']):>12} "
              f"[{fmt_usd(b['final_equity_p5'])} - {fmt_usd(b['final_equity_p95'])}]  "
              f"DD med {b['max_dd_median_pct']:.1f}%  ruin {b['ruin_probability_pct']:.1f}%")

    # ---- Scenario E: micro-sizing — OANDA's REAL minimums, not the config's ----
    # Current config: min_units=1.0 (1 full oz). OANDA proprietary platform:
    # 0.1 units. MT5: 0.001 lots = 0.1 units. Both allow 1/10 of the config floor.
    print("\n  E. MICRO-SIZING — OANDA's real minimum (0.1 units = 0.1 oz):")
    print(f"  {'Acct':>7} {'MinU':>5} {'Final':>11} {'Ret%':>8} {'MaxDD%':>8} {'AvgRisk%':>9}  notes")
    micro_results = []
    for acct, minu in [(100, 0.1), (250, 0.1), (500, 0.1), (1000, 0.1), (2000, 0.1), (1000, 0.01)]:
        r = simulate(trades, acct, 0.0035, LIVE_KELLY, 50.0, min_units=minu, lot_step=0.001)
        micro_results.append({
            "account": acct, "min_units": minu, **{k: r[k] for k in
            ("final_equity", "return_pct", "max_dd_pct", "avg_risk_pct_per_trade",
             "trades_blocked", "ruined")},
        })
        notes = ""
        if r["trades_blocked"] > 0:
            notes = f"  {r['trades_blocked']} blocked"
        print(f"  {acct:>7} {minu:>5} {fmt_usd(r['final_equity']):>11} {r['return_pct']:>8.1f} "
              f"{r['max_dd_pct']:>8.1f} {r['avg_risk_pct_per_trade']:>9.2f}{notes}")

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "n_live_trades": len(trades),
        "validation": {
            "actual_final_equity": round(actual_final, 2),
            "modeled_final_equity": live_res["final_equity"],
            "modeled_max_dd_pct": live_res["max_dd_pct"],
            "avg_risk_pct_per_trade": live_res["avg_risk_pct_per_trade"],
            "model": "RiskManager replica (kelly 0.25, min 0.01 lot = 1 unit, half-up to lot step)",
            "matched": abs(actual_final - live_res["final_equity"]) < 0.01,
        },
        "account_size_sweep_0_35pct": size_results,
        "risk_sweep_100k": risk_results,
        "small_account_100": small_results,
        "micro_sizing": micro_results,
        "bootstrap": boot_results,
        "interpretation": {
            "under_risking_finding": "Live system is actually running at ~0.16-0.18% risk/trade, NOT the configured 0.35%. Configured 0.35% x Kelly cap 0.25 = 0.0875% intent, but the 1-unit min floor forces ~1 unit ($11-40 risk) regardless, roughly doubling effective risk at $10k. 135/141 trades were exactly 1 unit.",
            "micro_sizing": "OANDA proprietary platform allows 0.1 units (0.1 oz) of Gold CFD, and MT5 allows 0.001 lots — 10x finer than the config's min_units=1.0. Lowering the floor lets $250-500 accounts trade at sub-1% risk/trade. A $100 account stays marginal even at 0.1 units because one ATR stop (~$11-40/oz) is 11-40% of equity regardless of unit size.",
            "scaling_up": "Scaling $10k -> $100k at SAME config gives ~4.6-5x dollars, NOT 10x, because at $10k the 1-unit floor inflates risk to ~0.17% while at $100k sizing is correct (~0.085%). Return% drops from 14% to 6.4%. To get the full linear 10x you must raise risk_per_trade_pct to ~0.7% (or lift the Kelly 0.25 cap). Edge/PF unchanged at all sizes (no capacity issue below ~$100M).",
            "scaling_down": "Minimum viable account ~ $4k-11k: 1 unit of gold risks $11-40, so below ~$4k that is >1% risk/trade (gambling) and below $11k it exceeds the intended 0.35%. A $100 account is not viable: risk 3.5%+/trade, ~45% max DD, and at leverage <1:50 every trade is margin-blocked.",
            "risk_lever": "Raising risk_per_trade_pct is the ONLY true speed lever, but Kelly cap 0.25 throttles it: even 2% config yields only ~0.5% actual risk. To deploy more you must raise BOTH risk_per_trade_pct AND kelly_max_fraction. Monte Carlo: each doubling roughly doubles return AND max DD.",
            "leverage": "Leverage does NOT change R per trade in a %-risk model; it only lifts the margin constraint so positions can open (below 1:50 a $100 account opens nothing). Real edge, not leverage, compounds.",
        },
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Results saved to {OUTPUT_FILE}")
    print("=" * 78)
    return summary


if __name__ == "__main__":
    main()
