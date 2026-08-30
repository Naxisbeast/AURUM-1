"""Compatibility stubs for archived ML/state-machine code.

These exist so that `backtesting/engine.py`, `walk_forward.py`, and a small
number of test files can still import `SignalResult` and `REGIME_LABELS`
without depending on the retired ML model package (archived after research
showed the ML layers added no edge) and the rejected state machine.

Once `engine.py` and `walk_forward.py` are themselves refactored to not
reference these, this file can be deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CandleRow:
    """A single OHLCV row with derived features, used as the broker price feed."""
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
    """A signal from the strategy layer that the risk manager evaluates."""
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


REGIME_LABELS: dict[int, str] = {
    0: "TRENDING_UP",
    1: "TRENDING_DOWN",
    2: "RANGING",
}


class RegimeClassifierStub:
    """Minimal stub preserving the static method used by backtesting/engine.py.

    The real RegimeClassifier was retired with the ML model package. This stub
    exists solely so engine.py can resolve the type annotation and the
    generate_labels() call when disable_ml=True (the production default).
    """

    model: Any = None

    @staticmethod
    def generate_labels(feature_frame: Any) -> Any:
        import pandas as pd
        return pd.Series([0])


@dataclass
class SignalResult:
    """Final ensemble signal and component evidence.

    Kept as a stub to preserve the interface used by backtesting/engine.py.
    """
    direction: str
    raw_score: float
    regime: str
    regime_confidence: float
    direction_signal: float
    sentiment_scalar: float
    timestamp: datetime
