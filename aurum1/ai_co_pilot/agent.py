"""AI Agent: the decision-making layer for AURUM-1.

This module integrates with an LLM (via API) to make context-aware trading
decisions. It constructs a system prompt defining the strategy rules, sends
the current market context, and parses the AI's structured response.

If the API is unavailable, it falls back to rule-based defaults.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pandas as pd
from aurum1.ai_co_pilot.context import MarketContext
from aurum1.ai_co_pilot.safety import SafetyLayer, SafetyViolation

SYSTEM_PROMPT = """You are AURUM-1's strategy manager — the trading co-pilot for a gold (XAU/USD) M15 trend-following system.

## Strategy Rules
- Core signal: 10-bar Donchian channel breakout (BUY above channel high, SELL below channel low)
- Entry: At next candle's open after the signal
- Exit: Fixed 2R take-profit (2× risk) and 2R stop-loss
- Base risk: 0.25% of equity per trade
- Instrument: XAU/USD spot via OANDA
- Spread: ~1.5 pips, Slippage: ~0.5 pips

## Your Role
You monitor market conditions and make intelligent decisions about:
1. Whether to take a signal or skip it
2. What position size to use (0.10% to 0.50% of equity)
3. Where to place stops (1.5× to 3.0× ATR)
4. Where to set take-profits (2.0× to 4.0× ATR)
5. When to move stops to breakeven
6. When to take partial profits
7. When to close a position early

## Decision Framework

### When a NEW SIGNAL appears:
- ADX > 25 (strong trend): Full or increased risk. Take the trade.
- ADX 20-25 (moderate trend): Normal risk. Take the trade.
- ADX < 20 (weak/ranging): Reduced risk or skip. Trend-following works poorly in ranging markets.
- ATR percentile > 0.8 (very high vol): Tighten stop to 1.5-1.8x. Reduce size.
- ATR percentile < 0.2 (very low vol): Normal settings. The breakout may have more room.
- Recent 3+ consecutive wins: You may be in a hot streak — increase size slightly.
- Recent 3+ consecutive losses: Conditions may have changed — reduce size or skip.
- H1 trend against signal direction: Be cautious. The higher timeframe may invalidate the move.
- DD > 5%: Reduce all risk by half.

### When MANAGING an OPEN POSITION:
- R < -0.7 and ADX dropping: Consider early exit. The trade isn't working.
- R > 0.5 and ADX dropping: Move stop to breakeven to lock in a risk-free trade.
- R > 1.0 and ADX still strong: Let it run. Don't interfere with winners.
- R > 1.5: Consider trailing stop or close 50% to lock profit.
- Held > 96 bars without resolution: The breakout failed. Close at market.
- Held > 192 bars in any case: Max holding period reached. Close.

