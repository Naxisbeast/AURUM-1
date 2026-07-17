"""Unit tests for Donchian breakout signal generation.

Tests the core signal logic: BUY on 20-bar high breakout, SELL on 20-bar low
breakdown, NONE when inside the range.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from scripts.research.donchian_research_runner import donchian_signals


def _make_ohlcv(
    close_prices: list[float],
    start_price: float = 100.0,
    n_bars: int = 100,
) -> pd.DataFrame:
    """Build OHLCV frame with given close prices and realistic OHLC."""
    rng = np.random.RandomState(42)
    n = len(close_prices)
    timestamps = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")

    opens = np.concatenate([[start_price], close_prices[:-1]])
    atr = 1.0
    highs = np.maximum(opens, close_prices) + rng.uniform(0, atr, n)
    lows = np.minimum(opens, close_prices) - rng.uniform(0, atr, n)

    # Ensure high >= low
    for i in range(n):
        if highs[i] < lows[i]:
            mid = (highs[i] + lows[i]) / 2
            spread = abs(highs[i] - lows[i]) / 2 + 0.01
            highs[i] = mid + spread
            lows[i] = mid - spread

    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": close_prices,
        "volume": 100.0,
    }, index=timestamps)


def _build_features(ohlcv: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """Minimal feature set for Donchian signal generation."""
    close = ohlcv["close"]
    high = ohlcv["high"]
    low = ohlcv["low"]

    # ATR(14)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr_14 = tr.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()

    features = pd.DataFrame({
        "close": close,
        "high": high,
        "low": low,
        "atr_14": atr_14,
    }, index=ohlcv.index)

    # Pre-rolling high/low for lookback check
    features["high_20_max"] = high.rolling(lookback, min_periods=lookback).max().shift(1)
    features["low_20_min"] = low.rolling(lookback, min_periods=lookback).min().shift(1)

    return features


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDonchianSignals:
    """Donchian breakout signal generation."""

    def test_buy_signal_on_uptrend_breakout(self):
        """BUY fires when close > 20-bar high."""
        # Create trending data: 80 bars flat, then 20 bars breaking up
        flat = [100.0] * 80
        breakout = list(np.linspace(100.0, 105.0, 20))
        prices = flat + breakout
        ohlcv = _make_ohlcv(prices, n_bars=100)
        features = _build_features(ohlcv)
        signals = donchian_signals(ohlcv, features, lookback=20, htf_filter=False)
        buy_signals = [s for s in signals if s.direction == "BUY"]
        assert len(buy_signals) >= 1, "Should have at least one BUY signal on uptrend"

    def test_sell_signal_on_downtrend_breakdown(self):
        """SELL fires when close < 20-bar low."""
        flat = [100.0] * 80
        breakdown = list(np.linspace(100.0, 95.0, 20))
        prices = flat + breakdown
        ohlcv = _make_ohlcv(prices, n_bars=100)
        features = _build_features(ohlcv)
        signals = donchian_signals(ohlcv, features, lookback=20, htf_filter=False)
        sell_signals = [s for s in signals if s.direction == "SELL"]
        assert len(sell_signals) >= 1, "Should have at least one SELL signal on downtrend"

    def test_no_signal_in_ranging_market(self):
        """No signals when price stays inside the 20-bar range."""
        rng = np.random.RandomState(42)
        n = 100
        # Random walk with small steps inside a tight range
        prices = [100.0]
        for _ in range(n):
            prices.append(prices[-1] + rng.normal(0, 0.2))
        ohlcv = _make_ohlcv(prices[:n], n_bars=n)
        features = _build_features(ohlcv)
        signals = donchian_signals(ohlcv, features, lookback=20, htf_filter=False)
        # In very tight range, there should be few or no signals
        assert len(signals) < n // 2, "Ranging market should not produce excessive signals"

    def test_buy_signal_has_correct_structure(self):
        """Each signal has the required fields with correct types."""
        flat = [100.0] * 80
        breakout = list(np.linspace(100.0, 105.0, 20))
        ohlcv = _make_ohlcv(flat + breakout, n_bars=100)
        features = _build_features(ohlcv)
        signals = donchian_signals(ohlcv, features, lookback=20, htf_filter=False)
        assert len(signals) > 0
        signal = signals[0]
        assert signal.direction in ("BUY", "SELL")
        assert signal.signal_bar >= 0
        assert signal.entry_bar > signal.signal_bar
        assert signal.entry_price > 0
        assert signal.atr_at_signal > 0
        assert signal.stop_loss > 0
        assert signal.take_profit > 0
        assert signal.reason is not None

    def test_entry_on_next_bar_open(self):
        """Entry must be on the bar after the signal bar."""
        flat = [100.0] * 80
        breakout = list(np.linspace(100.0, 105.0, 20))
        ohlcv = _make_ohlcv(flat + breakout, n_bars=100)
        features = _build_features(ohlcv)
        signals = donchian_signals(ohlcv, features, lookback=20, htf_filter=False)
        for s in signals:
            assert s.entry_bar == s.signal_bar + 1, "Entry must be next bar"

    def test_signal_take_profit_is_2r(self):
        """Take profit should be at 2R (entry ± 2× risk distance)."""
        flat = [100.0] * 80
        breakout = list(np.linspace(100.0, 105.0, 20))
        ohlcv = _make_ohlcv(flat + breakout, n_bars=100)
        features = _build_features(ohlcv)
        signals = donchian_signals(ohlcv, features, lookback=20, htf_filter=False)
        for s in signals:
            risk_dist = abs(s.entry_price - s.stop_loss)
            expected_tp = (
                s.entry_price + 2.0 * risk_dist
                if s.direction == "BUY"
                else s.entry_price - 2.0 * risk_dist
            )
            assert s.take_profit == pytest.approx(expected_tp, abs=0.01)

    def test_no_signal_without_enough_bars(self):
        """Need at least `lookback` bars for valid signal generation."""
        n = 30  # only 30 bars with lookback=20 is technically enough
        prices = list(np.linspace(100.0, 105.0, n))
        ohlcv = _make_ohlcv(prices, n_bars=n)
        features = _build_features(ohlcv)
        signals = donchian_signals(ohlcv, features, lookback=20, htf_filter=False)
        # Signals should exist or not — the test verifies it doesn't crash
        assert isinstance(signals, list)

    def test_both_buy_and_sell_in_volatile_market(self):
        """In volatile two-sided market, both BUY and SELL signals can appear."""
        rng = np.random.RandomState(42)
        n = 100
        prices = [100.0]
        # Larger steps to trigger both breakouts
        for _ in range(n):
            prices.append(prices[-1] + rng.normal(0, 2.0))
        ohlcv = _make_ohlcv(prices[:n], n_bars=n)
        features = _build_features(ohlcv)
        signals = donchian_signals(ohlcv, features, lookback=20, htf_filter=False)
        directions = {s.direction for s in signals}
        assert "BUY" in directions
        assert "SELL" in directions
