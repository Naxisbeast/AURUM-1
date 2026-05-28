"""Instrument unit conventions for AURUM-1.

XAU_USD convention used by this project:
- OANDA's XAU_USD price is USD per troy ounce of gold.
- One OANDA unit is treated as one troy ounce of exposure.
- A +1.00 price move on one BUY unit therefore produces +1.00 USD P&L.
- ``pip_size`` is 0.01, so one pip on one unit is 0.01 USD.
- A project "lot" is a sizing convenience, not an OANDA order unit. For
  XAU_USD the default is 100 OANDA units per lot, so one pip per lot is 1 USD.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InstrumentSpec:
    name: str
    oanda_instrument: str
    account_currency: str
    pip_size: float
    ounces_per_unit: float
    units_per_lot: float
    min_units: float
    max_units: float
    unit_precision: int
    min_lot_size: float
    max_lot_size: float
    lot_step: float

    @classmethod
    def from_settings(cls, settings: dict[str, Any], instrument: str | None = None) -> "InstrumentSpec":
        broker_instrument = str(settings.get("broker", {}).get("oanda", {}).get("instrument", "XAU_USD"))
        name = instrument or broker_instrument
        instruments = settings.get("instruments", {})
        raw = dict(instruments.get(name, instruments.get("XAU_USD", {})))
        risk = settings.get("risk", {})
        return cls(
            name=name,
            oanda_instrument=str(raw.get("oanda_instrument", broker_instrument)),
            account_currency=str(raw.get("account_currency", "USD")),
            pip_size=float(raw.get("pip_size", risk.get("pip_size", 0.01))),
            ounces_per_unit=float(raw.get("ounces_per_unit", 1.0)),
            units_per_lot=float(raw.get("units_per_lot", 100.0)),
            min_units=float(raw.get("min_units", 1.0)),
            max_units=float(raw.get("max_units", risk.get("max_lot_size", 10.0) * float(raw.get("units_per_lot", 100.0)))),
            unit_precision=int(raw.get("unit_precision", 0)),
            min_lot_size=float(raw.get("min_lot_size", risk.get("min_lot_size", 0.01))),
            max_lot_size=float(raw.get("max_lot_size", risk.get("max_lot_size", 10.0))),
            lot_step=float(raw.get("lot_step", risk.get("lot_step", 0.01))),
        )

    @property
    def pip_value_per_unit(self) -> float:
        return self.pip_size * self.ounces_per_unit

    @property
    def pip_value_per_lot(self) -> float:
        return self.pip_value_per_unit * self.units_per_lot

    def lots_to_units(self, lots: float) -> float:
        return self.round_units(float(lots) * self.units_per_lot)

    def units_to_lots(self, units: float) -> float:
        return float(units) / self.units_per_lot

    def round_lots(self, lots: float) -> float:
        clamped = max(self.min_lot_size, min(self.max_lot_size, float(lots)))
        step = self.lot_step
        if step <= 0.0:
            return clamped
        decimals = max(0, int(round(-math.log10(step)))) if step < 1.0 else 0
        return round(math.floor((clamped / step) + 0.5) * step, decimals)

    def round_units(self, units: float) -> float:
        clamped = max(self.min_units, min(self.max_units, float(units)))
        return round(clamped, self.unit_precision)

    def format_units(self, units: float) -> str:
        rounded = self.round_units(units)
        if self.unit_precision <= 0:
            return str(int(rounded))
        return f"{rounded:.{self.unit_precision}f}"

    def risk_to_units(self, risk_amount: float, entry_price: float, stop_loss: float) -> float:
        stop_distance = abs(float(entry_price) - float(stop_loss))
        if stop_distance <= 0.0 or self.ounces_per_unit <= 0.0:
            return self.min_units
        return self.round_units(float(risk_amount) / (stop_distance * self.ounces_per_unit))

    def pnl(self, direction: str, entry_price: float, exit_price: float, units: float) -> float:
        delta = float(exit_price) - float(entry_price)
        signed = delta if direction == "BUY" else -delta
        return signed * float(units) * self.ounces_per_unit


__all__ = ["InstrumentSpec"]
