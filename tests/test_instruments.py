"""Unit tests for InstrumentSpec — unit conventions and rounding."""
from __future__ import annotations

from typing import Any

import pytest

from aurum1.instruments import InstrumentSpec


def _settings(**overrides: Any) -> dict[str, Any]:
    base = {
        "broker": {"oanda": {"instrument": "XAU_USD"}},
        "instruments": {
            "XAU_USD": {
                "oanda_instrument": "XAU_USD",
                "account_currency": "USD",
                "pip_size": 0.01,
                "ounces_per_unit": 1.0,
                "units_per_lot": 100.0,
                "min_units": 1.0,
                "max_units": 1000.0,
                "unit_precision": 0,
                "min_lot_size": 0.01,
                "max_lot_size": 10.0,
                "lot_step": 0.01,
            }
        },
        "risk": {"pip_size": 0.01, "min_lot_size": 0.01, "max_lot_size": 10.0, "lot_step": 0.01},
    }
    base.update(overrides)
    return base


def test_xau_usd_pip_value():
    spec = InstrumentSpec.from_settings(_settings())
    assert spec.pip_value_per_unit == pytest.approx(0.01)


def test_pip_value_per_lot():
    spec = InstrumentSpec.from_settings(_settings())
    assert spec.pip_value_per_lot == pytest.approx(1.0)


def test_round_lots_standard():
    spec = InstrumentSpec.from_settings(_settings())
    assert spec.round_lots(1.234) == pytest.approx(1.23)  # step=0.01


def test_round_lots_clamped_to_min():
    spec = InstrumentSpec.from_settings(_settings())
    assert spec.round_lots(0.001) == pytest.approx(0.01)  # min_lot_size


def test_round_lots_clamped_to_max():
    spec = InstrumentSpec.from_settings(_settings())
    assert spec.round_lots(99.0) == pytest.approx(10.0)  # max_lot_size


def test_pnl_buy():
    spec = InstrumentSpec.from_settings(_settings())
    # (exit - entry) * units * ounces_per_unit = (105 - 100) * 10 * 1 = 50
    assert spec.pnl("BUY", 100.0, 105.0, 10.0) == pytest.approx(50.0)


def test_pnl_sell():
    spec = InstrumentSpec.from_settings(_settings())
    # -(exit - entry) * units * ounces_per_unit = -(95-100) * 10 * 1 = 50
    assert spec.pnl("SELL", 100.0, 95.0, 10.0) == pytest.approx(50.0)


def test_units_to_lots():
    spec = InstrumentSpec.from_settings(_settings())
    assert spec.units_to_lots(250.0) == pytest.approx(2.5)


def test_lots_to_units():
    spec = InstrumentSpec.from_settings(_settings())
    assert spec.lots_to_units(2.5) == pytest.approx(250.0)


def test_risk_to_units():
    spec = InstrumentSpec.from_settings(_settings())
    # risk_amount / (stop_distance * ounces_per_unit) = 25 / (5 * 1) = 5
    units = spec.risk_to_units(25.0, 100.0, 95.0)
    assert units == 5.0


def test_risk_to_units_zero_stop_distance():
    """Returns min_units when stop distance is zero to prevent division by zero."""
    spec = InstrumentSpec.from_settings(_settings())
    units = spec.risk_to_units(25.0, 100.0, 100.0)  # zero stop distance
    assert units == spec.min_units


def test_format_units_buy():
    spec = InstrumentSpec.from_settings(_settings())
    assert spec.format_units(5.0) == "5"
    assert spec.format_units(-5.0) == "-5"
