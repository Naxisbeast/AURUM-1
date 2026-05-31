"""Analyze maximum favorable/adverse excursion for AURUM-1 backtest trades."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import load_ohlcv, load_settings
from aurum1.instruments import InstrumentSpec


DEFAULT_REPORT = ROOT / "reports" / "backtest_rule_regime.json"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "research"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    market_db = args.market_db or Path(
        str(settings.get("backtesting", {}).get("market_data_db_path", "aurum1/data/backtest_market_cache.sqlite3"))
    )
    report = json.loads(args.report.read_text(encoding="utf-8"))
    trades = [trade for trade in report.get("trades", []) if not trade.get("type") and "direction" in trade]
    ohlcv = load_ohlcv(args.timeframe, ROOT / market_db if not market_db.is_absolute() else market_db)
    instrument = InstrumentSpec.from_settings(settings)

    detail = analyze_trades(trades, ohlcv, instrument)
    summary = build_summary(detail, args.report, market_db)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    json_path = output_dir / f"mfe_mae_{stamp}.json"
    csv_path = output_dir / f"mfe_mae_{stamp}.csv"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    write_detail_csv(csv_path, detail)

    print_summary(summary, json_path, csv_path)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze MFE/MAE for AURUM-1 backtest trades.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Mode-specific backtest JSON report.")
    parser.add_argument("--market-db", type=Path, default=None, help="SQLite market cache. Defaults to settings.")
    parser.add_argument("--timeframe", default="M15", help="OHLCV timeframe table to use.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def analyze_trades(
    trades: list[dict[str, Any]],
    ohlcv: pd.DataFrame,
    instrument: InstrumentSpec,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, trade in enumerate(trades, start=1):
        open_bar = int(trade.get("open_bar", -1))
        close_bar = int(trade.get("close_bar", -1))
        if open_bar < 0 or close_bar < open_bar or close_bar >= len(ohlcv):
            rows.append(error_row(index, trade, "invalid_open_close_bar"))
            continue
        segment = ohlcv.iloc[open_bar : close_bar + 1]
        if segment.empty:
            rows.append(error_row(index, trade, "empty_market_segment"))
            continue
        rows.append(analyze_one_trade(index, trade, segment, instrument))
    return rows


def analyze_one_trade(
    trade_index: int,
    trade: dict[str, Any],
    segment: pd.DataFrame,
    instrument: InstrumentSpec,
) -> dict[str, Any]:
    direction = str(trade.get("direction"))
    entry = float(trade.get("actual_entry", trade.get("entry", 0.0)))
    units = float(trade.get("units", 0.0))
    risk_amount = float(trade.get("risk_amount", 0.0))
    high = segment["high"].astype(float)
    low = segment["low"].astype(float)

    if direction == "BUY":
        max_favorable_price = float(high.max())
        max_adverse_price = float(low.min())
        mfe_price = max(0.0, max_favorable_price - entry)
        mae_price = max(0.0, entry - max_adverse_price)
    elif direction == "SELL":
        max_favorable_price = float(low.min())
        max_adverse_price = float(high.max())
        mfe_price = max(0.0, entry - max_favorable_price)
        mae_price = max(0.0, max_adverse_price - entry)
    else:
        return error_row(trade_index, trade, "unknown_direction")

    exposure = units * instrument.ounces_per_unit
    mfe_dollars = mfe_price * exposure
    mae_dollars = mae_price * exposure
    mfe_r = mfe_dollars / risk_amount if risk_amount > 0.0 else None
    mae_r = mae_dollars / risk_amount if risk_amount > 0.0 else None
    net_pnl = float(trade.get("net_pnl", trade.get("pnl_after_fees", trade.get("pnl", 0.0))))
    is_winner = net_pnl > 0.0

    return {
        "trade_index": trade_index,
        "position_id": trade.get("position_id", ""),
        "direction": direction,
        "regime": trade.get("regime", ""),
        "reason": trade.get("reason", ""),
        "entry_gap_fill": bool(trade.get("entry_gap_fill", False)),
        "market_open_time": trade.get("market_open_time", ""),
        "market_close_time": trade.get("market_close_time", ""),
        "open_bar": int(trade.get("open_bar", -1)),
        "close_bar": int(trade.get("close_bar", -1)),
        "duration_bars": int(trade.get("duration_bars", 0)),
        "entry": entry,
        "units": units,
        "risk_amount": risk_amount,
        "net_pnl": net_pnl,
        "is_winner": is_winner,
        "mfe_price": mfe_price,
        "mae_price": mae_price,
        "mfe_dollars": mfe_dollars,
        "mae_dollars": mae_dollars,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "mfe_gt_0_5r": bool(mfe_r is not None and mfe_r > 0.5),
        "mfe_gt_1r": bool(mfe_r is not None and mfe_r > 1.0),
        "mae_gt_0_5r": bool(mae_r is not None and mae_r > 0.5),
        "mae_gt_1r": bool(mae_r is not None and mae_r > 1.0),
        "moved_favorably": mfe_dollars > 0.0,
        "analysis_error": "",
    }


def error_row(trade_index: int, trade: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "trade_index": trade_index,
        "position_id": trade.get("position_id", ""),
        "direction": trade.get("direction", ""),
        "regime": trade.get("regime", ""),
        "reason": trade.get("reason", ""),
        "entry_gap_fill": bool(trade.get("entry_gap_fill", False)),
        "market_open_time": trade.get("market_open_time", ""),
        "market_close_time": trade.get("market_close_time", ""),
        "open_bar": int(trade.get("open_bar", -1)),
        "close_bar": int(trade.get("close_bar", -1)),
        "duration_bars": int(trade.get("duration_bars", 0)),
        "entry": trade.get("actual_entry", trade.get("entry", "")),
        "units": trade.get("units", ""),
        "risk_amount": trade.get("risk_amount", ""),
        "net_pnl": trade.get("net_pnl", ""),
        "is_winner": False,
        "mfe_price": "",
        "mae_price": "",
        "mfe_dollars": "",
        "mae_dollars": "",
        "mfe_r": "",
        "mae_r": "",
        "mfe_gt_0_5r": False,
        "mfe_gt_1r": False,
        "mae_gt_0_5r": False,
        "mae_gt_1r": False,
        "moved_favorably": False,
        "analysis_error": error,
    }


def build_summary(detail: list[dict[str, Any]], report_path: Path, market_db: Path) -> dict[str, Any]:
    valid = [row for row in detail if not row.get("analysis_error")]
    winners = [row for row in valid if bool(row["is_winner"])]
    losers = [row for row in valid if not bool(row["is_winner"])]
    losers_mfe_gt_half = [row for row in losers if bool(row["mfe_gt_0_5r"])]
    losers_mfe_gt_one = [row for row in losers if bool(row["mfe_gt_1r"])]
    winners_mae_gt_half = [row for row in winners if bool(row["mae_gt_0_5r"])]
    winners_mae_gt_one = [row for row in winners if bool(row["mae_gt_1r"])]

    return {
        "source_report": str(report_path),
        "market_db": str(market_db),
        "total_trades": len(detail),
        "valid_trades": len(valid),
        "analysis_errors": len(detail) - len(valid),
        "aggregate": {
            "all": aggregate_rows(valid),
            "winners": aggregate_rows(winners),
            "losers": aggregate_rows(losers),
        },
        "diagnostics": {
            "losing_trades_with_mfe_gt_0_5r": len(losers_mfe_gt_half),
            "losing_trades_with_mfe_gt_0_5r_pct": pct(len(losers_mfe_gt_half), len(losers)),
            "losing_trades_with_mfe_gt_1r": len(losers_mfe_gt_one),
            "losing_trades_with_mfe_gt_1r_pct": pct(len(losers_mfe_gt_one), len(losers)),
            "winning_trades_with_mae_gt_0_5r": len(winners_mae_gt_half),
            "winning_trades_with_mae_gt_0_5r_pct": pct(len(winners_mae_gt_half), len(winners)),
            "winning_trades_with_mae_gt_1r": len(winners_mae_gt_one),
            "winning_trades_with_mae_gt_1r_pct": pct(len(winners_mae_gt_one), len(winners)),
            "losing_trades_that_moved_favorably_pct": pct(
                sum(1 for row in losers if bool(row["moved_favorably"])),
                len(losers),
            ),
        },
    }


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(rows),
        "avg_mfe_dollars": mean([float(row["mfe_dollars"]) for row in rows]),
        "avg_mae_dollars": mean([float(row["mae_dollars"]) for row in rows]),
        "avg_mfe_r": mean([float(row["mfe_r"]) for row in rows if row["mfe_r"] is not None]),
        "avg_mae_r": mean([float(row["mae_r"]) for row in rows if row["mae_r"] is not None]),
        "median_mfe_r": median([float(row["mfe_r"]) for row in rows if row["mfe_r"] is not None]),
        "median_mae_r": median([float(row["mae_r"]) for row in rows if row["mae_r"] is not None]),
    }


def write_detail_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "trade_index",
        "position_id",
        "direction",
        "regime",
        "reason",
        "entry_gap_fill",
        "market_open_time",
        "market_close_time",
        "open_bar",
        "close_bar",
        "duration_bars",
        "entry",
        "units",
        "risk_amount",
        "net_pnl",
        "is_winner",
        "mfe_price",
        "mae_price",
        "mfe_dollars",
        "mae_dollars",
        "mfe_r",
        "mae_r",
        "mfe_gt_0_5r",
        "mfe_gt_1r",
        "mae_gt_0_5r",
        "mae_gt_1r",
        "moved_favorably",
        "analysis_error",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def pct(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def print_summary(summary: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    aggregate = summary["aggregate"]
    diagnostics = summary["diagnostics"]
    print("AURUM-1 MFE/MAE Analysis")
    print("=" * 56)
    print(f"Trades analyzed:                         {summary['valid_trades']}/{summary['total_trades']}")
    print_row("All trades", aggregate["all"])
    print_row("Winners", aggregate["winners"])
    print_row("Losers", aggregate["losers"])
    print("\nDiagnostics:")
    print(
        "Losing trades with MFE > 0.5R:           "
        f"{diagnostics['losing_trades_with_mfe_gt_0_5r']} "
        f"({diagnostics['losing_trades_with_mfe_gt_0_5r_pct']:.2%})"
    )
    print(
        "Losing trades with MFE > 1.0R:           "
        f"{diagnostics['losing_trades_with_mfe_gt_1r']} "
        f"({diagnostics['losing_trades_with_mfe_gt_1r_pct']:.2%})"
    )
    print(
        "Winning trades with MAE > 0.5R:          "
        f"{diagnostics['winning_trades_with_mae_gt_0_5r']} "
        f"({diagnostics['winning_trades_with_mae_gt_0_5r_pct']:.2%})"
    )
    print(
        "Winning trades with MAE > 1.0R:          "
        f"{diagnostics['winning_trades_with_mae_gt_1r']} "
        f"({diagnostics['winning_trades_with_mae_gt_1r_pct']:.2%})"
    )
    print(
        "Losing trades that moved favorably:      "
        f"{diagnostics['losing_trades_that_moved_favorably_pct']:.2%}"
    )
    print("\nWrote:")
    print(f"  JSON: {json_path}")
    print(f"  CSV:  {csv_path}")


def print_row(label: str, stats: dict[str, Any]) -> None:
    print(
        f"{label:<12} count={stats['count']:>4} "
        f"avg MFE=${stats['avg_mfe_dollars']:>8.2f} avg MAE=${stats['avg_mae_dollars']:>8.2f} "
        f"avg MFE(R)={stats['avg_mfe_r']:>5.2f} avg MAE(R)={stats['avg_mae_r']:>5.2f}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
