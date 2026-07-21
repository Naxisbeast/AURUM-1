"""Analyze the AURUM-1 backtest trade lifecycle funnel.

This script reads a mode-specific backtest JSON report. The execution SQLite DB
can be supplied for context, but the lifecycle funnel is intentionally computed
from the JSON report because the execution DB may contain multiple modes or
walk-forward windows.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = ROOT / "reports" / "backtest_rule_regime.json"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "research"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = load_report(args.report)
    trades = net_trades(report.get("trades", []))
    summary = build_lifecycle_summary(report, trades, args.report)
    if args.execution_db is not None:
        summary["execution_db_context"] = execution_db_context(args.execution_db)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    json_path = output_dir / f"trade_lifecycle_{stamp}.json"
    csv_path = output_dir / f"trade_lifecycle_{stamp}.csv"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    write_trade_csv(csv_path, trades)

    print_lifecycle_summary(summary, json_path, csv_path)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze AURUM-1 backtest trade lifecycle.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Mode-specific backtest JSON report.")
    parser.add_argument(
        "--execution-db",
        type=Path,
        default=None,
        help="Optional backtest execution SQLite DB for status-count context only.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def load_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Backtest report not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def net_trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not row.get("type") and "direction" in row]


def build_lifecycle_summary(report: dict[str, Any], trades: list[dict[str, Any]], report_path: Path) -> dict[str, Any]:
    signals = int(report.get("total_signals", 0))
    pending = int(report.get("signals_approved", 0))
    filled = len(trades)
    rejections = dict(report.get("rejection_reasons", {}))
    expired = int(rejections.get("fill_timeout", 0))
    normal_trades = [trade for trade in trades if not bool(trade.get("entry_gap_fill", False))]
    gap_trades = [trade for trade in trades if bool(trade.get("entry_gap_fill", False))]
    exit_reasons = Counter(str(trade.get("reason", "unknown")) for trade in trades)

    return {
        "mode": report.get("mode"),
        "instrument": report.get("instrument"),
        "source_report": str(report_path),
        "funnel": {
            "signals_generated": signals,
            "became_pending_orders": pending,
            "became_pending_pct_of_signals": pct(pending, signals),
            "filled": filled,
            "filled_pct_of_pending": pct(filled, pending),
            "normal_fill": len(normal_trades),
            "gap_fill_worse_price": len(gap_trades),
            "gap_fill_pct_of_filled": pct(len(gap_trades), filled),
            "expired_unfilled": expired,
            "expired_pct_of_pending": pct(expired, pending),
            "signals_rejected": int(report.get("signals_rejected", 0)),
            "rejection_reasons": rejections,
        },
        "filled_trade_exits": {
            "total": filled,
            "by_reason": dict(sorted(exit_reasons.items())),
            "hit_sl": sum(count for reason, count in exit_reasons.items() if reason.startswith("stop_loss")),
            "hit_tp": int(exit_reasons.get("take_profit", 0)),
            "timeout_or_end": int(exit_reasons.get("timeout", 0)) + int(exit_reasons.get("backtest_end", 0)),
            "other": filled
            - sum(count for reason, count in exit_reasons.items() if reason.startswith("stop_loss"))
            - int(exit_reasons.get("take_profit", 0))
            - int(exit_reasons.get("timeout", 0))
            - int(exit_reasons.get("backtest_end", 0)),
        },
        "pnl_split": {
            "all_filled_trades": trade_stats(trades),
            "normal_fill_trades": trade_stats(normal_trades),
            "gap_fill_trades": trade_stats(gap_trades),
        },
    }


def trade_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    gross = [float(trade.get("gross_pnl", trade.get("pnl", 0.0))) for trade in trades]
    net = [float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0)))) for trade in trades]
    gross_wins = [value for value in gross if value > 0.0]
    gross_losses = [value for value in gross if value <= 0.0]
    gross_profit = sum(gross_wins)
    gross_loss = abs(sum(gross_losses))
    return {
        "count": len(trades),
        "gross_pnl": sum(gross),
        "net_pnl": sum(net),
        "win_rate_gross": pct(len(gross_wins), len(trades)),
        "profit_factor_gross": gross_profit / gross_loss if gross_loss else None,
        "avg_gross_pnl": sum(gross) / len(gross) if gross else 0.0,
        "avg_net_pnl": sum(net) / len(net) if net else 0.0,
    }


def execution_db_context(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT status, payload_json FROM trades_log").fetchall()
    status_counts: Counter[str] = Counter()
    status_by_mode: dict[str, Counter[str]] = {}
    rejection_by_mode: dict[str, Counter[str]] = {}
    malformed_payloads = 0
    for status, payload_json in rows:
        status_text = str(status or "unknown")
        status_counts[status_text] += 1
        try:
            payload = json.loads(str(payload_json))
        except json.JSONDecodeError:
            malformed_payloads += 1
            continue
        risk_order = payload.get("risk_order", {})
        instruction = risk_order.get("instruction", {}) if isinstance(risk_order, dict) else {}
        mode = str(instruction.get("machine_mode", "unknown"))
        status_by_mode.setdefault(mode, Counter())[status_text] += 1
        rejection = str(payload.get("rejection_reason") or risk_order.get("rejection_reason") or "")
        if rejection:
            rejection_by_mode.setdefault(mode, Counter())[rejection] += 1
    return {
        "path": str(path),
        "exists": True,
        "note": "Execution DB status counts may include multiple modes and walk-forward windows.",
        "trades_log_rows": len(rows),
        "status_counts": dict(status_counts),
        "status_counts_by_mode": {mode: dict(counter) for mode, counter in sorted(status_by_mode.items())},
        "rejection_counts_by_mode": {mode: dict(counter) for mode, counter in sorted(rejection_by_mode.items())},
        "malformed_payloads": malformed_payloads,
    }


def write_trade_csv(path: Path, trades: list[dict[str, Any]]) -> None:
    fields = [
        "position_id",
        "direction",
        "regime",
        "signal_time",
        "market_open_time",
        "market_close_time",
        "reason",
        "entry_gap_fill",
        "requested_entry_price",
        "entry_fill_basis_price",
        "actual_entry",
        "actual_exit",
        "units",
        "duration_bars",
        "gross_pnl",
        "net_pnl",
        "spread_cost",
        "total_slippage_cost",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for trade in trades:
            writer.writerow({field: trade.get(field, "") for field in fields})


def pct(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def print_lifecycle_summary(summary: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    funnel = summary["funnel"]
    exits = summary["filled_trade_exits"]
    pnl = summary["pnl_split"]
    print("AURUM-1 Trade Lifecycle")
    print("=" * 56)
    print(f"Mode:                         {summary.get('mode')}")
    print(f"Signals generated:            {funnel['signals_generated']}")
    print(
        f"-> Became pending orders:      {funnel['became_pending_orders']} "
        f"({funnel['became_pending_pct_of_signals']:.2%} of signals)"
    )
    print(
        f"-> Filled:                     {funnel['filled']} "
        f"({funnel['filled_pct_of_pending']:.2%} of pending)"
    )
    print(f"   -> Normal fill:             {funnel['normal_fill']}")
    print(
        f"   -> Gap fill worse price:    {funnel['gap_fill_worse_price']} "
        f"({funnel['gap_fill_pct_of_filled']:.2%} of filled)"
    )
    print(
        f"-> Expired unfilled:           {funnel['expired_unfilled']} "
        f"({funnel['expired_pct_of_pending']:.2%} of pending)"
    )
    print("\nFilled trade exits:")
    print(f"-> Hit SL:                     {exits['hit_sl']} ({pct(exits['hit_sl'], exits['total']):.2%})")
    print(f"-> Hit TP:                     {exits['hit_tp']} ({pct(exits['hit_tp'], exits['total']):.2%})")
    print(f"-> Timeout/end:                {exits['timeout_or_end']} ({pct(exits['timeout_or_end'], exits['total']):.2%})")
    print(f"-> Other:                      {exits['other']} ({pct(exits['other'], exits['total']):.2%})")
    print("\nP&L split:")
    print_stats("All filled trades", pnl["all_filled_trades"])
    print_stats("Normal fill trades", pnl["normal_fill_trades"])
    print_stats("Gap fill trades", pnl["gap_fill_trades"])
    print("\nWrote:")
    print(f"  JSON: {json_path}")
    print(f"  CSV:  {csv_path}")


def print_stats(label: str, stats: dict[str, Any]) -> None:
    pf = stats["profit_factor_gross"]
    pf_text = "inf" if pf is None else f"{pf:.2f}"
    print(
        f"  {label:<20} count={stats['count']:>4} "
        f"gross=${stats['gross_pnl']:>9.2f} net=${stats['net_pnl']:>9.2f} "
        f"win={stats['win_rate_gross']:>6.2%} PF={pf_text}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
