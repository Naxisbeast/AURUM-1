"""Phase 4 signal state machine for AURUM-1.

The state machine consumes already-computed candle features and upstream model
signals. It does not call FeatureEngineer or any model directly.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aurum1.models.ensemble import SignalResult
from aurum1.signals import MachineMode, MachineState


@dataclass(frozen=True)
class CandleRow:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    atr_14: float
    adx_14: float
    ema_9: float
    ema_20: float
    session_london: int
    session_ny: int
    session_overlap: int


@dataclass(frozen=True)
class TradeInstruction:
    timestamp: datetime
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    atr_at_entry: float
    signal_score: float
    regime: str
    confidence: float
    machine_mode: str
    state_machine_version: str = "1.0"


class StateMachine:
    """Mode-aware pullback-breakout state machine."""

    def __init__(
        self,
        settings: dict[str, Any],
        mode: MachineMode = MachineMode.RULE_REGIME,
    ) -> None:
        self.settings = settings
        self.mode = mode
        self.signal_settings = settings.get("signals", {})
        self.state = MachineState.SCANNING
        self.armed_direction: str | None = None
        self.armed_signal: SignalResult | None = None
        self.armed_candle: CandleRow | None = None
        self.pullback_count = 0
        self.armed_candle_count = 0
        self.window_candle_count = 0
        self.breakout_level: float | None = None
        self.cancellation_log: list[dict[str, Any]] = []
        self._blackout_logged_active = False

    def on_candle(
        self,
        candle: CandleRow,
        signal: SignalResult,
        is_blackout: bool,
    ) -> TradeInstruction | None:
        """Advance the state machine by one candle and possibly emit a trade."""

        if is_blackout:
            if self.state == MachineState.SCANNING and not self._blackout_logged_active:
                self._log_cancellation(candle, "blackout_entry_blocked", signal=signal)
                self._blackout_logged_active = True
            return None
        self._blackout_logged_active = False

        if self.state == MachineState.SCANNING:
            return self._handle_scanning(candle, signal)
        if self.state == MachineState.ARMED:
            return self._handle_armed(candle)
        if self.state == MachineState.WINDOW_OPEN:
            return self._handle_window_open(candle)
        return None

    def reset(self) -> None:
        """Force reset to SCANNING, clearing active trade setup state."""

        self.state = MachineState.SCANNING
        self.armed_direction = None
        self.armed_signal = None
        self.armed_candle = None
        self.pullback_count = 0
        self.armed_candle_count = 0
        self.window_candle_count = 0
        self.breakout_level = None
        self._blackout_logged_active = False

    def get_state(self) -> MachineState:
        return self.state

    def get_cancellation_summary(self) -> dict[str, int]:
        return dict(Counter(entry["reason"] for entry in self.cancellation_log))

    def get_cancellation_log(self) -> list[dict[str, Any]]:
        return list(self.cancellation_log)

    def _handle_scanning(self, candle: CandleRow, signal: SignalResult) -> TradeInstruction | None:
        direction = self._resolve_direction(candle, signal)
        if direction is None:
            return None
        if candle.adx_14 <= self._setting("adx_threshold", 25.0):
            return None
        if not self._ema_matches_direction(candle, direction):
            return None
        if self._setting("require_session_filter", True) and not (candle.session_london == 1 or candle.session_ny == 1):
            return None

        self.state = MachineState.ARMED
        self.armed_direction = direction
        self.armed_signal = signal
        self.armed_candle = candle
        self.pullback_count = 0
        self.armed_candle_count = 0
        self.window_candle_count = 0
        self.breakout_level = None
        return None

    def _handle_armed(self, candle: CandleRow) -> TradeInstruction | None:
        self.armed_candle_count += 1
        is_pullback = self._is_pullback(candle)
        if is_pullback:
            self.pullback_count += 1

        if self.pullback_count > self._setting("max_pullback_candles", 4):
            self._log_cancellation(candle, "max_pullbacks_exceeded")
            self.reset()
            return None

        if self.armed_candle_count > self._setting("armed_timeout_candles", 20):
            self._log_cancellation(candle, "armed_timeout")
            self.reset()
            return None

        if self.pullback_count >= self._setting("min_pullback_candles", 1) and not is_pullback:
            self._open_window()
        return None

    def _handle_window_open(self, candle: CandleRow) -> TradeInstruction | None:
        self.window_candle_count += 1
        if self.window_candle_count > self._setting("window_expiry_candles", 6):
            self._log_cancellation(candle, "window_expired")
            self.reset()
            return None

        if self.breakout_level is None or self.armed_direction is None or self.armed_signal is None:
            self.reset()
            return None

        if self.armed_direction == "BUY" and candle.close > self.breakout_level:
            instruction = self._trade_instruction(candle, "BUY")
            self.reset()
            return instruction
        if self.armed_direction == "SELL" and candle.close < self.breakout_level:
            instruction = self._trade_instruction(candle, "SELL")
            self.reset()
            return instruction
        return None

    def _resolve_direction(self, candle: CandleRow, signal: SignalResult) -> str | None:
        if self.mode == MachineMode.RULE_ONLY:
            if candle.ema_9 > candle.ema_20:
                return "BUY"
            if candle.ema_9 < candle.ema_20:
                return "SELL"
            return None

        direction = signal.direction if signal.direction in {"BUY", "SELL"} else None
        if direction is None:
            return None
        if self.mode in {MachineMode.RULE_REGIME, MachineMode.RULE_REGIME_SENT}:
            if direction == "BUY" and signal.regime == "TRENDING_DOWN":
                return None
            if direction == "SELL" and signal.regime == "TRENDING_UP":
                return None
            if self.mode == MachineMode.RULE_REGIME_SENT and abs(signal.sentiment_scalar) < 0.1:
                return None
        return direction

    def _ema_matches_direction(self, candle: CandleRow, direction: str) -> bool:
        if direction == "BUY":
            return candle.ema_9 > candle.ema_20
        if direction == "SELL":
            return candle.ema_9 < candle.ema_20
        return False

    def _is_pullback(self, candle: CandleRow) -> bool:
        if self.armed_direction == "BUY":
            return candle.close < candle.open
        if self.armed_direction == "SELL":
            return candle.close > candle.open
        return False

    def _open_window(self) -> None:
        if self.armed_candle is None or self.armed_direction is None:
            return
        buffer = self._setting("atr_breakout_buffer", 0.3) * self.armed_candle.atr_14
        if self.armed_direction == "BUY":
            self.breakout_level = self.armed_candle.high + buffer
        else:
            self.breakout_level = self.armed_candle.low - buffer
        self.window_candle_count = 0
        self.state = MachineState.WINDOW_OPEN

    def _trade_instruction(self, candle: CandleRow, direction: str) -> TradeInstruction:
        if self.breakout_level is None or self.armed_signal is None:
            raise RuntimeError("Cannot create TradeInstruction without an active breakout setup")
        entry_price = float(self.breakout_level)
        sl_mult = self._setting("atr_sl_multiplier", 2.0)
        tp_mult = self._setting("atr_tp_multiplier", 3.0)
        if direction == "BUY":
            stop_loss = entry_price - sl_mult * candle.atr_14
            take_profit = entry_price + tp_mult * candle.atr_14
        else:
            stop_loss = entry_price + sl_mult * candle.atr_14
            take_profit = entry_price - tp_mult * candle.atr_14
        return TradeInstruction(
            timestamp=candle.timestamp,
            direction=direction,
            entry_price=entry_price,
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            atr_at_entry=float(candle.atr_14),
            signal_score=float(self.armed_signal.raw_score),
            regime=self.armed_signal.regime,
            confidence=float(self.armed_signal.regime_confidence),
            machine_mode=self.mode.value,
        )

    def _log_cancellation(
        self,
        candle: CandleRow,
        reason: str,
        *,
        signal: SignalResult | None = None,
    ) -> None:
        active_signal = signal or self.armed_signal
        self.cancellation_log.append(
            {
                "timestamp": candle.timestamp.isoformat(),
                "from_state": self.state.value,
                "reason": reason,
                "armed_direction": self.armed_direction,
                "signal_score": float(active_signal.raw_score) if active_signal is not None else None,
                "machine_mode": self.mode.value,
            }
        )

    def _setting(self, key: str, default: Any) -> Any:
        return self.signal_settings.get(key, default)


__all__ = ["CandleRow", "StateMachine", "TradeInstruction"]
