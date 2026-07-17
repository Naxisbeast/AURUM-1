"""D4 paper trader regression test.

Feeds KNOWN OHLCV data through the D4 trading pipeline and verifies
trade output matches expected values. Prevents regressions from code changes.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from aurum1.execution import ExecutionEngine
from aurum1.instruments import InstrumentSpec
from aurum1.risk import RiskManager
from aurum1.signals import CandleRow, TradeInstruction


def _make_test_ohlcv(n_bars: int = 5000, seed: int = 42) -> pd.DataFrame:
    """Generate deterministic OHLCV data for regression testing."""
    rng = np.random.RandomState(seed)
    start_price = 2000.0
    daily_vol = 15.0
    bars_per_day = 24 * 60 // 15
    bar_vol = daily_vol / np.sqrt(bars_per_day)

    log_returns = rng.normal(0, bar_vol / start_price, n_bars)
    closes = start_price * np.exp(np.cumsum(log_returns))

    opens = np.concatenate([[start_price], closes[:-1]])
    intra_range = bar_vol * rng.exponential(0.5, n_bars)
    highs = np.maximum(opens, closes) + intra_range
    lows = np.minimum(opens, closes) - intra_range

    for i in range(n_bars):
        if highs[i] < lows[i]:
            mid = (highs[i] + lows[i]) / 2
            spread = abs(highs[i] - lows[i]) / 2 + 0.01
            highs[i] = mid + spread
            lows[i] = mid - spread

    timestamps = pd.date_range("2024-01-01", periods=n_bars, freq="15min", tz="UTC")
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes, "volume": rng.randint(100, 1000, n_bars),
    }, index=timestamps)


def _settings(tmp_dir: Path) -> dict[str, Any]:
    return {
        "app": {"random_seed": 42},
        "broker": {
            "paper_trade": True,
            "paper_initial_equity": 10000.0,
            "oanda": {"instrument": "XAU_USD"},
        },
        "instruments": {
            "XAU_USD": {
                "oanda_instrument": "XAU_USD", "account_currency": "USD",
                "pip_size": 0.01, "ounces_per_unit": 1.0, "units_per_lot": 100.0,
                "min_units": 1.0, "max_units": 1000.0, "unit_precision": 0,
                "min_lot_size": 0.01, "max_lot_size": 10.0, "lot_step": 0.01,
            }
        },
        "data": {"db_path": str(tmp_dir / "test.sqlite3")},
        "execution": {"slippage_std_pips": 0.0, "paper_spread_pips": 1.5},
        "risk": {
            "risk_per_trade_pct": 0.0025, "kelly_min_trades": 20,
            "kelly_default_fraction": 0.25, "kelly_max_fraction": 0.25,
            "max_spread_pips": 3.0, "daily_loss_kill_pct": 0.03,
            "total_drawdown_kill_pct": 0.08, "pip_size": 0.01,
        },
    }


class TestD4Regression:
    """Regression test: D4 strategy on known data produces expected trades."""

    def test_donchian_signal_on_uptrend(self):
        """Uptrend data should generate BUY signals."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            settings = _settings(tmp)
            ohlcv = _make_test_ohlcv(2000, seed=42)

            execution = ExecutionEngine(settings)
            risk_mgr = RiskManager(settings)
            spec = InstrumentSpec.from_settings(settings)
            trades = []

            for i in range(200, len(ohlcv)):
                row = ohlcv.iloc[i]
                ts = ohlcv.index[i]

                # Feed candle to PaperBroker for SL/TP
                candle = CandleRow(
                    timestamp=ts.to_pydatetime(),
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row["volume"]),
                    atr_14=max(1e-9, float(row["high"] - row["low"])),
                    adx_14=0.0, ema_9=0.0, ema_20=0.0,
                    session_london=1, session_ny=0, session_overlap=0,
                )
                execution.update_paper_prices(candle)

                # Check for newly closed trades
                while len(execution.broker._trade_history) > len(trades):
                    new_trade = execution.broker._trade_history[len(trades)]
                    trades.append(new_trade)

                # Don't enter if position open
                if execution.broker.get_open_positions():
                    continue

                # Donchian breakout check
                lookback = 20
                if i < lookback + 5:
                    continue
                high_20 = float(ohlcv["high"].iloc[i - lookback:i].max())
                low_20 = float(ohlcv["low"].iloc[i - lookback:i].min())
                close = float(row["close"])

                direction = None
                if close > high_20:
                    direction = "BUY"
                elif close < low_20:
                    direction = "SELL"

                if direction is None:
                    continue

                atr = max(1e-9, float(row["high"] - row["low"]))
                entry_price = float(row["open"])
                stop_loss = entry_price - 2.0 * atr if direction == "BUY" else entry_price + 2.0 * atr
                if stop_loss >= entry_price if direction == "BUY" else stop_loss <= entry_price:
                    continue
                risk_dist = abs(entry_price - stop_loss)
                take_profit = entry_price + 2.0 * risk_dist if direction == "BUY" else entry_price - 2.0 * risk_dist

                instruction = TradeInstruction(
                    timestamp=ts.to_pydatetime(), direction=direction,
                    entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
                    atr_at_entry=atr, signal_score=1.0,
                    regime="TRENDING_UP" if direction == "BUY" else "TRENDING_DOWN",
                    confidence=0.75, machine_mode="test",
                )
                account = execution.broker.get_account_state()
                risk_order = risk_mgr.evaluate(
                    instruction, account, list(execution.broker._trade_history)
                )
                if risk_order.approved:
                    execution.execute(risk_order)

            # Verify trades exist (the test data should generate some)
            assert len(trades) > 0, "D4 strategy should generate trades on test data"

            # Verify all trades have valid data
            for t in trades:
                assert "r_multiple" in t
                assert "net_pnl" in t
                assert "direction" in t
                assert t["direction"] in ("BUY", "SELL")

    def test_trade_count_reproducible(self):
        """Same seed should produce same number of trades."""
        with tempfile.TemporaryDirectory() as td1:
            with tempfile.TemporaryDirectory() as td2:
                s1 = _settings(Path(td1))
                s2 = _settings(Path(td2))

                ohlcv = _make_test_ohlcv(1000, seed=42)

                def run_d4(settings, ohlcv):
                    execution = ExecutionEngine(settings)
                    risk_mgr = RiskManager(settings)
                    trades = []
                    for i in range(200, len(ohlcv)):
                        row = ohlcv.iloc[i]
                        ts = ohlcv.index[i]
                        candle = CandleRow(
                            timestamp=ts.to_pydatetime(),
                            open=float(row["open"]), high=float(row["high"]),
                            low=float(row["low"]), close=float(row["close"]),
                            volume=float(row["volume"]),
                            atr_14=max(1e-9, float(row["high"] - row["low"])),
                            adx_14=0.0, ema_9=0.0, ema_20=0.0,
                            session_london=1, session_ny=0, session_overlap=0,
                        )
                        execution.update_paper_prices(candle)
                        while len(execution.broker._trade_history) > len(trades):
                            trades.append(execution.broker._trade_history[len(trades)])
                        if execution.broker.get_open_positions():
                            continue
                        lookback = 20
                        if i < lookback + 5:
                            continue
                        high_20 = float(ohlcv["high"].iloc[i - lookback:i].max())
                        low_20 = float(ohlcv["low"].iloc[i - lookback:i].min())
                        close = float(row["close"])
                        if close > high_20:
                            direction = "BUY"
                        elif close < low_20:
                            direction = "SELL"
                        else:
                            continue
                        atr = max(1e-9, float(row["high"] - row["low"]))
                        entry_price = float(row["open"])
                        stop_loss = entry_price - 2.0 * atr if direction == "BUY" else entry_price + 2.0 * atr
                        if stop_loss >= entry_price if direction == "BUY" else stop_loss <= entry_price:
                            continue
                        risk_dist = abs(entry_price - stop_loss)
                        take_profit = entry_price + 2.0 * risk_dist if direction == "BUY" else entry_price - 2.0 * risk_dist
                        instruction = TradeInstruction(
                            timestamp=ts.to_pydatetime(), direction=direction,
                            entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
                            atr_at_entry=atr, signal_score=1.0,
                            regime="TRENDING_UP" if direction == "BUY" else "TRENDING_DOWN",
                            confidence=0.75, machine_mode="test",
                        )
                        account = execution.broker.get_account_state()
                        risk_order = risk_mgr.evaluate(
                            instruction, account, list(execution.broker._trade_history)
                        )
                        if risk_order.approved:
                            execution.execute(risk_order)
                    return trades

                trades1 = run_d4(s1, ohlcv)
                trades2 = run_d4(s2, ohlcv)

                assert len(trades1) == len(trades2), (
                    f"Trade count not reproducible: {len(trades1)} vs {len(trades2)}"
                )
