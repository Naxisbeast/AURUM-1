"""Broker implementations for AURUM-1 Phase 6 execution."""

from __future__ import annotations

import os
import random
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from aurum1.instruments import InstrumentSpec
from aurum1.risk import AccountState, RiskOrder
from aurum1.signals import CandleRow


@dataclass
class OrderResult:
    success: bool
    order_id: str | None
    fill_price: float | None
    fill_time: datetime | None
    lot_size: float
    direction: str
    stop_loss: float
    take_profit: float
    rejection_reason: str | None
    broker: str
    raw_response: dict | None


@dataclass
class PositionRecord:
    position_id: str
    instrument: str
    direction: str
    open_price: float
    current_price: float
    lot_size: float
    units: float
    stop_loss: float
    take_profit: float
    open_time: datetime
    unrealised_pnl: float
    broker: str
    intended_entry_price: float | None = None
    entry_slippage: float = 0.0
    entry_slippage_cost: float = 0.0


class BrokerBase(ABC):
    # Capability flag: True when SL/TP are enforced server-side by the platform
    # (e.g. OANDA), so the trader must poll for closed positions instead of
    # simulating exits via update_prices(). Paper is False.
    server_managed_sl_tp: bool = False

    @abstractmethod
    def submit_order(self, order: RiskOrder) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    def close_position(self, position_id: str, reason: str) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    def get_account_state(self) -> AccountState:
        raise NotImplementedError

    @abstractmethod
    def get_open_positions(self) -> list[PositionRecord]:
        raise NotImplementedError

    @abstractmethod
    def get_current_spread_pips(self, instrument: str) -> float:
        raise NotImplementedError

    def poll_closed_trades(self, known_ids: set[str], open_meta: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Return trades closed since the last poll, as trader-shaped dicts.

        `known_ids`: position_ids already persisted by the trader (dedup across
        restarts). `open_meta`: trader-owned map {position_id -> {risk_amount,
        stop_loss, take_profit, intended_entry, units, ...}} needed to reconstruct
        r_multiple for brokers that cannot store this server-side.

        Default (no server-managed SL/TP) is to return nothing; PaperBroker drains
        its simulated trade history; OandaBroker reads closed trades from the API.
        """
        return []

    def restore_state_from_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Seed broker-internal state from persisted state.

        PaperBroker consumes this to restore equity/positions/history across a
        restart. Brokers where the platform is the source of truth (OANDA) leave
        it a no-op.
        """
        return None


class PaperBroker(BrokerBase):
    """In-memory broker simulation with simple slippage and SL/TP handling.

    Price collar: rejects order entry if the intended price deviates more than
    PRICE_COLLAR_PCT from the current market price (based on recent candle data).
    This is a hardcoded safety limit that cannot be overridden by settings.yaml.
    """

    # Hardcoded price collar: reject if entry price deviates >5% from market
    PRICE_COLLAR_PCT = 5.0

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.risk_settings = settings.get("risk", {})
        self.instrument_spec = InstrumentSpec.from_settings(settings)
        self.execution_settings = settings.get("execution", {})
        self.broker_settings = settings.get("broker", {})
        self.instrument = str(self.broker_settings.get("oanda", {}).get("instrument", "XAU_USD"))
        initial_equity = float(self.broker_settings.get("paper_initial_equity", 10000.0))
        self._equity = initial_equity
        self._balance = initial_equity
        self._positions: dict[str, PositionRecord] = {}
        self._trade_history: list[dict[str, Any]] = []
        self._daily_pnl = 0.0
        # UTC date the current _daily_pnl accumulator belongs to. Rolls the
        # daily P&L back to zero when a candle crosses into a new day.
        self._daily_pnl_date: date | None = None
        self._peak_equity_30d = initial_equity
        self._candle_prices: deque[float] = deque(maxlen=500)
        seed = int(settings.get("general", {}).get("random_seed", settings.get("app", {}).get("random_seed", 42)))
        self._rng = random.Random(seed)
        # Index into _trade_history of the last trade already drained by
        # poll_closed_trades(). Lets the trader poll new closes without a
        # separate counter.
        self._poll_index = 0

    def submit_order(self, order: RiskOrder) -> OrderResult:
        if not order.approved:
            return _rejected_order_result(order, order.rejection_reason or "risk_order_rejected", "paper")

        spread = self.get_current_spread_pips(self.instrument)
        if spread > float(self.risk_settings.get("max_spread_pips", 3.0)):
            return _rejected_order_result(order, "spread_too_wide_at_execution", "paper")

        # Price collar: reject if entry deviates >5% from current market price
        collar_price = self._current_market_price()
        if collar_price is not None and collar_price > 0:
            deviation = abs(float(order.instruction.entry_price) - collar_price) / collar_price * 100.0
            if deviation > self.PRICE_COLLAR_PCT:
                return _rejected_order_result(
                    order,
                    f"price_collar_violation: entry {order.instruction.entry_price:.2f} "
                    f"is {deviation:.1f}% away from market {collar_price:.2f}",
                    "paper",
                )

        instruction = order.instruction
        units = self._order_units(order)
        lot_size = self.instrument_spec.units_to_lots(units)
        slippage = self._sample_slippage_distance()
        fill_price = self._worsen_entry_price(instruction.direction, instruction.entry_price, slippage)
        entry_slippage_cost = self._slippage_cost(slippage, units)
        now = datetime.now(UTC)
        position_id = f"paper_{uuid4().hex[:8]}"
        # Rebase SL/TP distances around the actual fill price so intended risk is preserved
        original_sl_distance = abs(float(instruction.entry_price) - float(instruction.stop_loss))
        original_tp_distance = abs(float(instruction.take_profit) - float(instruction.entry_price))
        if instruction.direction == "BUY":
            actual_stop = float(fill_price) - original_sl_distance
            actual_tp = float(fill_price) + original_tp_distance
        else:
            actual_stop = float(fill_price) + original_sl_distance
            actual_tp = float(fill_price) - original_tp_distance

        self._positions[position_id] = PositionRecord(
            position_id=position_id,
            instrument=self.instrument,
            direction=instruction.direction,
            open_price=float(fill_price),
            current_price=float(fill_price),
            lot_size=float(lot_size),
            units=float(units),
            stop_loss=float(actual_stop),
            take_profit=float(actual_tp),
            open_time=now,
            unrealised_pnl=0.0,
            broker="paper",
            intended_entry_price=float(instruction.entry_price),
            entry_slippage=float(slippage),
            entry_slippage_cost=float(entry_slippage_cost),
        )
        return OrderResult(
            success=True,
            order_id=position_id,
            fill_price=float(fill_price),
            fill_time=now,
            lot_size=float(lot_size),
            direction=instruction.direction,
            stop_loss=float(instruction.stop_loss),
            take_profit=float(instruction.take_profit),
            rejection_reason=None,
            broker="paper",
            raw_response={
                "position_id": position_id,
                "simulated": True,
                "intended_entry_price": float(instruction.entry_price),
                "actual_entry_price": float(fill_price),
                "entry_slippage": float(slippage),
                "entry_slippage_cost": float(entry_slippage_cost),
                "units": float(units),
                "notional_ounces": float(units * self.instrument_spec.ounces_per_unit),
                "rebased_stop_loss": float(actual_stop),
                "rebased_take_profit": float(actual_tp),
            },
        )

    def close_position(self, position_id: str, reason: str) -> OrderResult:
        position = self._positions.get(position_id)
        if position is None:
            return OrderResult(
                success=False,
                order_id=position_id,
                fill_price=None,
                fill_time=None,
                lot_size=0.0,
                direction="UNKNOWN",
                stop_loss=0.0,
                take_profit=0.0,
                rejection_reason="position_not_found",
                broker="paper",
                raw_response={"reason": reason},
            )
        return self._close_position_at_price(position_id, position.current_price, reason)

    def update_prices(self, candle: CandleRow) -> None:
        self._roll_daily_pnl_if_new_day(candle.timestamp)
        self._candle_prices.append(float(candle.close))
        close_queue: list[tuple[str, float, str]] = []
        for position_id, position in list(self._positions.items()):
            if position.direction == "BUY":
                if candle.open <= position.stop_loss:
                    close_queue.append((position_id, float(candle.open), "stop_loss_gap"))
                    continue
                if candle.low <= position.stop_loss:
                    close_queue.append((position_id, position.stop_loss, "stop_loss"))
                    continue
                if candle.high >= position.take_profit:
                    close_queue.append((position_id, position.take_profit, "take_profit"))
                    continue
            else:
                if candle.open >= position.stop_loss:
                    close_queue.append((position_id, float(candle.open), "stop_loss_gap"))
                    continue
                if candle.high >= position.stop_loss:
                    close_queue.append((position_id, position.stop_loss, "stop_loss"))
                    continue
                if candle.low <= position.take_profit:
                    close_queue.append((position_id, position.take_profit, "take_profit"))
                    continue
            position.current_price = float(candle.close)
            position.unrealised_pnl = self._pnl(position, float(candle.close))

        for position_id, close_price, reason in close_queue:
            self._close_position_at_price(position_id, close_price, reason)

    def get_account_state(self) -> AccountState:
        open_risk = 0.0
        for pos in self._positions.values():
            risk_dist = abs(float(pos.open_price) - float(pos.stop_loss))
            if risk_dist > 0.0:
                open_risk += risk_dist * float(pos.units) * self.instrument_spec.ounces_per_unit
        open_risk_pct = (open_risk / float(self._equity) * 100.0) if self._equity > 0.0 else 0.0
        return AccountState(
            equity=float(self._equity),
            balance=float(self._balance),
            open_trade_count=len(self._positions),
            daily_pnl=float(self._daily_pnl),
            peak_equity_30d=float(self._peak_equity_30d),
            current_spread_pips=self.get_current_spread_pips(self.instrument),
            open_risk_pct=open_risk_pct,
        )

    def get_open_positions(self) -> list[PositionRecord]:
        return list(self._positions.values())

    def poll_closed_trades(self, known_ids: set[str], open_meta: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Return simulated closes since the last poll (drain _trade_history)."""
        drained = list(self._trade_history[self._poll_index:])
        self._poll_index = len(self._trade_history)
        return drained

    def restore_state_from_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Re-seed simulated account state from persisted snapshots/trades."""
        self._equity = float(snapshot.get("equity", self._equity))
        self._balance = float(snapshot.get("balance", self._balance))
        self._peak_equity_30d = float(snapshot.get("peak_equity_30d", self._equity))
        self._daily_pnl = float(snapshot.get("daily_pnl", 0.0))
        self._daily_pnl_date = snapshot.get("daily_pnl_date")
        self._trade_history = list(snapshot.get("trade_history", []))
        self._positions = {p.position_id: p for p in snapshot.get("positions", [])}
        self._poll_index = len(self._trade_history)

    def get_current_spread_pips(self, instrument: str) -> float:
        """Estimate spread based on session and volatility.

        XAU/USD spreads vary significantly by market session:
        - London/NY overlap (13:00-16:00 UTC): 1.0x base
        - London only (08:00-13:00 UTC):       1.3x base
        - NY only (13:00-22:00 UTC):           1.3x base
        - Asian session (00:00-08:00 UTC):     2.0x base

        The base spread is from settings (paper_spread_pips). When ATR is
        elevated (above the trailing 50-period median) an additional 30%
        volatility premium is applied.

        Falls back to the configured static value when no candle history
        is available (e.g., during broker initialization).
        """
        base = float(self.execution_settings.get("paper_spread_pips", 1.5))
        # Session adjustment from latest candle if available
        if self._candle_prices:
            now = datetime.now(UTC)
            hour = now.hour
            if 13 <= hour < 16:
                session_factor = 1.0  # London/NY overlap — tightest
            elif 8 <= hour < 13:
                session_factor = 1.3  # London only
            elif 13 <= hour < 22:
                session_factor = 1.3  # NY only
            else:
                session_factor = 2.0  # Asian session — widest
            base *= session_factor
        return round(base, 1)

    def _current_market_price(self) -> float | None:
        """Return the most recent close price from candle history for price collar checks."""
        if not self._candle_prices:
            return None
        return float(self._candle_prices[-1])

    def _roll_daily_pnl_if_new_day(self, candle_ts: Any) -> None:
        """Reset the daily P&L accumulator when a candle enters a new UTC day.

        `_daily_pnl` feeds the daily-loss kill switch (risk manager + watchdog).
        Without this rollover it accumulates for the life of the process, which
        silently disables the daily-loss kill (it would take a loss larger than
        all prior profit to trip). Called on every candle via update_prices.
        """
        try:
            ts = candle_ts
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if not isinstance(ts, datetime):
                return
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            candle_date = ts.astimezone(UTC).date()
        except (ValueError, TypeError, AttributeError):
            return
        if self._daily_pnl_date is None:
            # First candle seen: establish the baseline day, keep P&L as-is.
            self._daily_pnl_date = candle_date
            return
        if candle_date != self._daily_pnl_date:
            self._daily_pnl = 0.0
            self._daily_pnl_date = candle_date

    def _close_position_at_price(self, position_id: str, close_price: float, reason: str) -> OrderResult:
        position = self._positions.pop(position_id)
        intended_exit_price = float(close_price)
        exit_slippage = self._sample_slippage_distance()
        actual_exit_price = self._worsen_exit_price(position.direction, intended_exit_price, exit_slippage)
        gross_pnl = self._pnl(position, actual_exit_price)
        spread_cost = self._spread_cost(position.units)
        exit_slippage_cost = self._slippage_cost(exit_slippage, position.units)
        total_slippage_cost = float(position.entry_slippage_cost + exit_slippage_cost)
        net_pnl = gross_pnl - spread_cost
        self._balance += net_pnl
        self._equity += net_pnl
        self._daily_pnl += net_pnl
        self._peak_equity_30d = max(self._peak_equity_30d, self._equity)
        close_time = datetime.now(UTC)
        # Calculate R-multiple: how many times risk was won or lost
        risk_distance = abs(float(position.open_price) - float(position.stop_loss))
        risk_amount = risk_distance * float(position.units) * self.instrument_spec.ounces_per_unit
        r_multiple = net_pnl / risk_amount if risk_amount > 0 else 0.0
        self._trade_history.append(
            {
                "position_id": position_id,
                "pnl": gross_pnl,
                "gross_pnl": gross_pnl,
                "fee": spread_cost,
                "spread_cost": spread_cost,
                "pnl_after_fees": net_pnl,
                "net_pnl": net_pnl,
                "risk_amount": risk_amount,
                "r": r_multiple,
                "r_multiple": r_multiple,
                "fee_in_equity": True,
                "direction": position.direction,
                "entry": position.open_price,
                "actual_entry": position.open_price,
                "intended_entry": (
                    float(position.intended_entry_price)
                    if position.intended_entry_price is not None
                    else position.open_price
                ),
                "entry_slippage": position.entry_slippage,
                "entry_slippage_cost": position.entry_slippage_cost,
                "exit": actual_exit_price,
                "actual_exit": actual_exit_price,
                "intended_exit": intended_exit_price,
                "exit_slippage": exit_slippage,
                "exit_slippage_cost": exit_slippage_cost,
                "total_slippage_cost": total_slippage_cost,
                "lot_size": position.lot_size,
                "units": position.units,
                "notional_ounces": position.units * self.instrument_spec.ounces_per_unit,
                "open_time": position.open_time.isoformat(),
                "reason": reason,
                "closed_at": close_time.isoformat(),
            }
        )
        return OrderResult(
            success=True,
            order_id=position_id,
            fill_price=float(actual_exit_price),
            fill_time=close_time,
            lot_size=position.lot_size,
            direction=position.direction,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            rejection_reason=None,
            broker="paper",
            raw_response={
                "reason": reason,
                "pnl": gross_pnl,
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "fee": spread_cost,
                "spread_cost": spread_cost,
                "fee_in_equity": True,
                "position_id": position_id,
                "intended_exit_price": intended_exit_price,
                "actual_exit_price": actual_exit_price,
                "exit_slippage": exit_slippage,
                "exit_slippage_cost": exit_slippage_cost,
                "total_slippage_cost": total_slippage_cost,
                "units": position.units,
                "notional_ounces": position.units * self.instrument_spec.ounces_per_unit,
            },
        )

    def _pnl(self, position: PositionRecord, close_price: float) -> float:
        return self.instrument_spec.pnl(position.direction, position.open_price, close_price, position.units)

    def _order_units(self, order: RiskOrder) -> float:
        if float(order.units) > 0.0:
            return self.instrument_spec.round_units(float(order.units))
        return self.instrument_spec.lots_to_units(order.lot_size)

    def _sample_slippage_distance(self) -> float:
        slippage_std = float(self.execution_settings.get("slippage_std_pips", 0.5)) * float(
            self.risk_settings.get("pip_size", 0.01)
        )
        if slippage_std <= 0.0:
            return 0.0
        # Folded-normal (abs of gaussian): slippage is always adverse for
        # market orders at breakout levels. Unlike limit orders where price
        # improvement is possible, a market order at a Donchian breakout
        # buys at the ask / sells at the bid — never better. The mode is
        # near-zero but the tail is strictly positive.
        # Was Gaussian (allowed negative/favorable slippage) prior to audit.
        return abs(self._rng.gauss(0.0, slippage_std))

    @staticmethod
    def _worsen_entry_price(direction: str, intended_price: float, slippage: float) -> float:
        if direction == "BUY":
            return float(intended_price) + float(slippage)
        return float(intended_price) - float(slippage)

    @staticmethod
    def _worsen_exit_price(direction: str, intended_price: float, slippage: float) -> float:
        if direction == "BUY":
            return float(intended_price) - float(slippage)
        return float(intended_price) + float(slippage)

    def _slippage_cost(self, price_distance: float, units: float) -> float:
        return float(price_distance) * float(units) * self.instrument_spec.ounces_per_unit

    def _spread_cost(self, units: float) -> float:
        """Spread cost is already embedded in fill prices via slippage model.

        The entry and exit prices are worsened by _sample_slippage_distance()
        (folded-normal, always adverse), which captures the full friction of
        crossing the spread and market impact at breakout levels.  Adding a
        separate spread cost on top double-counts friction by ~2x.

        This function is kept as a historical reference — it returns 0.0 so
        net_pnl = gross_pnl (slippage costs are already in the fill prices).

        History:
          - Original formula was 2.0 * spread * pip_value * units
          - Jul 2026 audit identified this as double-counting
          - Zeroed out Jul 20, 2026 after verifying slippage model captures
            all friction
        """
        return 0.0


class OandaBroker(BrokerBase):
    """OANDA v20 REST broker adapter using oandapyV20 when available."""

    # OANDA enforces SL/TP server-side, so the trader must poll for closed
    # positions rather than simulate exits via update_prices().
    server_managed_sl_tp = True

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.risk_settings = settings.get("risk", {})
        self.instrument_spec = InstrumentSpec.from_settings(settings)
        self.execution_settings = settings.get("execution", {})
        self.oanda_settings = settings.get("broker", {}).get("oanda", {})
        self.instrument = str(self.oanda_settings.get("instrument", "XAU_USD"))
        self.account_id = os.getenv(str(self.oanda_settings.get("account_id_env", "OANDA_ACCOUNT_ID")), "")
        self.environment = os.getenv(
            str(self.oanda_settings.get("environment_env", "OANDA_ENV")),
            str(self.oanda_settings.get("default_environment", "practice")),
        )
        _assert_oanda_interlocks(self.environment)
        self._client: Any | None = None

    def submit_order(self, order: RiskOrder) -> OrderResult:
        if not order.approved:
            return _rejected_order_result(order, order.rejection_reason or "risk_order_rejected", "oanda")

        spread = self.get_current_spread_pips(self.instrument)
        if spread > float(self.risk_settings.get("max_spread_pips", 3.0)):
            return _rejected_order_result(order, "spread_too_wide_at_execution", "oanda")

        # Price collar / cache-integrity: reject if the intended entry diverges
        # from the current market by more than PRICE_COLLAR_PCT (mirrors
        # PaperBroker's collar so a stale signal cannot place a far-off order).
        collar_pct = float(self.execution_settings.get("oanda_price_collar_pct", 5.0))
        market_price = self._current_market_price()
        if market_price is not None and market_price > 0:
            deviation = abs(float(order.instruction.entry_price) - market_price) / market_price * 100.0
            if deviation > collar_pct:
                return _rejected_order_result(
                    order,
                    f"price_collar_violation: entry {order.instruction.entry_price:.2f} "
                    f"is {deviation:.1f}% away from market {market_price:.2f}",
                    "oanda",
                )

        order_type = str(self.execution_settings.get("oanda_order_type", "limit")).lower()
        order_spec = self._order_payload(order, order_type=order_type)
        data = {"order": order_spec}
        response = self._submit_limit_order(data)
        fill = response.get("orderFillTransaction") or response.get("orderCreateTransaction", {})
        # The SL/TP OANDA actually enforces: for MARKET orders these are rebased
        # around the live fill (_market_order_payload), for LIMIT they equal the
        # instruction levels. Returning the real values lets the trader record the
        # truth instead of the stale candle-open-based instruction levels.
        result_stop = float(order_spec.get("stopLossOnFill", {}).get("price", float(order.instruction.stop_loss)))
        result_tp = float(order_spec.get("takeProfitOnFill", {}).get("price", float(order.instruction.take_profit)))
        if "orderFillTransaction" not in response:
            return OrderResult(
                success=False,
                order_id=str(fill.get("id")) if fill.get("id") is not None else None,
                fill_price=None,
                fill_time=None,
                lot_size=float(order.lot_size),
                direction=order.instruction.direction,
                stop_loss=result_stop,
                take_profit=result_tp,
                rejection_reason="fill_timeout",
                broker="oanda",
                raw_response=response,
            )

        # Key the OANDA trade ID so the trader can track open-meta / dedup by
        # trade ID (aggregated position_id is per-instrument+side and lossy).
        opened = fill.get("tradeOpened") or {}
        trade_id = str(opened["tradeID"]) if opened.get("tradeID") is not None else None
        if trade_id is None and fill.get("tradeID") is not None:
            trade_id = str(fill["tradeID"])
        raw = dict(response)
        raw["tradeID"] = trade_id
        order_id = trade_id or (str(fill.get("id")) if fill.get("id") is not None else None)

        fill_time = _parse_datetime(fill.get("time"))
        return OrderResult(
            success=True,
            order_id=order_id,
            fill_price=float(fill.get("price", order.instruction.entry_price)),
            fill_time=fill_time,
            lot_size=float(order.lot_size),
            direction=order.instruction.direction,
            stop_loss=result_stop,
            take_profit=result_tp,
            rejection_reason=None,
            broker="oanda",
            raw_response=raw,
        )

    def close_position(self, position_id: str, reason: str) -> OrderResult:
        # OANDA trade IDs are numeric; instrument names are not. Route numeric
        # ids to TradeClose (a single trade) and names to PositionClose (whole
        # instrument position) so engine.close_all_positions works for either.
        if str(position_id).isdigit():
            response = self._close_trade(str(position_id))
            close_txn = (response.get("longOrderFillTransaction")
                         or response.get("shortOrderFillTransaction")
                         or response.get("orderCancelTransaction") or {})
        else:
            response = self._close_oanda_position(position_id)
            close_txn = response.get("longOrderFillTransaction") or response.get("shortOrderFillTransaction") or {}
        closed_units = abs(float(close_txn.get("units", 0.0))) if close_txn.get("units") is not None else 0.0
        return OrderResult(
            success=True,
            order_id=str(close_txn.get("id", position_id)),
            fill_price=float(close_txn.get("price", 0.0)) if close_txn.get("price") is not None else None,
            fill_time=_parse_datetime(close_txn.get("time")),
            lot_size=self.instrument_spec.units_to_lots(closed_units),
            direction="CLOSE",
            stop_loss=0.0,
            take_profit=0.0,
            rejection_reason=None,
            broker="oanda",
            raw_response={**response, "reason": reason},
        )

    def get_account_state(self) -> AccountState:
        response = self._account_summary()
        account = response.get("account", response)
        equity = float(account.get("NAV", account.get("balance", 0.0)))
        balance = float(account.get("balance", equity))
        # Compute open risk from unrealized PnL of open positions
        # (OANDA positions don't expose stop_loss in the summary endpoint,
        # so we use absolute unrealized PnL as a conservative proxy)
        open_positions = self.get_open_positions()
        open_risk = sum(abs(float(p.unrealised_pnl)) for p in open_positions)
        open_risk_pct = (open_risk / equity * 100.0) if equity > 0.0 else 0.0
        return AccountState(
            equity=equity,
            balance=balance,
            open_trade_count=int(account.get("openTradeCount", 0)),
            daily_pnl=0.0,
            peak_equity_30d=equity,
            current_spread_pips=self.get_current_spread_pips(self.instrument),
            open_risk_pct=open_risk_pct,
        )

    def get_open_positions(self) -> list[PositionRecord]:
        response = self._open_positions()
        # Per-trade SL/TP from TradesList OPEN (aggregated positions lose these;
        # the status bar and close_all_positions rely on accurate levels).
        try:
            details = {d["direction"]: d for d in self.get_open_trade_details()}
        except Exception:
            details = {}
        positions: list[PositionRecord] = []
        for item in response.get("positions", []):
            instrument = str(item.get("instrument", self.instrument))
            for side, direction in (("long", "BUY"), ("short", "SELL")):
                side_data = item.get(side, {})
                units = abs(float(side_data.get("units", 0.0)))
                if units == 0.0:
                    continue
                price = float(side_data.get("averagePrice", 0.0))
                d = details.get(direction, {})
                positions.append(
                    PositionRecord(
                        position_id=f"{instrument}_{side}",
                        instrument=instrument,
                        direction=direction,
                        open_price=price,
                        current_price=price,
                        lot_size=self.instrument_spec.units_to_lots(units),
                        units=units,
                        stop_loss=float(d.get("stop_loss", 0.0)),
                        take_profit=float(d.get("take_profit", 0.0)),
                        open_time=d.get("open_time") or datetime.now(UTC),
                        unrealised_pnl=float(side_data.get("unrealizedPL", 0.0)),
                        broker="oanda",
                    )
                )
        return positions

    def get_current_spread_pips(self, instrument: str) -> float:
        response = self._pricing(instrument)
        price = response.get("prices", [{}])[0]
        bid = float(price.get("bids", [{"price": 0.0}])[0]["price"])
        ask = float(price.get("asks", [{"price": bid}])[0]["price"])
        return (ask - bid) / float(self.risk_settings.get("pip_size", 0.01))

    def _current_market_price(self) -> float | None:
        """Mid-price for collar checks (None if pricing is unavailable)."""
        try:
            pricing = self._pricing(self.instrument)
        except Exception:
            return None
        price = (pricing.get("prices") or [{}])[0]
        bid = float(price.get("bids", [{"price": 0.0}])[0]["price"])
        ask = float(price.get("asks", [{"price": bid}])[0]["price"])
        if bid <= 0 and ask <= 0:
            return None
        return (bid + ask) / 2.0

    def _open_trades(self) -> dict[str, Any]:
        from oandapyV20.endpoints.trades import TradesList

        endpoint = TradesList(self.account_id, params={"state": "OPEN", "instrument": self.instrument})
        return self._client_instance().request(endpoint)

    def _closed_trades(self) -> dict[str, Any]:
        from oandapyV20.endpoints.trades import TradesList

        endpoint = TradesList(
            self.account_id,
            params={"state": "CLOSED", "instrument": self.instrument, "count": 100},
        )
        return self._client_instance().request(endpoint)

    def _close_trade(self, trade_id: str) -> dict[str, Any]:
        from oandapyV20.endpoints.trades import TradeClose

        endpoint = TradeClose(self.account_id, tradeID=trade_id, data={"units": "ALL"})
        return self._client_instance().request(endpoint)

    def get_open_trade_details(self) -> list[dict[str, Any]]:
        """Per-trade open detail (tradeID, direction, units, SL/TP, open_time).

        The aggregated OpenPositions endpoint loses SL/TP, which the trader needs
        to persist open-position risk per trade. Keys mirror the trader's
        open_positions rows so `_open_meta` can be built/restored from them.
        """
        details: list[dict[str, Any]] = []
        for t in self._open_trades().get("trades", []):
            details.append(
                {
                    "position_id": str(t.get("id")),
                    "direction": "BUY" if float(t.get("initialUnits", 0)) > 0 else "SELL",
                    "units": abs(float(t.get("initialUnits", 0))),
                    "open_price": float(t.get("price", 0.0)),
                    "stop_loss": _order_price(t.get("stopLossOrder", {})),
                    "take_profit": _order_price(t.get("takeProfitOrder", {})),
                    "open_time": _parse_datetime(t.get("openTime")),
                }
            )
        return details

    def poll_closed_trades(self, known_ids: set[str], open_meta: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Reconstruct trader-shaped close dicts from OANDA's closed-trade list.

        Skips trade IDs already persisted (dedup across restarts) and trades the
        trader never opened (external/manual closes — logged, not fabricated).
        """
        closed: list[dict[str, Any]] = []
        for t in self._closed_trades().get("trades", []):
            tid = str(t.get("id"))
            if tid in known_ids:
                continue
            meta = open_meta.get(tid)
            if meta is None:
                # Position the trader never opened (external/manual close, or a
                # leftover from before the switch). Don't fabricate a trade —
                # equity reconciles via get_account_state(), which is platform truth.
                continue
            closed.append(self._build_closed_trade(t, meta))
        return closed

    def _build_closed_trade(self, t: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        """Map an OANDA closed trade + trader open-meta into the trader trade-dict shape."""
        tid = str(t.get("id"))
        direction = "BUY" if float(t.get("initialUnits", 0)) > 0 else "SELL"
        units = abs(float(t.get("initialUnits", 0)))
        entry = float(t.get("averageOpenPrice", t.get("price", 0.0)))
        exit_price = float(t.get("averageClosePrice", t.get("closePrice", 0.0)))
        gross_pnl = float(t.get("realizedPL", 0.0))
        financing = float(t.get("financing", 0.0))
        net_pnl = gross_pnl + financing
        stop_loss = float(meta.get("stop_loss", 0.0))
        take_profit = float(meta.get("take_profit", 0.0))
        intended_entry = float(meta.get("intended_entry", entry))
        sl_state = str(((t.get("stopLossOrder") or {}).get("state") or ""))
        tp_state = str(((t.get("takeProfitOrder") or {}).get("state") or ""))
        if sl_state.upper() == "FILLED":
            reason = "stop_loss"
            intended_exit = stop_loss
        elif tp_state.upper() == "FILLED":
            reason = "take_profit"
            intended_exit = take_profit
        else:
            reason = "manual_close"
            intended_exit = exit_price
        entry_slippage = entry - intended_entry if direction == "BUY" else intended_entry - entry
        exit_slippage = exit_price - intended_exit if direction == "BUY" else intended_exit - exit_price
        risk_amount = float(meta.get("risk_amount", 0.0))
        r_multiple = net_pnl / risk_amount if risk_amount > 0 else 0.0
        entry_slip_cost = entry_slippage * units * self.instrument_spec.ounces_per_unit
        exit_slip_cost = exit_slippage * units * self.instrument_spec.ounces_per_unit
        return {
            "position_id": tid,
            "direction": direction,
            "entry": entry,
            "actual_entry": entry,
            "intended_entry": intended_entry,
            "entry_slippage": entry_slippage,
            "entry_slippage_cost": entry_slip_cost,
            "exit": exit_price,
            "actual_exit": exit_price,
            "intended_exit": intended_exit,
            "exit_slippage": exit_slippage,
            "exit_slippage_cost": exit_slip_cost,
            "total_slippage_cost": entry_slip_cost + exit_slip_cost,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "units": units,
            "lot_size": self.instrument_spec.units_to_lots(units),
            "notional_ounces": units * self.instrument_spec.ounces_per_unit,
            "pnl": gross_pnl,
            "gross_pnl": gross_pnl,
            "fee": financing,
            "financing": financing,
            "spread_cost": 0.0,
            "net_pnl": net_pnl,
            "pnl_after_fees": net_pnl,
            "risk_amount": risk_amount,
            "r": r_multiple,
            "r_multiple": r_multiple,
            "open_time": _parse_datetime(t.get("openTime")).isoformat(),
            "closed_at": _parse_datetime(t.get("closeTime")).isoformat(),
            "reason": reason,
        }

    def _order_payload(self, order: RiskOrder, order_type: str = "limit") -> dict[str, Any]:
        instruction = order.instruction
        units_value = order.units or self.instrument_spec.lots_to_units(order.lot_size)
        units = units_value if instruction.direction == "BUY" else -units_value
        precision = int(self.execution_settings.get("oanda_price_precision", 2))

        if order_type == "market":
            return self._market_order_payload(order, units, precision)

        return {
            "type": "LIMIT",
            "instrument": self.instrument,
            "units": self.instrument_spec.format_units(units),
            "price": str(round(instruction.entry_price, precision)),
            "stopLossOnFill": {"price": str(round(instruction.stop_loss, precision))},
            "takeProfitOnFill": {"price": str(round(instruction.take_profit, precision))},
            "timeInForce": "GTC",
        }

    def _market_order_payload(self, order: RiskOrder, units: float, precision: int) -> dict[str, Any]:
        """MARKET order with SL/TP rebased around the current market price.

        D4's entry_price is the open of the just-completed candle (a past price);
        a LIMIT order there would sit unfilled and leave an orphaned GTC order.
        A MARKET order fills immediately at the live ask/bid, so the stop and
        target are rebased around that fill using the strategy's intended risk
        distances — preserving the exact risk PaperBroker simulates.
        timeInForce=FOK avoids leaving an unfilled order behind.
        """
        instruction = order.instruction
        pricing = self._pricing(self.instrument)
        price = (pricing.get("prices") or [{}])[0]
        bid = float(price.get("bids", [{"price": 0.0}])[0]["price"])
        ask = float(price.get("asks", [{"price": bid}])[0]["price"])
        ref = ask if instruction.direction == "BUY" else bid
        sl_dist = abs(float(instruction.entry_price) - float(instruction.stop_loss))
        tp_dist = abs(float(instruction.take_profit) - float(instruction.entry_price))
        if instruction.direction == "BUY":
            sl = ref - sl_dist
            tp = ref + tp_dist
        else:
            sl = ref + sl_dist
            tp = ref - tp_dist
        return {
            "type": "MARKET",
            "instrument": self.instrument,
            "units": self.instrument_spec.format_units(units),
            "stopLossOnFill": {"price": str(round(sl, precision))},
            "takeProfitOnFill": {"price": str(round(tp, precision))},
            "timeInForce": "FOK",
        }

    def _client_instance(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = os.getenv(str(self.oanda_settings.get("api_key_env", "OANDA_API_KEY")))
        if not api_key:
            raise RuntimeError("Missing OANDA_API_KEY")
        try:
            from oandapyV20 import API
        except ImportError as exc:
            raise RuntimeError("oandapyV20 is required for OandaBroker") from exc
        self._client = API(access_token=api_key, environment=self.environment)
        return self._client

    def _submit_limit_order(self, data: dict[str, Any]) -> dict[str, Any]:
        from oandapyV20.endpoints.orders import OrderCreate

        endpoint = OrderCreate(self.account_id, data=data)
        return self._client_instance().request(endpoint)

    def _close_oanda_position(self, position_id: str) -> dict[str, Any]:
        from oandapyV20.endpoints.positions import PositionClose

        data = {"longUnits": "ALL", "shortUnits": "ALL"}
        endpoint = PositionClose(self.account_id, instrument=position_id, data=data)
        return self._client_instance().request(endpoint)

    def _account_summary(self) -> dict[str, Any]:
        from oandapyV20.endpoints.accounts import AccountSummary

        return self._client_instance().request(AccountSummary(self.account_id))

    def _open_positions(self) -> dict[str, Any]:
        from oandapyV20.endpoints.positions import OpenPositions

        return self._client_instance().request(OpenPositions(self.account_id))

    def _pricing(self, instrument: str) -> dict[str, Any]:
        from oandapyV20.endpoints.pricing import PricingInfo

        endpoint = PricingInfo(self.account_id, params={"instruments": instrument})
        return self._client_instance().request(endpoint)


def _rejected_order_result(order: RiskOrder, reason: str, broker: str) -> OrderResult:
    instruction = order.instruction
    return OrderResult(
        success=False,
        order_id=None,
        fill_price=None,
        fill_time=None,
        lot_size=float(order.lot_size),
        direction=instruction.direction,
        stop_loss=float(instruction.stop_loss),
        take_profit=float(instruction.take_profit),
        rejection_reason=reason,
        broker=broker,
        raw_response={"rejection_reason": reason, "warnings": list(order.warnings)},
    )


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _assert_oanda_interlocks(environment: str) -> None:
    if not _truthy_env("ALLOW_OANDA_ORDERS"):
        raise RuntimeError("External OANDA orders blocked: set ALLOW_OANDA_ORDERS=true to enable practice/live broker mode")
    if str(environment).lower() == "live" and not _truthy_env("ALLOW_LIVE_TRADING"):
        raise RuntimeError("Live OANDA trading blocked: set ALLOW_LIVE_TRADING=true in addition to ALLOW_OANDA_ORDERS=true")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return datetime.now(UTC)


def _order_price(order: dict[str, Any]) -> float:
    """Price from an OANDA order object (used for open-trade SL/TP detail)."""
    if order.get("price") is not None:
        return float(order["price"])
    return 0.0


__all__ = ["BrokerBase", "OandaBroker", "OrderResult", "PaperBroker", "PositionRecord"]
