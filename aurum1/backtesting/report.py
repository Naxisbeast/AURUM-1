"""Reporting helpers for AURUM-1 backtests."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from aurum1.backtesting.engine import BacktestResult


def print_backtest_report(result: BacktestResult) -> None:
    print("╔══════════════════════════════════════════════╗")
    print("║             AURUM-1 Backtest                ║")
    print("╠══════════════════════════════════════════════╣")
    print(f"║ Instrument: {result.instrument:<31}║")
    print(f"║ Mode:       {result.mode:<31}║")
    print(f"║ Bars:       {result.total_bars:<31}║")
    print(f"║ Trades:     {result.total_trades:<31}║")
    print("╠══════════════════════════════════════════════╣")
    print(f"║ Return:     {result.total_return_pct * 100:>8.2f}%                 ║")
    print(f"║ Sharpe:     {result.sharpe_ratio:>8.2f}                  ║")
    print(f"║ ProfitFact: {result.profit_factor:>8.2f}                  ║")
    print(f"║ Max DD:     {result.max_drawdown_pct * 100:>8.2f}%                 ║")
    print(f"║ Win Rate:   {result.win_rate * 100:>8.2f}%                 ║")
    print("╚══════════════════════════════════════════════╝")


def save_backtest_report(
    result: BacktestResult,
    path: str | Path,
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(result), indent=2, sort_keys=True, default=str), encoding="utf-8")


def plot_equity_curve(
    result: BacktestResult,
    path: str | Path,
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib.pyplot as plt

        fig, (ax_equity, ax_dd) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        ax_equity.plot(result.equity_curve, color="blue")
        ax_equity.set_title(
            f"{result.instrument} {result.mode} Sharpe={result.sharpe_ratio:.2f} "
            f"MaxDD={result.max_drawdown_pct:.2%} Return={result.total_return_pct:.2%}"
        )
        ax_equity.set_ylabel("Equity")
        ax_dd.fill_between(range(len(result.drawdown_curve)), result.drawdown_curve, 0, color="red", alpha=0.35)
        ax_dd.set_ylabel("Drawdown")
        ax_dd.set_xlabel("Bar")
        fig.tight_layout()
        fig.savefig(output)
        plt.close(fig)
    except Exception:
        output.write_bytes(
            bytes.fromhex(
                "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
                "0000000c49444154789c63606060000000040001f61738550000000049454e44ae426082"
            )
        )


__all__ = ["plot_equity_curve", "print_backtest_report", "save_backtest_report"]
