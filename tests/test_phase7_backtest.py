from __future__ import annotations

import math
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aurum1.backtesting import (
    BacktestEngine,
    BacktestResult,
    WalkForwardValidator,
    run_ablation_backtest,
    run_monte_carlo,
)
from aurum1.backtesting.engine import build_backtest_result
from aurum1.backtesting.report import plot_equity_curve, print_backtest_report
from aurum1.data.ingestion import initialize_database
from aurum1.execution import PaperBroker
from aurum1.risk import AccountState, RiskOrder
from aurum1.signals import CandleRow, TradeInstruction
from aurum1.signals import MachineMode
from scripts.run_backtest import validate_backtest_history


_CACHED_RESULT: BacktestResult | None = None


def synthetic_ohlcv(rows: int = 500) -> pd.DataFrame:
    index = pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="15min", tz=UTC)
    close = np.zeros(rows, dtype=float)
    close[0] = 2300.0
    for idx in range(1, rows):
        drift = 0.18 if idx < rows // 2 else -0.18
        wave = 0.9 * math.sin(idx / 3.0)
        close[idx] = close[idx - 1] + drift + wave * 0.08
    open_ = np.roll(close, 1)
    open_[0] = close[0] - 0.2
    for idx in range(205, rows, 12):
        open_[idx] = close[idx] + 1.2
    high = np.maximum(open_, close) + 1.8
    low = np.minimum(open_, close) - 1.8
    volume = 1000.0 + (np.arange(rows) % 30) * 10.0
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "source": "synthetic",
            "instrument": "XAU_USD",
        },
        index=index,
    )


def synthetic_macro(ohlcv: pd.DataFrame) -> pd.DataFrame:
    index = pd.date_range(ohlcv.index.min().normalize(), ohlcv.index.max().normalize() + pd.Timedelta(days=1), freq="D", tz=UTC)
    step = np.arange(len(index), dtype=float)
    return pd.DataFrame(
        {
            "dgs10": 4.0 + step * 0.01,
            "cpi": 315.0 + step * 0.1,
            "cpi_yoy": 3.0 + step * 0.01,
            "real_yield": 1.0 + step * 0.002,
            "dxy": 104.0 + step * 0.03,
            "dxy_daily_return": np.sin(step / 5.0) * 0.001,
            "vix": 16.0 + np.cos(step / 4.0),
            "vix_1d_change": np.sin(step / 3.0) * 0.1,
        },
        index=index,
    )


def synthetic_cot(ohlcv: pd.DataFrame) -> pd.DataFrame:
    index = pd.date_range(ohlcv.index.min().normalize() - pd.Timedelta(days=14), ohlcv.index.max().normalize(), freq="7D", tz=UTC)
    return pd.DataFrame(
        {
            "market_name": "GOLD - COMMODITY EXCHANGE INC.",
            "open_interest": 200000.0,
            "long_positions": 120000.0,
            "short_positions": 70000.0,
            "net_positioning": 50000.0,
            "cot_net_long_pct": 0.20,
            "source": "synthetic",
        },
        index=index,
    )


def settings(overrides: dict | None = None) -> dict:
    data_dir = Path(tempfile.mkdtemp())
    config = {
        "general": {"random_seed": 9},
        "data": {"db_path": str(data_dir / "backtest.sqlite3")},
        "broker": {
            "paper_trade": True,
            "paper_initial_equity": 10000.0,
            "oanda": {"instrument": "XAU_USD"},
        },
        "risk": {
            "risk_per_trade_pct": 0.01,
            "kelly_min_trades": 20,
            "kelly_default_fraction": 0.25,
            "kelly_cap": 0.25,
            "kelly_max_fraction": 0.25,
            "max_spread_pips": 3.0,
            "daily_loss_kill_pct": 0.03,
            "total_drawdown_kill_pct": 0.08,
            "drawdown_recovery_threshold_pct": 0.05,
            "max_portfolio_risk_pct": 3.0,
            "pip_value_per_lot": 1.0,
            "pip_size": 0.01,
            "min_lot_size": 0.01,
            "max_lot_size": 10.0,
            "lot_step": 0.01,
        },
        "signals": {
            "adx_threshold": 10,
            "min_pullback_candles": 1,
            "max_pullback_candles": 4,
            "armed_timeout_candles": 20,
            "window_expiry_candles": 6,
            "atr_sl_multiplier": 2.0,
            "atr_tp_multiplier": 3.0,
            "atr_breakout_buffer": 0.1,
            "require_session_filter": False,
        },
        "execution": {"fill_timeout_candles": 3, "slippage_std_pips": 0.0, "paper_spread_pips": 1.5},
        "backtesting": {"train_bars": 300, "test_bars": 100, "step_bars": 100, "allow_overlap": False, "n_monte_carlo": 1000},
        "models": {"direction": {"max_epochs": 1, "sequence_length": 10, "batch_size": 64, "patience": 1}},
    }
    if overrides:
        for section, values in overrides.items():
            config.setdefault(section, {}).update(values)
    return config


