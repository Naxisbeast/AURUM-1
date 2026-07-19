from __future__ import annotations

import pandas as pd

from scripts.research.donchian_research_runner import donchian_signals, htf_bull_slope_filter, verdict


def _frame(rows: int = 30) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = pd.date_range("2026-01-01", periods=rows, freq="15min", tz="UTC")
    ohlcv = pd.DataFrame(
        {
            "open": [100.0] * rows,
            "high": [100.0] * rows,
            "low": [99.0] * rows,
            "close": [99.5] * rows,
            "volume": [1.0] * rows,
        },
        index=index,
    )
    ohlcv.iloc[21, ohlcv.columns.get_loc("close")] = 101.0
    ohlcv.iloc[22, ohlcv.columns.get_loc("open")] = 101.25
    features = ohlcv.copy()
    features["atr_14"] = 1.0
    features["H4_ema_200"] = 100.0
    features["H4_close"] = 101.0
    return ohlcv, features


def test_donchian_signals_use_twenty_bar_breakout_and_next_open() -> None:
    ohlcv, features = _frame()

    signals = donchian_signals(ohlcv, features, lookback=20, htf_filter=False)

    assert len(signals) == 1
    signal = signals[0]
    assert signal.signal_bar == 21
    assert signal.entry_bar == 22
    assert signal.entry_price == 101.25
    assert signal.stop_loss == 99.25
    assert signal.take_profit == 105.25


def test_htf_bull_slope_filter_requires_rising_h4_ema200() -> None:
    index = pd.date_range("2026-01-01", periods=100, freq="15min", tz="UTC")
    features = pd.DataFrame(index=index)
    features["H4_ema_200"] = range(100)
    features["H4_close"] = 200.0

    mask = htf_bull_slope_filter(features)

    assert bool(mask.iloc[-1]) is True


def test_donchian_verdict_requires_beating_random_controls() -> None:
    summary = {
        "best_variant": {"summary": {"net_pnl": 100.0, "sharpe": 1.0, "profit_factor": 1.3}},
        "random_control_distribution": {"pct95_net_pnl": 200.0},
        "yearly": {
            "2020": {"net_pnl": 1.0},
            "2021": {"net_pnl": 1.0},
            "2022": {"net_pnl": 1.0, "profit_factor": 1.2},
            "2023": {"net_pnl": 1.0},
            "2024": {"net_pnl": 1.0},
        },
        "cost_stress": {
            "base": {"net_pnl": 100.0},
            "3x": {"net_pnl": 90.0},
            "trade_count_locked": True,
        },
    }

    result = verdict(summary)

    assert result["status"] == "not_validated"
    assert result["gates"]["beats_random_95th_pct_net_pnl"] is False
    assert result["paper_readiness"] == "failed"
