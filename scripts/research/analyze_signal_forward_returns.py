"""Measure forward returns after AURUM-1 state-machine signals.

This analysis is independent of risk sizing and order execution. It regenerates
the state-machine trade instructions on closed candles, then measures signed
future close-to-close returns after each emitted instruction.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.backtesting.engine import BacktestEngine, _candle_from_row
from aurum1.data.ingestion import load_cot, load_macro, load_ohlcv, load_settings
from aurum1.signals import MachineMode, StateMachine


DEFAULT_OUTPUT_DIR = ROOT / "reports" / "research"
DEFAULT_HORIZONS = (1, 4, 8, 16, 32, 64)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    market_db = args.market_db or Path(
        str(settings.get("backtesting", {}).get("market_data_db_path", "aurum1/data/backtest_market_cache.sqlite3"))
    )
    db_path = ROOT / market_db if not market_db.is_absolute() else market_db
    ohlcv = load_ohlcv(args.timeframe, db_path)
    macro = load_macro(db_path)
    cot = load_cot(db_path)
    mode = MachineMode(args.mode)
    horizons = tuple(int(value) for value in args.horizons)

    detail = build_forward_return_rows(ohlcv, macro, cot, settings, mode, horizons)
    summary = build_summary(detail, mode, db_path, horizons)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    json_path = output_dir / f"signal_forward_returns_{stamp}.json"
    csv_path = output_dir / f"signal_forward_returns_{stamp}.csv"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    write_detail_csv(csv_path, detail, horizons)

    print_summary(summary, json_path, csv_path)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze state-machine signal forward returns.")
    parser.add_argument(
        "--mode",
        default=MachineMode.RULE_REGIME.value,
        choices=[mode.value for mode in MachineMode],
        help="Machine mode used to regenerate state-machine instructions.",
    )
    parser.add_argument("--market-db", type=Path, default=None, help="SQLite market cache. Defaults to settings.")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--horizons", type=int, nargs="+", default=list(DEFAULT_HORIZONS))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def build_forward_return_rows(
    ohlcv: pd.DataFrame,
    macro: pd.DataFrame,
    cot: pd.DataFrame,
    settings: dict[str, Any],
    mode: MachineMode,
    horizons: tuple[int, ...],
) -> list[dict[str, Any]]:
    engine = BacktestEngine(settings)
    state_machine = StateMachine(settings, mode=mode)
    features = engine._build_causal_feature_table(ohlcv, macro, cot, htf_frames=None)
    close = ohlcv["close"].astype(float).reset_index(drop=True)
    index_positions = {timestamp: pos for pos, timestamp in enumerate(pd.DatetimeIndex(ohlcv.index))}
    rows: list[dict[str, Any]] = []

    for timestamp in ohlcv.index:
        if timestamp not in features.index:
            continue
        feature_frame = features.loc[:timestamp]
        feature_row = feature_frame.iloc[-1]
        candle = _candle_from_row(timestamp, ohlcv.loc[timestamp], feature_row)
        signal = engine._infer_signal(feature_frame, feature_row, timestamp, mode)
        instruction = state_machine.on_candle(candle, signal, is_blackout=False)
        if instruction is None:
            continue
        pos = index_positions[pd.Timestamp(timestamp)]
        row: dict[str, Any] = {
            "signal_time": pd.Timestamp(timestamp).isoformat(),
            "bar_index": int(pos),
            "direction": instruction.direction,
            "entry_price": float(instruction.entry_price),
            "close_at_signal": float(close.iloc[pos]),
            "regime": instruction.regime,
            "signal_score": float(instruction.signal_score),
        }
        for horizon in horizons:
            future_pos = pos + horizon
            if future_pos >= len(close):
                row[f"forward_return_{horizon}bar"] = ""
                row[f"signed_forward_return_{horizon}bar"] = ""
                row[f"positive_{horizon}bar"] = ""
                continue
            raw_return = float(close.iloc[future_pos] / close.iloc[pos] - 1.0)
            signed_return = raw_return if instruction.direction == "BUY" else -raw_return
            row[f"forward_return_{horizon}bar"] = raw_return
            row[f"signed_forward_return_{horizon}bar"] = signed_return
            row[f"positive_{horizon}bar"] = signed_return > 0.0
        rows.append(row)
    return rows


def build_summary(
    detail: list[dict[str, Any]],
    mode: MachineMode,
    market_db: Path,
    horizons: tuple[int, ...],
) -> dict[str, Any]:
    by_direction: dict[str, Any] = {}
    for direction in ("BUY", "SELL", "ALL"):
        subset = detail if direction == "ALL" else [row for row in detail if row["direction"] == direction]
        by_direction[direction] = {
            f"{horizon}bar": summarize_values(
                [
                    float(row[f"signed_forward_return_{horizon}bar"])
                    for row in subset
                    if row.get(f"signed_forward_return_{horizon}bar") != ""
                ]
            )
            for horizon in horizons
        }
    return {
        "mode": mode.value,
        "market_db": str(market_db),
        "signals_analyzed": len(detail),
        "direction_counts": {
            "BUY": sum(1 for row in detail if row["direction"] == "BUY"),
            "SELL": sum(1 for row in detail if row["direction"] == "SELL"),
        },
        "horizons_bars": list(horizons),
        "summary_by_direction": by_direction,
    }


def summarize_values(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "sample_size": 0,
            "mean_return": 0.0,
            "median_return": 0.0,
            "positive_rate": 0.0,
            "t_statistic": 0.0,
        }
    mean_value = sum(values) / len(values)
    std = sample_std(values)
    t_stat = mean_value / (std / math.sqrt(len(values))) if std > 0.0 and len(values) > 1 else 0.0
    return {
        "sample_size": len(values),
        "mean_return": mean_value,
        "median_return": median(values),
        "positive_rate": sum(1 for value in values if value > 0.0) / len(values),
        "t_statistic": t_stat,
    }


def write_detail_csv(path: Path, rows: list[dict[str, Any]], horizons: tuple[int, ...]) -> None:
    fields = [
        "signal_time",
        "bar_index",
        "direction",
        "entry_price",
        "close_at_signal",
        "regime",
        "signal_score",
    ]
    for horizon in horizons:
        fields.extend(
            [
                f"forward_return_{horizon}bar",
                f"signed_forward_return_{horizon}bar",
                f"positive_{horizon}bar",
            ]
        )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean_value = sum(values) / len(values)
    variance = sum((value - mean_value) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def median(values: list[float]) -> float:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def print_summary(summary: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    print("AURUM-1 Signal Forward Returns")
    print("=" * 56)
    print(f"Mode:                         {summary['mode']}")
    print(f"Signals analyzed:             {summary['signals_analyzed']}")
    print(f"BUY / SELL:                   {summary['direction_counts']['BUY']} / {summary['direction_counts']['SELL']}")
    print("\nSigned close-to-close forward returns:")
    for direction in ("BUY", "SELL", "ALL"):
        print(f"\n{direction}:")
        for horizon, stats in summary["summary_by_direction"][direction].items():
            print(
                f"  {horizon:<5} n={stats['sample_size']:>4} "
                f"mean={stats['mean_return']:>8.4%} median={stats['median_return']:>8.4%} "
                f"positive={stats['positive_rate']:>6.2%} t={stats['t_statistic']:>6.2f}"
            )
    print("\nWrote:")
    print(f"  JSON: {json_path}")
    print(f"  CSV:  {csv_path}")


if __name__ == "__main__":
    raise SystemExit(main())