def run_synthetic_backtest() -> BacktestResult:
    global _CACHED_RESULT
    if _CACHED_RESULT is None:
        ohlcv = synthetic_ohlcv(500)
        _CACHED_RESULT = BacktestEngine(settings()).run(ohlcv, synthetic_macro(ohlcv), synthetic_cot(ohlcv))
    return _CACHED_RESULT


def test_backtest_runs_without_error() -> None:
    result = run_synthetic_backtest()

    assert isinstance(result, BacktestResult)
    assert result.total_bars == 500
    assert result.final_equity > 0


def test_backtest_equity_curve_length() -> None:
    assert len(run_synthetic_backtest().equity_curve) == 500


def test_backtest_no_lookahead_bias() -> None:
    ohlcv_a = synthetic_ohlcv(500)
    ohlcv_b = ohlcv_a.copy()
    for column in ["open", "high", "low", "close", "volume"]:
        ohlcv_b.iloc[250:, ohlcv_b.columns.get_loc(column)] = 0.0
    result_a = BacktestEngine(settings()).run(ohlcv_a, synthetic_macro(ohlcv_a), synthetic_cot(ohlcv_a))
    result_b = BacktestEngine(settings()).run(ohlcv_b, synthetic_macro(ohlcv_b), synthetic_cot(ohlcv_b))
    cutoff = ohlcv_a.index[249].isoformat()
    trades_a = [(trade.get("open_time"), trade.get("direction"), round(float(trade.get("entry", 0.0)), 4)) for trade in result_a.trades if str(trade.get("open_time", "")) <= cutoff]
    trades_b = [(trade.get("open_time"), trade.get("direction"), round(float(trade.get("entry", 0.0)), 4)) for trade in result_b.trades if str(trade.get("open_time", "")) <= cutoff]

    assert trades_a == trades_b


def test_backtest_fees_applied() -> None:
    result = run_synthetic_backtest()

    assert result.total_trades >= 1
    assert result.total_fees_paid > 0


def test_backtest_cost_attribution_fields_populated() -> None:
    result = run_synthetic_backtest()

    assert result.total_trades >= 1
    assert result.total_gross_pnl == pytest.approx(sum(float(trade["gross_pnl"]) for trade in result.trades))
    assert result.total_net_pnl == pytest.approx(sum(float(trade["net_pnl"]) for trade in result.trades))
    assert result.total_spread_cost == pytest.approx(result.total_fees_paid)
    assert result.total_spread_cost > 0.0
    assert result.total_entry_slippage_cost == 0.0
    assert result.total_exit_slippage_cost == 0.0
    assert result.avg_units > 0.0
    assert result.median_units > 0.0
    assert result.min_units > 0.0
    assert result.max_units >= result.min_units
    assert all("intended_entry" in trade for trade in result.trades)
    assert all("actual_exit" in trade for trade in result.trades)
    assert all("units" in trade for trade in result.trades)


def test_backtest_records_exit_slippage_when_enabled() -> None:
    ohlcv = synthetic_ohlcv(500)
    result = BacktestEngine(settings({"execution": {"slippage_std_pips": 0.5}})).run(
        ohlcv,
        synthetic_macro(ohlcv),
        synthetic_cot(ohlcv),
    )

    assert result.total_trades >= 1
    assert result.total_entry_slippage_cost > 0.0
    assert result.total_exit_slippage_cost > 0.0
    assert result.total_slippage_cost == pytest.approx(
        result.total_entry_slippage_cost + result.total_exit_slippage_cost
    )


def test_backtest_report_separates_gross_net_and_costs(capsys: pytest.CaptureFixture[str]) -> None:
    result = run_synthetic_backtest()

    print_backtest_report(result)

    output = capsys.readouterr().out
    assert "Gross P&L:" in output
    assert "Avg units:" in output
    assert "Median units:" in output
    assert "Spread cost:" in output
    assert "Entry slip cost:" in output
    assert "Exit slip cost:" in output
    assert "Net P&L:" in output


