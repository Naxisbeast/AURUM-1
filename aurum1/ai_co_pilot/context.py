"""Context Builder: converts market state into structured data for the AI.

Takes raw candle data, position state, and recent trade history and builds
a structured context dict that the AI agent can reason about.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class MarketContext:
    """Complete market state snapshot for AI decision-making."""

    # Timestamp
    timestamp: str

    # Signal info
    signal_direction: str | None  # 'BUY', 'SELL', or None
    signal_bar_index: int
    entry_price: float | None
    atr_at_signal: float | None

    # Current candle (the bar we're about to trade or are in)
    current_open: float
    current_high: float
    current_low: float
    current_close: float
    current_volume: float
    current_atr_14: float
    current_adx_14: float

    # Market context
    atr_percentile_100: float  # 0-1, where ATR sits vs last 100 bars
    ema_9: float
    ema_20: float
    ema_alignment: float  # positive = bullish, negative = bearish
    hour_of_day: int
    day_of_week: int
    session_name: str  # 'asia', 'london', 'ny', 'overlap', 'other'

    # Position state (if one exists)
    has_position: bool
    position_direction: str | None
    position_entry: float | None
    position_stop: float | None
    position_target: float | None
    position_bars_held: int | None
    position_current_r: float | None  # current R-multiple of open position
    position_highest_r: float | None  # best R-multiple seen so far

    # Recent trade history (last 10)
    recent_wins: int
    recent_losses: int
    recent_total_trades: int
    recent_win_rate: float
    recent_r_values: list[float]  # last 10 R-multiples

    # Account state
    equity: float
    peak_equity: float
    current_drawdown_pct: float
    daily_pnl: float


class ContextBuilder:
    """Builds MarketContext from raw market data."""

    def __init__(self, lookback_donchian: int = 10):
        self.lookback = lookback_donchian
        self.atr_period = 14
        self.adx_period = 14
        self.atr_pct_period = 100
        self.recent_trade_window = 10

        # Internal state
        self._atr_values: list[float] = []
        self._atr_pct_values: list[float] = []
        self._ohlcv_history: list[pd.Series] = []
        self._features_history: list[pd.Series] = []
        self.recent_r_values: list[float] = []

    def add_trade_result(self, r_value: float) -> None:
        """Record a completed trade's R-multiple for context."""
        self.recent_r_values.append(float(r_value))
        if len(self.recent_r_values) > self.recent_trade_window:
            self.recent_r_values.pop(0)

    def build(
        self,
        timestamp: datetime,
        signal_direction: str | None,
        signal_bar_index: int,
        entry_price: float | None,
        atr_at_signal: float | None,
        current_candle: pd.Series,
        current_features: pd.Series,
        position: dict[str, Any] | None,
        equity: float,
        peak_equity: float,
        daily_pnl: float,
    ) -> MarketContext:
        """Build a complete market context from live data."""

        # Session detection
        hour = timestamp.hour
        if 7 <= hour < 16:
            session = "london"
        elif 13 <= hour < 22:
            session = "ny"
        elif 0 <= hour < 8:
            session = "asia"
        else:
            session = "other"
        if 13 <= hour < 16:
            session = "overlap"  # Override: London/NY overlap

        # Position state
        pos_r = None
        pos_high_r = None
        pos_bars = None
        if position is not None:
            distance = (
                current_candle["close"] - position["entry"]
                if position["direction"] == "BUY"
                else position["entry"] - current_candle["close"]
            )
            risk = abs(position["entry"] - position["stop"])
            pos_r = distance / risk if risk > 0 else 0

            # Track highest R seen
            pos_high_r = position.get("highest_r", pos_r)
            if pos_r and (pos_high_r is None or pos_r > pos_high_r):
                pos_high_r = pos_r

            pos_bars = position.get("bars_held", 0)

        # Recent trade stats
        recent_wins = sum(1 for r in self.recent_r_values if r > 0)
        recent_losses = sum(1 for r in self.recent_r_values if r <= 0)
        recent_total = len(self.recent_r_values)

        return MarketContext(
            timestamp=timestamp.isoformat(),
            signal_direction=signal_direction,
            signal_bar_index=signal_bar_index,
            entry_price=entry_price,
            atr_at_signal=atr_at_signal,
            current_open=float(current_candle.get("open", 0)),
            current_high=float(current_candle.get("high", 0)),
            current_low=float(current_candle.get("low", 0)),
            current_close=float(current_candle.get("close", 0)),
            current_volume=float(current_candle.get("volume", 0)),
            current_atr_14=float(current_features.get("atr_14", 0)),
            current_adx_14=float(current_features.get("adx_14", 0)),
            atr_percentile_100=float(
                current_features.get("atr_percentile", current_features.get("atr_percentile_100", 0.5))
            ),
            ema_9=float(current_features.get("ema_9", 0)),
            ema_20=float(current_features.get("ema_20", 0)),
            ema_alignment=float(
                current_features.get("ema_9", 0) - current_features.get("ema_20", 0)
            ),
            hour_of_day=hour,
            day_of_week=timestamp.weekday(),
            session_name=session,
            has_position=position is not None,
            position_direction=position["direction"] if position else None,
            position_entry=position["entry"] if position else None,
            position_stop=position["stop"] if position else None,
            position_target=position.get("target") if position else None,
            position_bars_held=pos_bars,
            position_current_r=pos_r,
            position_highest_r=pos_high_r,
            recent_wins=recent_wins,
            recent_losses=recent_losses,
            recent_total_trades=recent_total,
            recent_win_rate=recent_wins / recent_total if recent_total > 0 else 0.0,
            recent_r_values=list(self.recent_r_values),
            equity=equity,
            peak_equity=peak_equity,
            current_drawdown_pct=(peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0,
            daily_pnl=daily_pnl,
        )

    def context_to_prompt(self, ctx: MarketContext) -> str:
        """Convert MarketContext into a formatted prompt for the AI."""
        lines = [
            "=== MARKET CONTEXT ===",
            f"Time: {ctx.timestamp}",
            f"Session: {ctx.session_name}  |  Day: {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][ctx.day_of_week]}",
            "",
            "=== SIGNAL STATUS ===",
        ]
        if ctx.signal_direction:
            lines.append(f"New signal: {ctx.signal_direction} at entry ${ctx.entry_price:.2f}" if ctx.entry_price else f"New signal: {ctx.signal_direction}")
            lines.append(f"ATR at signal: ${ctx.atr_at_signal:.2f}" if ctx.atr_at_signal else "")
        else:
            lines.append("No new signal")
        lines.append("")

        if ctx.has_position:
            lines.append("=== OPEN POSITION ===")
            lines.append(f"Direction: {ctx.position_direction}")
            lines.append(f"Entry: ${ctx.position_entry:.2f}  |  Stop: ${ctx.position_stop:.2f}  |  Target: ${ctx.position_target:.2f}" if ctx.position_target else f"Entry: ${ctx.position_entry:.2f}  |  Stop: ${ctx.position_stop:.2f}")
            lines.append(f"Bars held: {ctx.position_bars_held}")
            lines.append(f"Current R: {ctx.position_current_r:+.2f}  |  Best R seen: {ctx.position_highest_r:+.2f}" if ctx.position_current_r is not None else "")
            lines.append("")
        else:
            lines.append("No open position")
            lines.append("")

        lines.append("=== MARKET CONDITIONS ===")
        lines.append(f"ATR(14): ${ctx.current_atr_14:.2f}  |  ATR percentile: {ctx.atr_percentile_100:.0%}")
        lines.append(f"ADX(14): {ctx.current_adx_14:.1f}  |  EMA9: ${ctx.ema_9:.2f}  |  EMA20: ${ctx.ema_20:.2f}  |  Alignment: {ctx.ema_alignment:+.2f}")
        lines.append(f"Current bar: O={ctx.current_open:.2f} H={ctx.current_high:.2f} L={ctx.current_low:.2f} C={ctx.current_close:.2f} V={ctx.current_volume:.0f}")
        lines.append("")

        lines.append("=== RECENT PERFORMANCE ===")
        lines.append(f"Last {ctx.recent_total_trades} trades: {ctx.recent_wins}W / {ctx.recent_losses}L ({ctx.recent_win_rate:.0%})")
        if ctx.recent_r_values:
            r_str = ", ".join(f"{r:+.2f}" for r in ctx.recent_r_values)
            lines.append(f"R-multiples: [{r_str}]")
        lines.append("")

        lines.append("=== ACCOUNT ===")
        lines.append(f"Equity: ${ctx.equity:.2f}  |  Peak: ${ctx.peak_equity:.2f}  |  DD: {ctx.current_drawdown_pct:.1%}")
        lines.append(f"Daily PnL: ${ctx.daily_pnl:+.2f}")

        return "\n".join(lines)

    def prompt_for_new_signal(self, ctx: MarketContext) -> str:
        """Generate prompt specifically at signal time (no position yet)."""
        base = self.context_to_prompt(ctx)
        decision_prompt = """

=== YOUR DECISION ===
A new Donchian breakout signal has fired. Decide:
1. TAKE or SKIP this trade
2. If TAKE: what risk percentage? (0.10% to 0.50%, default 0.25%)
3. If TAKE: what stop multiplier? (1.5x to 3.0x ATR, default 2.0x)
4. If TAKE: what take-profit multiplier? (2.0x to 4.0x ATR, default 2.0x)

Rules:
- Base risk is 0.25%. Adjust up if conditions are favorable (strong trend, low volatility, recent wins).
- Adjust down if conditions are unfavorable (ranging market, high volatility, recent losses).
- ADX > 25 suggests trending conditions — may justify higher risk.
- ADX < 20 suggests ranging — reduce risk or skip.
- ATR percentile > 0.8 means very high volatility — tighten stops.
- ATR percentile < 0.2 means very low volatility — normal stops.
- Never exceed 1.0% risk on any single trade.
- If equity DD > 5%, reduce all risk by 50%.

Return your decision as JSON only (no other text):
{
  "action": "take" | "skip",
  "reason": "brief explanation",
  "risk_pct": 0.25,
  "stop_mult": 2.0,
  "tp_mult": 2.0
}"""
        return base + decision_prompt

    def prompt_for_position_management(self, ctx: MarketContext) -> str:
        """Generate prompt for managing an existing open position."""
        base = self.context_to_prompt(ctx)
        decision_prompt = """

=== YOUR DECISION ===
You have an open position. Decide what to do:
1. HOLD with current stop/target
2. Move stop to BREAKEVEN (price entered +/- spread)
3. CLOSE HALF at current price, trail the rest
4. CLOSE ALL at current price

Guidelines:
- If R > 0.5 and ADX dropping: consider moving stop to breakeven
- If R > 1.0 and ADX still strong: let it run (hold)
- If R > 1.5: consider trailing stop or partial close to lock profits
- If R < -0.5: this trade is going badly — consider early exit
- If held > 96 bars without resolution: close at market
- Consecutive losses may warrant tighter stops
- Consecutive wins may warrant letting runners run

Return your decision as JSON only (no other text):
{
  "action": "hold" | "breakeven" | "close_half" | "close_all",
  "reason": "brief explanation",
  "adjusted_stop": null | price,
  "adjusted_target": null | price
}"""
        return base + decision_prompt


__all__ = ["ContextBuilder", "MarketContext"]
