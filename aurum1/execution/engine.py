"""Thin execution engine wrapper for broker routing and SQLite logging."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aurum1.data.ingestion import initialize_database
from aurum1.execution.broker import BrokerBase, OandaBroker, OrderResult, PaperBroker
from aurum1.risk import RiskOrder
from aurum1.signals import CandleRow


class ExecutionEngine:
    """Route approved risk orders to the configured broker and log outcomes."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.db_path = Path(
            str(
                settings.get("execution", {}).get(
                    "db_path",
                    settings.get("data", {}).get("db_path", "aurum1/data/aurum1.sqlite3"),
                )
            )
        )
        initialize_database(self.db_path)
        if bool(settings.get("broker", {}).get("paper_trade", True)):
            self.broker: BrokerBase = PaperBroker(settings)
        else:
            self.broker = OandaBroker(settings)

    def execute(self, order: RiskOrder) -> OrderResult:
        result = self.broker.submit_order(order)
        self._log_order_result(result, order)
        return result

    def update_paper_prices(self, candle: CandleRow) -> None:
        if isinstance(self.broker, PaperBroker):
            self.broker.update_prices(candle)

    def close_all_positions(self, reason: str) -> list[OrderResult]:
        results: list[OrderResult] = []
        for position in list(self.broker.get_open_positions()):
            result = self.broker.close_position(position.position_id, reason)
            self._log_order_result(result, None, status="closed")
            results.append(result)
        return results

    def _log_order_result(self, result: OrderResult, order: RiskOrder | None, status: str | None = None) -> None:
        log_status = status or _status_for_result(result)
        payload = _jsonable(result)
        if order is not None:
            payload["risk_order"] = _jsonable(order)
        timestamp = (result.fill_time or datetime.now(UTC)).isoformat()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO trades_log
                    (timestamp, direction, price, size, sl, tp, order_id, status, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp,
                        result.direction,
                        result.fill_price,
                        result.lot_size,
                        result.stop_loss,
                        result.take_profit,
                        result.order_id,
                        log_status,
                        json.dumps(payload, sort_keys=True),
                    ),
                )


def _status_for_result(result: OrderResult) -> str:
    if result.success:
        return "filled"
    if result.rejection_reason == "fill_timeout":
        return "timeout"
    return "rejected"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


__all__ = ["ExecutionEngine"]