def test_backtest_sharpe_formula() -> None:
    curve = [10000.0 * (1.001**idx) for idx in range(252)]
    result = build_backtest_result(
        equity_curve=curve,
        trades=[],
        start_date=pd.Timestamp("2026-01-01", tz=UTC).to_pydatetime(),
        end_date=pd.Timestamp("2026-12-31", tz=UTC).to_pydatetime(),
        instrument="XAU_USD",
        mode="rule_regime",
        initial_equity=10000.0,
        total_bars=len(curve),
        total_signals=0,
        signals_approved=0,
        signals_rejected=0,
        rejection_reasons={},
    )
    expected_returns = pd.Series(curve).pct_change().dropna()
    expected = expected_returns.mean() / expected_returns.std() * math.sqrt(252)

    assert result.sharpe_ratio == 0.0 if math.isnan(expected) else abs(result.sharpe_ratio - expected) < 0.05


def test_daily_sharpe_uses_daily_returns() -> None:
    intraday_curve = [10000.0, 10050.0, 10100.0, 10080.0, 10120.0, 10160.0]
    result = build_backtest_result(
        equity_curve=intraday_curve,
        trades=[],
        start_date=pd.Timestamp("2026-01-01T00:00:00Z").to_pydatetime(),
        end_date=pd.Timestamp("2026-01-03T23:45:00Z").to_pydatetime(),
        instrument="XAU_USD",
        mode="rule_regime",
        initial_equity=10000.0,
        total_bars=len(intraday_curve),
        total_signals=0,
        signals_approved=0,
        signals_rejected=0,
        rejection_reasons={},
    )
    daily_equity = pd.Series(
        intraday_curve,
        index=pd.date_range("2026-01-01T00:00:00Z", "2026-01-03T23:45:00Z", periods=len(intraday_curve)),
    ).resample("1D").last()
    expected_returns = daily_equity.pct_change().dropna()
    expected = expected_returns.mean() / expected_returns.std() * math.sqrt(252)

    assert result.sharpe_ratio == pytest.approx(expected, abs=0.001)


def test_backtest_max_drawdown_formula() -> None:
    result = build_backtest_result(
        equity_curve=[10000.0, 11000.0, 9000.0, 9500.0],
        trades=[],
        start_date=pd.Timestamp("2026-01-01", tz=UTC).to_pydatetime(),
        end_date=pd.Timestamp("2026-01-04", tz=UTC).to_pydatetime(),
        instrument="XAU_USD",
        mode="rule_regime",
        initial_equity=10000.0,
        total_bars=4,
        total_signals=0,
        signals_approved=0,
        signals_rejected=0,
        rejection_reasons={},
    )

    assert result.max_drawdown_pct == pytest.approx(2000.0 / 11000.0, abs=0.001)


def test_backtest_profit_factor_formula() -> None:
    result = build_backtest_result(
        equity_curve=[10000.0, 10100.0, 10050.0],
        trades=[{"pnl": 100.0}, {"pnl": -50.0}],
        start_date=pd.Timestamp("2026-01-01", tz=UTC).to_pydatetime(),
        end_date=pd.Timestamp("2026-01-03", tz=UTC).to_pydatetime(),
        instrument="XAU_USD",
        mode="rule_regime",
        initial_equity=10000.0,
        total_bars=3,
        total_signals=2,
        signals_approved=2,
        signals_rejected=0,
        rejection_reasons={},
    )

    assert result.profit_factor == pytest.approx(2.0, abs=0.001)


def test_backtest_win_rate_formula() -> None:
    trades = [{"pnl": 10.0}] * 6 + [{"pnl": -10.0}] * 4
    result = build_backtest_result(
        equity_curve=[10000.0] * 11,
        trades=trades,
        start_date=pd.Timestamp("2026-01-01", tz=UTC).to_pydatetime(),
        end_date=pd.Timestamp("2026-01-03", tz=UTC).to_pydatetime(),
        instrument="XAU_USD",
        mode="rule_regime",
        initial_equity=10000.0,
        total_bars=11,
        total_signals=10,
        signals_approved=10,
        signals_rejected=0,
        rejection_reasons={},
    )

    assert result.win_rate == pytest.approx(0.60, abs=0.001)


def test_backtest_rejection_reasons_logged() -> None:
    ohlcv = synthetic_ohlcv(500)
    result = BacktestEngine(settings({"execution": {"paper_spread_pips": 5.0}})).run(
        ohlcv,
        synthetic_macro(ohlcv),
        synthetic_cot(ohlcv),
    )

    assert result.signals_rejected > 0
    assert "spread_too_wide" in result.rejection_reasons


def test_walk_forward_produces_multiple_windows() -> None:
    ohlcv = synthetic_ohlcv(1000)
    result = WalkForwardValidator(settings()).run(ohlcv, synthetic_macro(ohlcv), synthetic_cot(ohlcv))

    assert len(result.windows) >= 2
    assert all(window.start_date < window.end_date for window in result.windows)
    assert isinstance(result.positive_window_rate, float)
    assert isinstance(result.worst_window_max_drawdown, float)
    assert isinstance(result.criteria_passed, int)
    assert isinstance(result.criteria_detail, dict)
    assert set(result.criteria_detail) == {
        "mean_sharpe",
        "mean_profit_factor",
        "mean_win_rate",
        "mean_max_drawdown",
        "worst_window_max_drawdown",
        "positive_window_rate",
    }


