"""Event-driven backtesting engine for AURUM-1 Phase 7."""

from __future__ import annotations

import copy
import math
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from aurum1.execution import ExecutionEngine, PaperBroker
from aurum1.features.engineer import FeatureEngineer, WARMUP_BARS
from aurum1.models.ensemble import SignalResult
from aurum1.models.regime_classifier import REGIME_LABELS, RegimeClassifier
from aurum1.risk import RiskManager
from aurum1.signals import CandleRow, MachineMode, StateMachine


@dataclass
class BacktestResult:
    start_date: datetime
    end_date: datetime
    instrument: str
    mode: str
    initial_equity: float
    final_equity: float
    total_bars: int
    total_trades: int
    total_return_pct: float
    cagr: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    max_drawdown_duration_bars: int
    avg_drawdown_pct: float
    win_rate: float
    profit_factor: float
    avg_win_pct: float
    avg_loss_pct: float
    avg_rr_achieved: float
    largest_win_pct: float
    largest_loss_pct: float
    avg_trade_duration_bars: int
    total_fees_paid: float
    total_signals: int
    signals_approved: int
    signals_rejected: int
    rejection_reasons: dict[str, int]
    trades_in_trending_up: int
    trades_in_trending_down: int
    trades_in_ranging: int
    win_rate_by_regime: dict[str, float]
    equity_curve: list[float]
    drawdown_curve: list[float]
    trades: list[dict[str, Any]]


