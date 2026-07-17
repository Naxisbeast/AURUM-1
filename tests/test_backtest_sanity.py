"""Sanity tests for backtest integrity.

These tests verify the backtest engine doesn't produce false positive
results on random or synthetic data. A strategy with PF significantly
above 1.0 on random data indicates lookahead bias.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aurum1.backtesting import BacktestEngine, BacktestResult
from aurum1.data.ingestion import initialize_database
from aurum1.features.engineer import FeatureEngineer
from aurum1.signals import MachineMode


def _make_random_walk_ohlcv(
    n_bars: int = 50000,
    seed: int = 42,
    start_price: float = 2000.0,
    daily_vol: float = 15.0,
    timeframe_minutes: int = 15,
) -> pd.DataFrame:
    """Generate synthetic OHLCV data from a geometric random walk.

    Parameters
    ----------
    n_bars : int
        Number of M15 bars to generate
    seed : int
        Random seed for reproducibility
    start_price : float
        Starting price level
    daily_vol : float
        Daily volatility in price units (~1 ATR for gold)
    timeframe_minutes : int
        Bar timeframe in minutes

    Returns
    -------
    pd.DataFrame with columns: open, high, low, close, volume
    Index: DatetimeIndex (UTC)
    """
    rng = np.random.RandomState(seed)

    # Scale daily vol to per-bar vol
    bars_per_day = 24 * 60 // timeframe_minutes
    bar_vol = daily_vol / np.sqrt(bars_per_day)

    # Generate close prices as random walk
    log_returns = rng.normal(0, bar_vol / start_price, n_bars)
    closes = start_price * np.exp(np.cumsum(log_returns))

    # Generate OHLC from close + intra-bar noise
    opens = np.concatenate([[start_price], closes[:-1]])
    intra_bar_range = bar_vol * rng.exponential(0.5, n_bars)
    highs = np.maximum(opens, closes) + intra_bar_range
    lows = np.minimum(opens, closes) - intra_bar_range

    # Ensure high >= low (rare edge case with tiny noise)
    for i in range(n_bars):
        if highs[i] < lows[i]:
            mid = (highs[i] + lows[i]) / 2
            spread = abs(highs[i] - lows[i]) / 2 + 0.01
            highs[i] = mid + spread
            lows[i] = mid - spread

    # Create timestamp index
    start = pd.Timestamp("2020-01-01", tz="UTC")
    timestamps = pd.date_range(start, periods=n_bars, freq="15min", tz="UTC")

    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": rng.randint(100, 1000, n_bars),
    }, index=timestamps)


def _settings_for_backtest() -> dict:
    """Return minimal settings dict for BacktestEngine."""
    return {
        "app": {"random_seed": 42},
        "broker": {
            "paper_trade": True,
            "paper_initial_equity": 10000.0,
            "oanda": {"instrument": "XAU_USD"},
        },
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
        "data": {
            "db_path": str(Path(tempfile.mkdtemp()) / "test_db.sqlite3"),
        },
        "risk": {
            "risk_per_trade_pct": 0.0025,
            "kelly_min_trades": 20,
            "kelly_default_fraction": 0.25,
            "kelly_max_fraction": 0.25,
            "max_spread_pips": 3.0,
            "daily_loss_kill_pct": 0.03,
            "total_drawdown_kill_pct": 0.08,
            "drawdown_recovery_threshold_pct": 0.05,
            "max_portfolio_risk_pct": 3.0,
            "pip_size": 0.01,
            "pip_value_per_lot": 1.0,
        },
        "execution": {
            "fill_timeout_candles": 3,
            "slippage_std_pips": 0.5,
            "paper_spread_pips": 1.5,
        },
        "signals": {
            "adx_threshold": 25,
            "min_pullback_candles": 1,
            "max_pullback_candles": 4,
            "armed_timeout_candles": 20,
            "window_expiry_candles": 6,
            "atr_sl_multiplier": 2.0,
            "atr_tp_multiplier": 3.0,
            "atr_breakout_buffer": 0.3,
            "require_session_filter": True,
        },
        "models": {
            "enable_direction_predictor": False,
            "enable_sentiment": False,
        },
        "backtesting": {
            "exit_mode": "FIXED",
            "train_bars": 2000,
            "test_bars": 1000,
            "step_bars": 1000,
            "allow_overlap": False,
            "lock_geometry": False,
            "disable_ml": True,
            "verify_feature_causality": False,
            "n_monte_carlo": 100,
            "min_trades_for_stats": 10,
        },
        "orchestrator": {
            "mode": "rule_only",
            "shadow_mode": False,
        },
        "feature_engineering": {"lookahead_check": True},
    }


def test_backtest_on_random_walk_profit_factor():
    """Run D4-style backtest on random-walk data. PF should be ≈ 1.0.

    A Donchian 20 breakout strategy on pure random walk data should not
    produce a systematic edge. If PF > 1.05, there is likely lookahead bias
    or a structural overfitting issue in the backtest engine.

    Because of spread and slippage costs, the expected PF on random data
    is slightly below 1.0 (costs are a net drag).
    """
    ohlcv = _make_random_walk_ohlcv(n_bars=5000, seed=42)
    # Macro dataframe with required columns
    macro_dates = ohlcv.index.normalize().unique()
    macro = pd.DataFrame({
        "dgs10": 4.0, "cpi": 300.0, "cpi_yoy": 3.0, "real_yield": 1.0,
        "dxy": 100.0, "dxy_daily_return": 0.0, "vix": 20.0, "vix_1d_change": 0.0,
    }, index=pd.DatetimeIndex(macro_dates, name="date"))
    cot = pd.DataFrame()
    settings = _settings_for_backtest()

    # Initialize database
    db_path = Path(settings["data"]["db_path"])
    initialize_database(db_path)

    engine = BacktestEngine(settings)
    result = engine.run(
        ohlcv=ohlcv,
        macro=macro,
        cot=cot,
        mode=MachineMode.RULE_ONLY,
        initial_equity=10000.0,
    )

    msg = (
        f"Random walk backtest produced abnormal PF={result.profit_factor:.3f} "
        f"(Sharpe={result.sharpe_ratio:.3f}, WinRate={result.win_rate:.3f}, "
        f"Trades={result.total_trades}). Expected ≈ 1.0 or slightly below "
        f"due to spread/slippage costs."
    )

    # On random data with costs, PF should be < 1.05
    # (could be slightly above 1.0 due to sampling, but 1.05 is a hard upper bound)
    assert result.profit_factor < 1.05, msg

    # On random data with our cost model, PF should typically be < 1.0
    # (costs create a net negative expectancy)
    # This is not strictly required (sampling can produce a winning window)
    # but it's a useful warning signal
    if result.profit_factor > 1.02:
        import logging
        logging.getLogger("aurum1.backtest").warning(
            f"Random walk PF={result.profit_factor:.3f} (Sharpe={result.sharpe_ratio:.3f}, "
            f"trades={result.total_trades}) — borderline. Verify no lookahead bias."
        )


def test_backtest_on_random_walk_win_rate():
    """Win rate on random-walk data should be near 50% or below.

    A Donchian breakout on random noise should not have a systematically
    elevated win rate. The 2R exit means the win rate will naturally be
    lower (~33%), but any significant deviation from expected is suspicious.
    """
    ohlcv = _make_random_walk_ohlcv(n_bars=5000, seed=99)
    macro_dates = ohlcv.index.normalize().unique()
    macro = pd.DataFrame({
        "dgs10": 4.0, "cpi": 300.0, "cpi_yoy": 3.0, "real_yield": 1.0,
        "dxy": 100.0, "dxy_daily_return": 0.0, "vix": 20.0, "vix_1d_change": 0.0,
    }, index=pd.DatetimeIndex(macro_dates, name="date"))
    cot = pd.DataFrame()
    settings = _settings_for_backtest()

    db_path = Path(settings["data"]["db_path"])
    initialize_database(db_path)

    engine = BacktestEngine(settings)
    result = engine.run(
        ohlcv=ohlcv,
        macro=macro,
        cot=cot,
        mode=MachineMode.RULE_ONLY,
        initial_equity=10000.0,
    )

    # On random data with 2R exit, win rate should be around 33%
    # But it could vary. A win rate > 50% on random data is suspicious.
    if result.win_rate > 0.50 and result.total_trades > 50:
        pytest.fail(
            f"Random walk win rate={result.win_rate:.3f} with {result.total_trades} "
            f"trades is suspiciously high for random data (PF={result.profit_factor:.3f})."
        )
