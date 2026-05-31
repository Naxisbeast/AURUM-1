from __future__ import annotations

import pandas as pd

from scripts.research_edge_prototypes import ResearchSignal, research_verdict, run_signal_backtest


def _settings() -> dict:
    return {
        "broker": {
            "paper_trade": True,
            "paper_initial_equity": 10000.0,
            "oanda": {"instrument": "XAU_USD"},
        },
        "execution": {"paper_spread_pips": 1.5, "slippage_std_pips": 0.0},
        "risk": {
            "risk_per_trade_pct": 0.01,
            "kelly_default_fraction": 0.25,
            "kelly_min_trades": 20,
            "max_spread_pips": 10.0,
            "max_portfolio_risk_pct": 3.0,
            "daily_loss_kill_pct": 0.03,
            "total_drawdown_kill_pct": 0.08,
            "drawdown_recovery_threshold_pct": 0.05,
            "pip_size": 0.01,
            "min_lot_size": 0.01,
            "max_lot_size": 10.0,
            "lot_step": 0.01,
        },
        "instruments": {
            "XAU_USD": {
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
    }


def test_research_signal_backtest_uses_next_open_fill_metadata() -> None:
    index = pd.date_range("2026-01-01", periods=3, freq="15min", tz="UTC")
    ohlcv = pd.DataFrame(
        {
            "open": [100.0, 101.0, 101.5],
            "high": [100.5, 101.4, 103.0],
            "low": [99.8, 100.6, 101.0],
            "close": [100.2, 101.2, 102.8],
            "volume": [1.0, 1.0, 1.0],
        },
        index=index,
    )
    signal = ResearchSignal(
        strategy="trend_pullback_continuation",
        signal_bar=0,
        entry_bar=1,
        signal_time=index[0].isoformat(),
        entry_time=index[1].isoformat(),
        direction="BUY",
        atr_at_signal=1.0,
        stop_loss=100.0,
        take_profit=102.0,
        entry_price=101.0,
        reason="test",
    )

    result = run_signal_backtest(
        "trend_pullback_continuation",
        ohlcv,
        [signal],
        _settings(),
        initial_equity=10000.0,
        max_one_position=True,
    )

    assert result.total_trades == 1
    trade = result.trades[0]
    assert trade["fill_type"] == "next_open"
    assert trade["market_open_time"] == index[1].isoformat()
    assert trade["signal_time"] == index[0].isoformat()
    assert trade["requested_entry_price"] == 101.0
    assert trade["reason"] == "take_profit"


def test_research_verdict_never_marks_paper_ready() -> None:
    summary = {
        "results": {
            "trend_pullback_continuation": {
                "net_pnl": 1000.0,
                "profit_factor": 1.5,
                "sharpe": 1.2,
                "top_3_month_share_of_net": 0.75,
                "positive_months": 14,
            },
            "ema_trend_long_next_open": {"net_pnl": 200.0},
        },
        "random_control_distribution": {"runs": 20, "pct95_net_pnl": 800.0},
        "walk_forward": {
            "strategies": {
                "trend_pullback_continuation": {
                    "positive_net_windows": 16,
                    "mean_profit_factor": 1.4,
                    "total_net_pnl": 1200.0,
                },
                "random_matched_long_next_open": {"total_net_pnl": 900.0},
            }
        },
        "cost_stress": {"3x": {"profit_factor": 1.2}},
    }

    verdict = research_verdict(summary)

    assert verdict["status"] == "promising_research_lead_not_paper_ready"
    assert verdict["paper_readiness"] == "failed"
    assert verdict["live_readiness"] == "failed"
    assert "month_concentration_below_50pct" in verdict["failed_gates"]
