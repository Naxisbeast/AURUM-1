"""Phase 5 risk management engine for AURUM-1.

The risk manager consumes a Phase 4 TradeInstruction and current account state.
It performs sizing and kill-switch checks without calling models, features, or
the state machine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean
from typing import Any

from aurum1.instruments import InstrumentSpec
from aurum1.signals import TradeInstruction


@dataclass(frozen=True)
class AccountState:
    equity: float
    balance: float
    open_trade_count: int
    daily_pnl: float
    peak_equity_30d: float
    current_spread_pips: float
    open_risk_pct: float


@dataclass
class RiskOrder:
    instruction: TradeInstruction
    lot_size: float
    risk_amount: float
    risk_pct: float
    kelly_fraction: float
    approved: bool
    rejection_reason: str | None
    portfolio_risk_after: float
    recovery_mode: bool = False
    warnings: list[str] = field(default_factory=list)
    units: float = 0.0
    notional_ounces: float = 0.0


class RiskManager:
    """Apply AURUM-1 risk sizing, kill switches, and advisory warnings."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.risk_settings = settings.get("risk", {})
        self.instrument = InstrumentSpec.from_settings(settings)

    def evaluate(
        self,
        instruction: TradeInstruction,
        account: AccountState,
        trade_history: list[dict[str, Any]],
    ) -> RiskOrder:
        """Return a sized order or a rejected risk decision."""

        equity = max(float(account.equity), 1e-12)
        base_risk_amount = equity * self._setting("risk_per_trade_pct", 0.01)
        kelly_fraction = self._kelly_fraction(trade_history)
        adjusted_risk = base_risk_amount * kelly_fraction
        initial_projected = account.open_risk_pct + (adjusted_risk / equity * 100.0)
        warnings = self._base_warnings(instruction, account, kelly_fraction)

        rejection_reason: str | None = None
        recovery_mode = False

        if account.current_spread_pips > self._setting("max_spread_pips", 3.0):
            rejection_reason = "spread_too_wide"
        elif account.daily_pnl < -(equity * self._setting("daily_loss_kill_pct", 0.03)):
            rejection_reason = "daily_loss_kill"
        elif equity < account.peak_equity_30d * (1.0 - self._setting("total_drawdown_kill_pct", 0.08)):
            rejection_reason = "total_drawdown_kill"
        elif initial_projected > self._setting("max_portfolio_risk_pct", 3.0):
            rejection_reason = "portfolio_risk_limit"
        elif self._has_regime_conflict(instruction):
            rejection_reason = "regime_correlation_conflict"
        elif equity < account.peak_equity_30d * (1.0 - self._setting("drawdown_recovery_threshold_pct", 0.05)):
            adjusted_risk *= 0.5
            recovery_mode = True
            warnings.append("recovery_mode_active")

        lot_size, units = self._position_size(instruction, adjusted_risk)
        risk_amount = self._risk_amount(instruction, units)
        risk_pct = risk_amount / equity * 100.0
        portfolio_risk_after = account.open_risk_pct + risk_pct
        if 2.0 < portfolio_risk_after < self._setting("max_portfolio_risk_pct", 3.0):
            warnings.append("portfolio_risk_elevated")

        return RiskOrder(
            instruction=instruction,
            lot_size=lot_size,
            risk_amount=risk_amount,
            risk_pct=risk_pct,
            kelly_fraction=kelly_fraction,
            approved=rejection_reason is None,
            rejection_reason=rejection_reason,
            portfolio_risk_after=portfolio_risk_after,
            recovery_mode=recovery_mode,
            warnings=_dedupe(warnings),
            units=units,
            notional_ounces=units * self.instrument.ounces_per_unit,
        )

    def _kelly_fraction(self, trade_history: list[dict[str, Any]]) -> float:
        if len(trade_history) < int(self._setting("kelly_min_trades", 20)):
            return float(self._setting("kelly_default_fraction", 0.25))

        wins = [float(trade["pnl"]) for trade in trade_history if float(trade.get("pnl", 0.0)) > 0.0]
        losses = [float(trade["pnl"]) for trade in trade_history if float(trade.get("pnl", 0.0)) <= 0.0]
        win_rate = len(wins) / len(trade_history)
        avg_win = mean(wins) if wins else 0.0
        avg_loss = abs(mean(losses)) if losses else 1.0
        win_loss_ratio = avg_win / avg_loss if avg_loss > 0.0 else 1.0
        full_kelly = 0.0 if win_loss_ratio <= 0.0 else win_rate - (1.0 - win_rate) / win_loss_ratio
        full_kelly = max(0.0, full_kelly)
        return float(
            min(
                full_kelly * self._setting("kelly_cap", 0.25),
                self._setting("kelly_max_fraction", 0.25),
            )
        )

    def _position_size(self, instruction: TradeInstruction, adjusted_risk: float) -> tuple[float, float]:
        sl_distance = abs(float(instruction.entry_price) - float(instruction.stop_loss))
        if sl_distance <= 0.0 or self.instrument.ounces_per_unit <= 0.0:
            raw_units = self.instrument.min_units
        else:
            raw_units = float(adjusted_risk) / (sl_distance * self.instrument.ounces_per_unit)
        raw_lots = self.instrument.units_to_lots(raw_units)
        lots = self.instrument.round_lots(raw_lots)
        units = self.instrument.lots_to_units(lots)
        return float(lots), float(units)

    def _risk_amount(self, instruction: TradeInstruction, units: float) -> float:
        sl_distance = abs(float(instruction.entry_price) - float(instruction.stop_loss))
        return float(sl_distance * units * self.instrument.ounces_per_unit)

    def _sl_pips(self, instruction: TradeInstruction) -> float:
        sl_distance = abs(float(instruction.entry_price) - float(instruction.stop_loss))
        return sl_distance / self.instrument.pip_size

    def _round_to_step(self, value: float, step: float) -> float:
        if step <= 0.0:
            return value
        decimals = max(0, int(round(-math.log10(step)))) if step < 1.0 else 0
        return round(math.floor((value / step) + 0.5) * step, decimals)

    def _has_regime_conflict(self, instruction: TradeInstruction) -> bool:
        if instruction.direction == "BUY" and instruction.regime == "TRENDING_DOWN":
            return True
        if instruction.direction == "SELL" and instruction.regime == "TRENDING_UP":
            return True
        return False

    def _base_warnings(
        self,
        instruction: TradeInstruction,
        account: AccountState,
        kelly_fraction: float,
    ) -> list[str]:
        warnings: list[str] = []
        if kelly_fraction < 0.10:
            warnings.append("kelly_below_default")
        if 2.0 < account.current_spread_pips < self._setting("max_spread_pips", 3.0):
            warnings.append("spread_elevated")
        if instruction.confidence < 0.65:
            warnings.append("low_confidence")
        return warnings

    def _setting(self, key: str, default: Any) -> Any:
        return self.risk_settings.get(key, default)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


__all__ = ["AccountState", "RiskManager", "RiskOrder"]
