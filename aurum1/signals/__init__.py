"""Core signal types for AURUM-1.

CandleRow and TradeInstruction are the shared types used by the live
trading pipeline, risk manager, and broker. MachineMode and MachineState
are retained for backtest compatibility.

The original pullback-breakout StateMachine has been archived — see
archive/aurum1/phase_s4_shadow_decision_candidate_lock.py and related.
"""

from enum import Enum

from aurum1.signals._legacy_compat import CandleRow, TradeInstruction


class MachineMode(Enum):
    """Operating modes used by validation ablations."""

    RULE_ONLY = "rule_only"
    RULE_REGIME = "rule_regime"
    RULE_REGIME_SENT = "rule_regime_sent"
    FULL_ENSEMBLE = "full_ensemble"


class MachineState(Enum):
    """State-machine phases (retained for backtest compatibility)."""

    SCANNING = "SCANNING"
    ARMED = "ARMED"
    WINDOW_OPEN = "WINDOW_OPEN"
    BLACKOUT = "BLACKOUT"


__all__ = ["CandleRow", "MachineMode", "MachineState", "TradeInstruction"]
