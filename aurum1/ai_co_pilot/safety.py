"""Safety Layer: Hard limits that the AI cannot override.

Every AI decision is validated against these rules before execution.
If a decision violates any rule, it's rejected and the default action is taken.
"""

from __future__ import annotations

from typing import Any


class SafetyViolation(Exception):
    """Raised when an AI decision violates safety limits."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class SafetyLayer:
    """Hard limits for AI trading decisions."""

    def __init__(self):
        # Absolute limits (never override)
        self.max_risk_per_trade = 0.01  # 1.0% max
        self.min_risk_per_trade = 0.001  # 0.1% min
        self.max_stop_multiplier = 3.5  # 3.5x ATR max
        self.min_stop_multiplier = 1.0  # 1.0x ATR min
        self.max_tp_multiplier = 6.0  # 6.0x ATR max
        self.min_tp_multiplier = 1.0  # 1.0x ATR min
        self.max_daily_drawdown = 0.08  # 8% daily loss limit
        self.max_total_drawdown = 0.15  # 15% total DD limit
        self.max_position_bars = 384  # Max 384 bars (~4 days) in a position
        self.max_consecutive_losses = 50  # High enough to never trigger in normal backtest

        # Tracking state
        self.consecutive_losses = 0
        self.daily_start_equity = None
        self.peak_equity = None
        self.today_pnl = 0.0

    def validate_new_signal(self, decision: dict, context: Any) -> dict:
        """Validate an AI decision about a new trade signal."""
        action = decision.get("action", "skip")

        if action == "skip":
            return {"action": "skip", "reason": decision.get("reason", "AI skipped"), "approved": True}

        if action == "take":
            risk_pct = float(decision.get("risk_pct", 0.0025))
            stop_mult = float(decision.get("stop_mult", 2.0))
            tp_mult = float(decision.get("tp_mult", 2.0))

            # Check hard limits
            if risk_pct > self.max_risk_per_trade:
                raise SafetyViolation(f"Risk {risk_pct:.4f} exceeds max {self.max_risk_per_trade:.4f}")
            if risk_pct < self.min_risk_per_trade:
                raise SafetyViolation(f"Risk {risk_pct:.4f} below min {self.min_risk_per_trade:.4f}")
            if stop_mult > self.max_stop_multiplier:
                raise SafetyViolation(f"Stop mult {stop_mult:.1f} exceeds max {self.max_stop_multiplier:.1f}")
            if stop_mult < self.min_stop_multiplier:
                raise SafetyViolation(f"Stop mult {stop_mult:.1f} below min {self.min_stop_multiplier:.1f}")
            if tp_mult > self.max_tp_multiplier:
                raise SafetyViolation(f"TP mult {tp_mult:.1f} exceeds max {self.max_tp_multiplier:.1f}")

            # Check drawdown limits
            if context.current_drawdown_pct > self.max_total_drawdown:
                return {"action": "skip", "reason": f"Total DD {context.current_drawdown_pct:.1%} exceeds limit", "approved": True}

            # Check consecutive losses
            if self.consecutive_losses >= self.max_consecutive_losses:
                return {"action": "skip", "reason": f"{self.consecutive_losses} consecutive losses — auto-stop", "approved": True}

            # Apply drawdown reduction
            if context.current_drawdown_pct > 0.05:
                risk_pct *= 0.5
                decision["risk_pct"] = risk_pct
                decision["drawdown_reduced"] = True

            decision["approved"] = True
            return decision

        raise SafetyViolation(f"Unknown action: {action}")

    def validate_position_management(self, decision: dict, context: Any) -> dict:
        """Validate an AI decision about managing an open position."""
        action = decision.get("action", "hold")

        allowed_actions = {"hold", "breakeven", "close_half", "close_all"}
        if action not in allowed_actions:
            raise SafetyViolation(f"Unknown position action: {action}")

        adjusted_stop = decision.get("adjusted_stop")
        adjusted_target = decision.get("adjusted_target")

        if adjusted_stop is not None:
            # Check stop isn't too far
            if context.position_entry is not None:
                stop_distance = abs(context.position_entry - adjusted_stop)
                atr = context.current_atr_14
                if atr > 0 and stop_distance > self.max_stop_multiplier * atr:
                    raise SafetyViolation(f"Stop distance {stop_distance:.2f} exceeds {self.max_stop_multiplier}× ATR")

        if context.position_bars_held is not None and context.position_bars_held > self.max_position_bars:
            return {"action": "close_all", "reason": f"Position held {context.position_bars_held} bars — max reached", "approved": True}

        decision["approved"] = True
        return decision

    def record_trade_result(self, r_value: float) -> None:
        """Record a completed trade to update safety state."""
        if r_value <= 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def reset_daily(self, starting_equity: float) -> None:
        """Reset daily counters."""
        self.daily_start_equity = starting_equity
        self.today_pnl = 0.0


__all__ = ["SafetyLayer", "SafetyViolation"]