def test_walk_forward_defaults_non_overlapping() -> None:
    config = settings()

    assert config["backtesting"]["step_bars"] == config["backtesting"]["test_bars"]
    assert config["backtesting"]["allow_overlap"] is False


def test_backtest_history_gate_rejects_short_history() -> None:
    ohlcv = synthetic_ohlcv(500)

    with pytest.raises(RuntimeError, match="Insufficient backtest history"):
        validate_backtest_history(ohlcv, settings(), min_bars=20000, min_days=250.0)


def test_backtest_history_gate_allows_explicit_short_plumbing() -> None:
    ohlcv = synthetic_ohlcv(500)

    status = validate_backtest_history(
        ohlcv,
        settings(),
        allow_short_history=True,
        min_bars=20000,
        min_days=250.0,
    )

    assert status["short_history"] is True
    assert status["quantitative_readiness"] == "not_verified"


def test_backtest_uses_isolated_database_by_default() -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        runtime_db = Path(tempdir) / "runtime.sqlite3"
        initialize_database(runtime_db)
        config = settings({"data": {"db_path": str(runtime_db)}})
        ohlcv = synthetic_ohlcv(500)
        before = _count_trades(runtime_db)

        BacktestEngine(config).run(ohlcv, synthetic_macro(ohlcv), synthetic_cot(ohlcv))

        assert _count_trades(runtime_db) == before


def test_backtest_no_same_candle_exit_after_entry() -> None:
    ohlcv = synthetic_ohlcv(500)
    result = BacktestEngine(settings()).run(ohlcv, synthetic_macro(ohlcv), synthetic_cot(ohlcv))

    assert all(int(trade.get("duration_bars", 0)) >= 1 for trade in result.trades)


def test_same_candle_sl_tp_assumes_stop_first() -> None:
    broker = PaperBroker(settings())
    instruction = TradeInstruction(
        timestamp=pd.Timestamp("2026-01-01T00:00:00Z").to_pydatetime(),
        direction="BUY",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=105.0,
        atr_at_entry=2.0,
        signal_score=0.8,
        regime="TRENDING_UP",
        confidence=0.9,
        machine_mode="rule_regime",
    )
    order = RiskOrder(
        instruction=instruction,
        lot_size=0.01,
        risk_amount=5.0,
        risk_pct=0.05,
        kelly_fraction=0.25,
        approved=True,
        rejection_reason=None,
        portfolio_risk_after=0.05,
        units=1.0,
        notional_ounces=1.0,
    )
    broker.submit_order(order)
    broker.update_prices(
        CandleRow(
            timestamp=pd.Timestamp("2026-01-01T00:15:00Z").to_pydatetime(),
            open=100.0,
            high=106.0,
            low=94.0,
            close=100.0,
            volume=1.0,
            atr_14=2.0,
            adx_14=30.0,
            ema_9=101.0,
            ema_20=100.0,
            session_london=1,
            session_ny=0,
            session_overlap=0,
        )
    )

    assert broker._trade_history[-1]["reason"] == "stop_loss"


def _count_trades(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM trades_log").fetchone()[0])


def test_monte_carlo_produces_distribution() -> None:
    trades = [{"pnl_after_fees": value} for value in np.linspace(-50, 100, 50)]

    result = run_monte_carlo(trades, n_simulations=1000, initial_equity=10000.0)

    assert result.n_simulations == 1000
    assert result.pct5_final_equity < result.median_final_equity
    assert result.pct95_final_equity > result.median_final_equity
    assert 0.0 <= result.ruin_probability <= 1.0


def test_ablation_backtest_runs_all_modes() -> None:
    ohlcv = synthetic_ohlcv(500)

    result = run_ablation_backtest(ohlcv, synthetic_macro(ohlcv), synthetic_cot(ohlcv), settings())

    assert set(result) == {mode.value for mode in MachineMode}
    assert all(isinstance(value, BacktestResult) for value in result.values())


def test_backtest_regime_breakdown_populated() -> None:
    result = run_synthetic_backtest()

    assert result.trades_in_trending_up + result.trades_in_trending_down + result.trades_in_ranging == result.total_trades


def test_equity_curve_plot_saves_file() -> None:
    result = run_synthetic_backtest()
    with tempfile.TemporaryDirectory() as tempdir:
        path = Path(tempdir) / "equity.png"
        plot_equity_curve(result, path)

        assert path.exists()
        assert path.stat().st_size > 0