class BacktestEngine:
    """Run AURUM-1 one candle at a time with no future bars visible."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.regime_classifier: RegimeClassifier | None = None
        self.direction_predictor: Any | None = None

    def run(
        self,
        ohlcv: pd.DataFrame,
        macro: pd.DataFrame,
        cot: pd.DataFrame,
        htf_frames: dict[str, pd.DataFrame] | None = None,
        mode: MachineMode = MachineMode.RULE_REGIME,
        initial_equity: float = 10000.0,
    ) -> BacktestResult:
        ohlcv = ohlcv.sort_index().copy()
        run_settings = self._settings_for_run(initial_equity)
        feature_engineer = FeatureEngineer({"feature_engineering": {"lookahead_check": False}})
        state_machine = StateMachine(run_settings, mode=mode)
        risk_manager = RiskManager(run_settings)
        execution = ExecutionEngine(run_settings)
        assert isinstance(execution.broker, PaperBroker)

        equity_curve: list[float] = []
        signals = 0
        approved = 0
        rejected = 0
        rejection_reasons: Counter[str] = Counter()
        trade_history_cursor = 0
        open_meta: dict[str, dict[str, Any]] = {}
        closed_trades: list[dict[str, Any]] = []
        backtest_log: list[dict[str, Any]] = []
        feature_table = self._build_causal_feature_table(ohlcv, macro, cot, htf_frames)

        for bar_index, (timestamp, _) in enumerate(ohlcv.iterrows()):
            candle = None
            signal = None
            if timestamp in feature_table.index:
                feature_frame = feature_table.loc[:timestamp]
                feature_row = feature_frame.iloc[-1]
                candle = _candle_from_row(timestamp, ohlcv.loc[timestamp], feature_row)
                signal = self._infer_signal(feature_frame, feature_row, timestamp, mode)
                instruction = state_machine.on_candle(candle, signal, is_blackout=False)
                if instruction is not None:
                    signals += 1
                    account = execution.broker.get_account_state()
                    risk_order = risk_manager.evaluate(
                        instruction,
                        account,
                        list(execution.broker._trade_history),  # Paper broker trade history for Kelly.
                    )
                    if risk_order.approved:
                        approved += 1
                    else:
                        rejected += 1
                        rejection_reasons[risk_order.rejection_reason or "unknown"] += 1
                    result = execution.execute(risk_order)
                    if result.success and result.order_id:
                        open_meta[result.order_id] = {
                            "regime": instruction.regime,
                            "open_bar": bar_index,
                            "open_time": timestamp.isoformat(),
                            "risk_amount": risk_order.risk_amount,
                            "signal_score": instruction.signal_score,
                        }

            price_candle = candle or _basic_candle_from_ohlcv(timestamp, ohlcv.loc[timestamp])
            execution.update_paper_prices(price_candle)
            new_closed = execution.broker._trade_history[trade_history_cursor:]
            for trade in new_closed:
                meta = open_meta.pop(str(trade.get("position_id")), {})
                closed_trades.append(_augment_trade(trade, meta, bar_index, run_settings))
            trade_history_cursor = len(execution.broker._trade_history)

            account_state = execution.broker.get_account_state()
            equity_curve.append(float(account_state.equity))
            backtest_log.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "equity": float(account_state.equity),
                    "open_positions": account_state.open_trade_count,
                    "signal": signal.direction if signal else "NONE",
                }
            )

        final_timestamp = ohlcv.index[-1] if len(ohlcv) else datetime.now(UTC)
        for position in list(execution.broker.get_open_positions()):
            result = execution.broker.close_position(position.position_id, "backtest_end")
            if result.success:
                trade = execution.broker._trade_history[-1]
                meta = open_meta.pop(str(trade.get("position_id")), {})
                closed_trades.append(_augment_trade(trade, meta, len(ohlcv) - 1, run_settings))
        if equity_curve:
            equity_curve[-1] = float(execution.broker.get_account_state().equity)

        result = build_backtest_result(
            equity_curve=equity_curve,
            trades=closed_trades,
            start_date=_to_datetime(ohlcv.index[0]) if len(ohlcv) else datetime.now(UTC),
            end_date=_to_datetime(final_timestamp) if len(ohlcv) else datetime.now(UTC),
            instrument=str(ohlcv.get("instrument", pd.Series(["XAU_USD"])).iloc[0]) if len(ohlcv) else "XAU_USD",
            mode=mode.value,
            initial_equity=initial_equity,
            total_bars=len(ohlcv),
            total_signals=signals,
            signals_approved=approved,
            signals_rejected=rejected,
            rejection_reasons=dict(rejection_reasons),
        )
        result.trades.append({"backtest_log_size": len(backtest_log), "type": "metadata"})
        result.trades.pop()
        return result

    def _build_causal_feature_table(
        self,
        ohlcv: pd.DataFrame,
        macro: pd.DataFrame,
        cot: pd.DataFrame,
        htf_frames: dict[str, pd.DataFrame] | None,
    ) -> pd.DataFrame:
        try:
            return FeatureEngineer({"feature_engineering": {"lookahead_check": False}}).build_features(
                ohlcv,
                macro,
                cot,
                htf_frames=htf_frames,
                include_target=False,
            )
        except ValueError:
            rows: list[pd.DataFrame] = []
            for end in range(WARMUP_BARS + 1, len(ohlcv) + 1):
                try:
                    prefix_features = FeatureEngineer({"feature_engineering": {"lookahead_check": False}}).build_features(
                        ohlcv.iloc[:end],
                        macro,
                        cot,
                        htf_frames=_slice_htf_frames(htf_frames, ohlcv.index[end - 1]),
                        include_target=False,
                    )
                except ValueError:
                    if rows:
                        break
                    continue
                if not prefix_features.empty:
                    rows.append(prefix_features.tail(1))
            return pd.concat(rows).sort_index() if rows else pd.DataFrame()

    def _infer_signal(
        self,
        feature_frame: pd.DataFrame,
        feature_row: pd.Series,
        timestamp: Any,
        mode: MachineMode,
    ) -> SignalResult:
        if self.regime_classifier is not None and self.regime_classifier.model is not None:
            proba = self.regime_classifier.predict_proba(feature_frame.tail(1))[0]
            regime_class = int(np.argmax(proba))
            confidence = float(proba[regime_class])
        else:
            label = int(RegimeClassifier.generate_labels(feature_frame.tail(1)).iloc[-1])
            regime_class = label
            confidence = 0.75
        regime = REGIME_LABELS.get(regime_class, "RANGING")

        if float(feature_row.get("ema_9", 0.0)) > float(feature_row.get("ema_20", 0.0)):
            direction = "BUY"
            direction_signal = 0.6
        elif float(feature_row.get("ema_9", 0.0)) < float(feature_row.get("ema_20", 0.0)):
            direction = "SELL"
            direction_signal = -0.6
        else:
            direction = "FLAT"
            direction_signal = 0.0

        if mode == MachineMode.RULE_REGIME_SENT:
            sentiment_scalar = 0.2 if direction == "BUY" else -0.2 if direction == "SELL" else 0.0
        else:
            sentiment_scalar = 0.0
        return SignalResult(
            direction=direction,
            raw_score=abs(direction_signal),
            regime=regime,
            regime_confidence=confidence,
            direction_signal=direction_signal,
            sentiment_scalar=sentiment_scalar,
            timestamp=_to_datetime(timestamp),
        )

    def _settings_for_run(self, initial_equity: float) -> dict[str, Any]:
        run_settings = copy.deepcopy(self.settings)
        run_settings.setdefault("broker", {})
        run_settings["broker"]["paper_trade"] = True
        run_settings["broker"]["paper_initial_equity"] = initial_equity
        run_settings.setdefault("data", {})
        if "db_path" not in run_settings["data"]:
            run_settings["data"]["db_path"] = str(Path(tempfile.mkdtemp()) / "backtest.sqlite3")
        return run_settings


def build_backtest_result(
    *,
    equity_curve: list[float],
    trades: list[dict[str, Any]],
    start_date: datetime,
    end_date: datetime,
    instrument: str,
    mode: str,
    initial_equity: float,
    total_bars: int,
    total_signals: int,
    signals_approved: int,
    signals_rejected: int,
    rejection_reasons: dict[str, int],
) -> BacktestResult:
    if not equity_curve:
        equity_curve = [initial_equity]
    net_trades = [_net_trade(trade) for trade in trades]
    final_equity = float(equity_curve[-1] - sum(trade.get("fee", 0.0) for trade in net_trades))
    adjusted_curve = _fee_adjusted_equity_curve(equity_curve, net_trades)
    drawdown_curve = _drawdown_curve(adjusted_curve)
    total_return_pct = (final_equity / initial_equity - 1.0) if initial_equity else 0.0
    cagr = _cagr(initial_equity, final_equity, start_date, end_date)
    sharpe = _sharpe(adjusted_curve)
    sortino = _sortino(adjusted_curve)
    max_drawdown = abs(min(drawdown_curve)) if drawdown_curve else 0.0
    calmar = _cap_metric(cagr / max_drawdown if max_drawdown else math.inf)
    pnl_values = [float(trade.get("pnl_after_fees", trade.get("pnl", 0.0))) for trade in net_trades]
    wins = [value for value in pnl_values if value > 0.0]
    losses = [value for value in pnl_values if value <= 0.0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = _cap_metric(gross_profit / gross_loss if gross_loss else math.inf)
    regimes = Counter(str(trade.get("regime", "RANGING")) for trade in net_trades)
    win_rate_by_regime = {
        regime: _win_rate([trade for trade in net_trades if trade.get("regime") == regime])
        for regime in ["TRENDING_UP", "TRENDING_DOWN", "RANGING"]
    }
    durations = [int(trade.get("duration_bars", 0)) for trade in net_trades]
    drawdown_values = [abs(value) for value in drawdown_curve if value < 0.0]
    rr_values = [
        float(trade.get("pnl_after_fees", trade.get("pnl", 0.0))) / float(trade.get("risk_amount", 1.0))
        for trade in net_trades
        if float(trade.get("risk_amount", 0.0)) > 0.0
    ]
    return BacktestResult(
        start_date=start_date,
        end_date=end_date,
        instrument=instrument,
        mode=mode,
        initial_equity=initial_equity,
        final_equity=final_equity,
        total_bars=total_bars,
        total_trades=len(net_trades),
        total_return_pct=total_return_pct,
        cagr=cagr,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        max_drawdown_pct=max_drawdown,
        max_drawdown_duration_bars=_max_drawdown_duration(drawdown_curve),
        avg_drawdown_pct=float(np.mean(drawdown_values)) if drawdown_values else 0.0,
        win_rate=len(wins) / len(net_trades) if net_trades else 0.0,
        profit_factor=profit_factor,
        avg_win_pct=float(np.mean([value / initial_equity for value in wins])) if wins else 0.0,
        avg_loss_pct=float(np.mean([value / initial_equity for value in losses])) if losses else 0.0,
        avg_rr_achieved=float(np.mean(rr_values)) if rr_values else 0.0,
        largest_win_pct=max([value / initial_equity for value in wins], default=0.0),
        largest_loss_pct=min([value / initial_equity for value in losses], default=0.0),
        avg_trade_duration_bars=int(round(float(np.mean(durations)))) if durations else 0,
        total_fees_paid=sum(float(trade.get("fee", 0.0)) for trade in net_trades),
        total_signals=total_signals,
        signals_approved=signals_approved,
        signals_rejected=signals_rejected,
        rejection_reasons=rejection_reasons,
        trades_in_trending_up=int(regimes["TRENDING_UP"]),
        trades_in_trending_down=int(regimes["TRENDING_DOWN"]),
        trades_in_ranging=int(regimes["RANGING"]),
        win_rate_by_regime=win_rate_by_regime,
        equity_curve=[float(value) for value in adjusted_curve],
        drawdown_curve=[float(value) for value in drawdown_curve],
        trades=net_trades,
    )


def _candle_from_row(timestamp: Any, ohlcv_row: pd.Series, feature_row: pd.Series) -> CandleRow:
    return CandleRow(
        timestamp=_to_datetime(timestamp),
        open=float(ohlcv_row["open"]),
        high=float(ohlcv_row["high"]),
        low=float(ohlcv_row["low"]),
        close=float(ohlcv_row["close"]),
        volume=float(ohlcv_row["volume"]),
        atr_14=float(feature_row["atr_14"]),
        adx_14=float(feature_row["adx_14"]),
        ema_9=float(feature_row["ema_9"]),
        ema_20=float(feature_row["ema_20"]),
        session_london=int(feature_row["session_london"]),
        session_ny=int(feature_row["session_ny"]),
        session_overlap=int(feature_row["session_overlap"]),
    )


def _basic_candle_from_ohlcv(timestamp: Any, row: pd.Series) -> CandleRow:
    return CandleRow(
        timestamp=_to_datetime(timestamp),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        atr_14=max(1e-9, float(row["high"] - row["low"])),
        adx_14=0.0,
        ema_9=float(row["close"]),
        ema_20=float(row["close"]),
        session_london=1,
        session_ny=0,
        session_overlap=0,
    )


def _slice_htf_frames(htf_frames: dict[str, pd.DataFrame] | None, timestamp: Any) -> dict[str, pd.DataFrame] | None:
    if not htf_frames:
        return None
    return {key: frame.loc[frame.index <= timestamp] for key, frame in htf_frames.items()}


def _augment_trade(trade: dict[str, Any], meta: dict[str, Any], bar_index: int, settings: dict[str, Any]) -> dict[str, Any]:
    result = dict(trade)
    result.update(meta)
    result.setdefault("regime", meta.get("regime", "RANGING"))
    result.setdefault("open_bar", meta.get("open_bar", bar_index))
    result["close_bar"] = bar_index
    result["duration_bars"] = max(0, bar_index - int(result.get("open_bar", bar_index)))
    lot_size = float(result.get("lot_size", 0.0))
    fee = (
        2.0
        * float(settings.get("execution", {}).get("paper_spread_pips", 1.5))
        * float(settings.get("risk", {}).get("pip_value_per_lot", 1.0))
        * lot_size
    )
    result["fee"] = fee
    result["pnl_after_fees"] = float(result.get("pnl", 0.0)) - fee
    return result


def _net_trade(trade: dict[str, Any]) -> dict[str, Any]:
    result = dict(trade)
    result.setdefault("fee", 0.0)
    result.setdefault("pnl_after_fees", float(result.get("pnl", 0.0)) - float(result.get("fee", 0.0)))
    result.setdefault("regime", "RANGING")
    return result


def _fee_adjusted_equity_curve(equity_curve: list[float], trades: list[dict[str, Any]]) -> list[float]:
    adjusted = list(map(float, equity_curve))
    cumulative_fee = 0.0
    trades_by_close = Counter()
    for trade in trades:
        trades_by_close[int(trade.get("close_bar", len(adjusted) - 1))] += float(trade.get("fee", 0.0))
    for idx in range(len(adjusted)):
        cumulative_fee += trades_by_close[idx]
        adjusted[idx] -= cumulative_fee
    return adjusted


def _drawdown_curve(equity_curve: list[float]) -> list[float]:
    curve = pd.Series(equity_curve, dtype=float)
    rolling_max = curve.cummax().replace(0.0, np.nan)
    drawdown = (curve - rolling_max) / rolling_max
    return drawdown.fillna(0.0).tolist()


def _sharpe(equity_curve: list[float]) -> float:
    returns = pd.Series(equity_curve, dtype=float).pct_change().dropna()
    std = float(returns.std())
    if returns.empty or std == 0.0 or math.isnan(std):
        return 0.0
    return float((returns.mean() / std) * math.sqrt(252))


def _sortino(equity_curve: list[float]) -> float:
    returns = pd.Series(equity_curve, dtype=float).pct_change().dropna()
    downside = returns[returns < 0.0]
    if downside.empty:
        return 10.0
    std = float(downside.std())
    if std == 0.0 or math.isnan(std):
        return 0.0
    return _cap_metric(float((returns.mean() / std) * math.sqrt(252)))


def _cagr(initial_equity: float, final_equity: float, start_date: datetime, end_date: datetime) -> float:
    seconds = max(1.0, (end_date - start_date).total_seconds())
    years = seconds / (365.25 * 24 * 60 * 60)
    if initial_equity <= 0.0 or final_equity <= 0.0:
        return 0.0
    return float((final_equity / initial_equity) ** (1.0 / years) - 1.0)


def _max_drawdown_duration(drawdown_curve: list[float]) -> int:
    longest = 0
    current = 0
    for value in drawdown_curve:
        if value < 0.0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _win_rate(trades: list[dict[str, Any]]) -> float:
    if not trades:
        return 0.0
    return sum(1 for trade in trades if float(trade.get("pnl_after_fees", trade.get("pnl", 0.0))) > 0.0) / len(trades)


def _cap_metric(value: float) -> float:
    if math.isinf(value):
        return 10.0
    return float(max(-10.0, min(10.0, value)))


def _to_datetime(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(UTC)
    return timestamp.to_pydatetime().astimezone(UTC)


__all__ = ["BacktestEngine", "BacktestResult", "build_backtest_result"]
