"""Signal-state-machine shared types for AURUM-1."""

from enum import Enum


class MachineMode(Enum):
    """Operating modes used by Phase 4 state machine and validation ablations."""

    RULE_ONLY = "rule_only"
    RULE_REGIME = "rule_regime"
    RULE_REGIME_SENT = "rule_regime_sent"
    FULL_ENSEMBLE = "full_ensemble"


class MachineState(Enum):
    """State-machine phases for the pullback-breakout entry workflow."""

    SCANNING = "SCANNING"
    ARMED = "ARMED"
    WINDOW_OPEN = "WINDOW_OPEN"
    BLACKOUT = "BLACKOUT"


from aurum1.signals.state_machine import CandleRow, StateMachine, TradeInstruction


__all__ = ["CandleRow", "MachineMode", "MachineState", "StateMachine", "TradeInstruction"]