## Output Format
Always respond with ONLY a JSON object. No explanations, no markdown, no conversation."""


class AiAgent:
    """Interface to the AI decision-making layer.

    Can operate in two modes:
    - API mode: calls the LLM API (requires ANTHROPIC_API_KEY set)
    - Fallback mode: returns rule-based default decisions
    """

    def __init__(self, safety: SafetyLayer | None = None, api_key: str | None = None):
        self.safety = safety or SafetyLayer()
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._client = None
        self._api_available = self.api_key is not None

    @property
    def api_available(self) -> bool:
        return self._api_available

    def decide_on_signal(self, context: MarketContext) -> dict:
        """Decide whether to take a new signal and with what parameters."""
        if self._api_available:
            return self._ask_ai(context, "new_signal")
        return self._fallback_new_signal(context)

    def decide_on_position(self, context: MarketContext) -> dict:
        """Decide how to manage an existing open position."""
        if self._api_available:
            return self._ask_ai(context, "position_management")
        return self._fallback_position_management(context)

    def _ask_ai(self, context: MarketContext, decision_type: str) -> dict:
        """Call the LLM API for a trading decision."""
        from aurum1.ai_co_pilot.context import ContextBuilder

        cb = ContextBuilder()
        if decision_type == "new_signal":
            user_prompt = cb.prompt_for_new_signal(context)
        else:
            user_prompt = cb.prompt_for_position_management(context)

        try:
            import anthropic

            if self._client is None:
                self._client = anthropic.Anthropic(api_key=self.api_key)

            response = self._client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0.1,
            )

            text = response.content[0].text
            # Parse JSON from response
            decision = self._parse_json_response(text)
            return decision

        except ImportError:
            print("  [AI] anthropic not installed, falling back to rules")
            self._api_available = False
        except Exception as e:
            print(f"  [AI] API call failed: {e}, falling back to rules")
            self._api_available = False

        # Fallback
        if decision_type == "new_signal":
            return self._fallback_new_signal(context)
        return self._fallback_position_management(context)

    def _parse_json_response(self, text: str) -> dict:
        """Extract JSON from the AI's response, handling common formatting."""
        # Try direct parse
        text = text.strip()
        if text.startswith("{"):
            return json.loads(text)

        # Try to find JSON block
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])

        # Try markdown code block
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                return json.loads(text[start:end].strip())

        raise ValueError(f"No JSON found in response: {text[:200]}")

    def _fallback_new_signal(self, context: MarketContext) -> dict:
        """ADX-aware rule-based decision.

        Uses ADX to filter trades — only take breakouts when there's
        sufficient trend strength. This is exactly what we walk-forward
        tested earlier (ADX > 20), which showed 34% head-to-head wins
        vs the unfiltered baseline.

        Returns the SAME decision as the ADX > 20 filter we tested
        in Sweep 002, so we can verify the backtest matches.
        """
        adx = context.current_adx_14
        risk_pct = 0.0025
        stop_mult = 2.0
        tp_mult = 2.0

        # ADX filter: only trade when trend has some strength
        if pd.isna(adx) or adx < 20:
            return {
                "action": "skip",
                "reason": f"ADX={adx:.1f} < 20 — ranging market",
                "risk_pct": 0, "stop_mult": 0, "tp_mult": 0,
            }

        # Slight size adjustments within the ADX > 20 band
        if adx > 30:
            risk_pct = 0.0030  # Strong trend
        elif adx > 25:
            risk_pct = 0.0028  # Moderate trend

        # Drawdown protection
        if context.current_drawdown_pct > 0.05:
            risk_pct *= 0.5

        # Loss streak protection
        if context.recent_losses >= 4:
            risk_pct *= 0.5

        return {
            "action": "take",
            "reason": f"ADX={adx:.1f} > 20 — trending",
            "risk_pct": round(risk_pct, 4),
            "stop_mult": round(stop_mult, 1),
            "tp_mult": round(tp_mult, 1),
        }

    def _fallback_position_management(self, context: MarketContext) -> dict:
        """Rule-based position management — conservative, baseline-matching.

        The proven 10-bar + fixed 2R baseline simply holds every trade until
        stop or target is hit. The fallback should do the same unless there's
        a STRONG reason to deviate.
        """
        if context.position_current_r is None:
            return {"action": "hold", "reason": "No position info", "adjusted_stop": None, "adjusted_target": None}

        r = context.position_current_r
        adx = context.current_adx_14
        bars = context.position_bars_held or 0

        # Only close_all in truly extreme cases — matches baseline (let SL/TP work)
        if bars > 288:
            return {"action": "close_all", "reason": f"Held {bars} bars — time limit", "adjusted_stop": None, "adjusted_target": None}

        if r < -1.5:
            # Trade is deep in drawdown, almost certainly hitting SL anyway
            return {"action": "close_all", "reason": f"R={r:.2f} — deep in drawdown", "adjusted_stop": None, "adjusted_target": None}

        # Breakeven after good move (only if ADX confirms)
        if r > 0.8 and adx < 22:
            return {"action": "breakeven", "reason": f"R={r:.2f}, ADX={adx:.0f} — lock breakeven", "adjusted_stop": None, "adjusted_target": None}

        # Partial close on big winner
        if r > 2.5:
            return {"action": "close_half", "reason": f"R={r:.2f} — big winner, take some profit", "adjusted_stop": None, "adjusted_target": None}

        return {"action": "hold", "reason": f"R={r:.2f}, ADX={adx:.0f} — holding", "adjusted_stop": None, "adjusted_target": None}


__all__ = ["AiAgent"]
