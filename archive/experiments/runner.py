"""Run a complete experiment: backtest, walk-forward, stress test, compare.

Full validation pipeline for one strategy change against the D4 baseline.
"""

from __future__ import annotations

import copy
import logging
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from aurum1.backtesting.engine import BacktestEngine, BacktestResult
from aurum1.data.ingestion import load_ohlcv, load_settings
from experiments.models import (
    ExperimentConfig,
    ExperimentResult,
    MetricComparison,
    MonteCarloResult,
    StressTestResult,
    WalkForwardMetrics,
)
from experiments.stress_test import run_stress_tests
from experiments.compare import compare_to_baseline, compute_mc_summary

LOGGER = logging.getLogger("aurum1.experiments")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKET_DB = ROOT / "aurum1" / "data" / "combined_market_cache.sqlite3"

# ── Baseline D4 metrics (11-year full backtest) ──────────────────────────
BASELINE_METRICS: dict[str, float] = {
    "profit_factor": 1.134,
    "sharpe": 0.75,
    "sortino": 0.95,
    "max_drawdown": 0.165,
    "win_rate": 0.369,
    "total_net_pnl": 42131.0,
    "avg_r": 0.087,
    "trade_count": 8178,
}


class ExperimentRunner:
    """Run and validate a single strategy change."""

    def __init__(
        self,
        settings_path: str | Path | None = None,
        market_db_path: str | Path | None = None,
    ):
        self.settings_path = Path(settings_path or (ROOT / "aurum1" / "config" / "settings.yaml"))
        self.market_db_path = Path(market_db_path or DEFAULT_MARKET_DB)
        self.settings = load_settings(self.settings_path)
        self._ohlcv: pd.DataFrame | None = None
        self._macro: pd.DataFrame | None = None
        self._cot: pd.DataFrame | None = None

    def run(self, config: ExperimentConfig) -> ExperimentResult:
        """Run a full experiment: backtest, walk-forward, stress test, compare."""
        start_time = time.time()
        exp_id = str(datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_" + config.name[:20])

        LOGGER.info("Running experiment: %s (%s)", config.name, config.description)

        # Load data once
        ohlcv, macro, cot = self._load_data()

        # Create modified settings
        modified_settings = self._apply_overrides(config.settings_overrides)

        # ── 1. Full 11-year backtest ──
        LOGGER.info("  Running full backtest...")
        try:
            result = self._run_backtest(modified_settings, ohlcv, macro, cot)
        except Exception as e:
            LOGGER.error("  Backtest failed: %s", e)
            return ExperimentResult(
                experiment_id=exp_id,
                config=config,
                created_at=datetime.now(UTC).isoformat(),
                status="error",
                duration_seconds=time.time() - start_time,
            )

        # ── 2. Walk-forward validation ──
        LOGGER.info("  Running walk-forward...")
        wf_metrics = self._run_walk_forward(modified_settings, ohlcv, macro, cot)

        # ── 3. Stress tests ──
        LOGGER.info("  Running stress tests...")
        stress_results = run_stress_tests(modified_settings, ohlcv, macro, cot)

        # ── 4. Monte Carlo ──
        LOGGER.info("  Running Monte Carlo...")
        mc_result = self._run_monte_carlo(result)

        # ── 5. Compare vs baseline ──
        LOGGER.info("  Comparing vs baseline...")
        metric_comparisons = compare_to_baseline(result, BASELINE_METRICS)

        # ── 6. Evaluate gates ──
        gates, gates_passed = self._evaluate_gates(
            result, metric_comparisons, stress_results, mc_result, wf_metrics
        )

        elapsed = time.time() - start_time
        LOGGER.info("  Done: %d gates passed (%.1fs)", gates_passed, elapsed)

        return ExperimentResult(
            experiment_id=exp_id,
            config=config,
            created_at=datetime.now(UTC).isoformat(),
            status="passed" if gates_passed >= 5 else "failed",
            duration_seconds=elapsed,
            trade_count=result.total_trades,
            profit_factor=result.profit_factor,
            sharpe=result.sharpe_ratio,
            sortino=result.sortino_ratio,
            max_drawdown=result.max_drawdown_pct,
            win_rate=result.win_rate,
            total_net_pnl=result.total_net_pnl,
            avg_r=float(
                np.mean([
                    float(t.get("net_pnl", 0)) / max(float(t.get("risk_amount", 1)), 1e-9)
                    for t in result.trades
                    if float(t.get("risk_amount", 0)) > 0
                ])
            ) if result.trades else 0.0,
            metric_comparisons=metric_comparisons,
            walk_forward=wf_metrics,
            stress_tests=stress_results,
            monte_carlo=mc_result,
            gates_passed=gates_passed,
            gates_total=7,
            gate_details=gates,
        )

    def _load_data(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Load market data once and cache it."""
        if self._ohlcv is not None:
            return self._ohlcv, self._macro, self._cot

        LOGGER.info("Loading data from %s...", self.market_db_path)

        ohlcv = load_ohlcv("M15", self.market_db_path)
        if ohlcv.empty:
            raise RuntimeError(f"No M15 data in {self.market_db_path}")

        macro = self._load_macro_data()
        cot = self._load_cot_data()

        # Cache
        self._ohlcv = ohlcv
        self._macro = macro
        self._cot = cot

        LOGGER.info("  Loaded %d M15 candles (%s to %s)",
                     len(ohlcv), ohlcv.index[0], ohlcv.index[-1])
        return ohlcv, macro, cot

    def _load_macro_data(self) -> pd.DataFrame:
        """Load macro data, filling NaN values so merge_macro_onto_ohlcv doesn't fail.

        DXY/VIX data may only be available from 2024 onward (yfinance source).
        NaNs before that are filled with 0 to indicate neutral macro conditions,
        which is safe for a causal backtest.
        """
        from aurum1.data.ingestion import load_macro

        macro_db = self.market_db_path
        # Fallback: try other DBs that may have better macro coverage
        if not Path(macro_db).exists() or not _has_macro_table(macro_db):
            alt_paths = [
                Path("aurum1/data/aurum1.sqlite3"),
                Path("aurum1/data/backtest_market_cache.sqlite3"),
            ]
            for alt in alt_paths:
                if alt.exists() and _has_macro_table(str(alt)):
                    macro_db = alt
                    LOGGER.info("  Using macro data from %s", alt)
                    break

        try:
            macro = load_macro(macro_db)
        except Exception:
            LOGGER.warning("  Macro data not available, using empty frame")
            macro = pd.DataFrame(columns=[
                "real_yield", "dxy_daily_return", "vix_level", "vix_1d_change"
            ])
            macro.index = pd.DatetimeIndex([], tz=UTC, name="date")
            return macro

        if macro.empty:
            return macro

        # Fill NaN values: forward-fill then any remaining (early period) with 0
        macro = macro.ffill().fillna(0.0)

        # Ensure all required columns exist
        for col in ["real_yield", "dxy_daily_return", "vix_level", "vix_1d_change"]:
            col_aliases = {"vix_level": "vix"}
            source_col = col_aliases.get(col, col)
            if source_col in macro.columns and col != source_col:
                macro[col] = macro[source_col]
            elif col not in macro.columns:
                macro[col] = 0.0

        return macro

    def _load_cot_data(self) -> pd.DataFrame:
        """Load COT data from the same DB as OHLCV."""
        try:
            from aurum1.data.ingestion import load_cot
            return load_cot(self.market_db_path)
        except Exception:
            LOGGER.warning("  COT data not available, using empty frame")
            empty = pd.DataFrame(columns=["cot_net_long_pct"])
            empty.index = pd.DatetimeIndex([], tz=UTC, name="report_date")
            return empty

    def _apply_overrides(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge settings overrides into base settings."""
        merged = copy.deepcopy(self.settings)
        self._deep_merge(merged, overrides)
        return merged

    def _deep_merge(self, base: dict, overrides: dict) -> None:
        """Recursive dict merge."""
        for key, value in overrides.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = copy.deepcopy(value)

    def _run_backtest(
        self,
        settings: dict[str, Any],
        ohlcv: pd.DataFrame,
        macro: pd.DataFrame,
        cot: pd.DataFrame,
    ) -> BacktestResult:
        """Run a full backtest and return the result."""
        from aurum1.signals import MachineMode

        engine = BacktestEngine(settings)
        return engine.run(
            ohlcv=ohlcv,
            macro=macro,
            cot=cot,
            mode=MachineMode.RULE_REGIME,
            initial_equity=10000.0,
        )

    def _run_walk_forward(
        self,
        settings: dict[str, Any],
        ohlcv: pd.DataFrame,
        macro: pd.DataFrame,
        cot: pd.DataFrame,
    ) -> WalkForwardMetrics:
        """Run a 20-bar walk-forward validation."""
        from aurum1.backtesting.walk_forward import WalkForwardValidator
        from aurum1.signals import MachineMode

        # Use 20-bar walk-forward parameters
        wf_settings = copy.deepcopy(settings)
        wf_settings.setdefault("backtesting", {})
        wf_settings["backtesting"]["train_bars"] = 6552
        wf_settings["backtesting"]["test_bars"] = 1638
        wf_settings["backtesting"]["step_bars"] = 1638
        wf_settings["backtesting"]["allow_overlap"] = False
        wf_settings["backtesting"]["lock_geometry"] = False

        validator = WalkForwardValidator(wf_settings)
        try:
            wf_result = validator.run(
                ohlcv=ohlcv,
                macro=macro,
                cot=cot,
                mode=MachineMode.RULE_REGIME,
                initial_equity=10000.0,
            )
        except Exception as e:
            LOGGER.warning("  Walk-forward failed: %s", e)
            return WalkForwardMetrics(
                window_count=0,
                positive_window_rate=0.0,
                mean_profit_factor=0.0,
                mean_sharpe=0.0,
                mean_win_rate=0.0,
                mean_max_drawdown=0.0,
                std_profit_factor=0.0,
                std_sharpe=0.0,
                pf_stability=0.0,
                pf_trend_slope=0.0,
            )

        windows_data = []
        pf_values = []
        sharpe_values = []
        for i, w in enumerate(wf_result.windows):
            windows_data.append({
                "window_index": i,
                "profit_factor": w.profit_factor,
                "sharpe": w.sharpe_ratio,
                "win_rate": w.win_rate,
                "max_drawdown": w.max_drawdown_pct,
                "net_pnl": w.total_net_pnl,
            })
            pf_values.append(w.profit_factor)
            sharpe_values.append(w.sharpe_ratio)

        n = len(pf_values)
        if n == 0:
            return WalkForwardMetrics(window_count=0, positive_window_rate=0.0, mean_profit_factor=0.0, mean_sharpe=0.0, mean_win_rate=0.0, mean_max_drawdown=0.0, std_profit_factor=0.0, std_sharpe=0.0, pf_stability=0.0, pf_trend_slope=0.0)

        pf_arr = np.asarray(pf_values)
        sharpe_arr = np.asarray(sharpe_values)
        mean_pf = float(np.mean(pf_arr))
        std_pf = float(np.std(pf_arr)) if n > 1 else 0.0

        # Stability: 1 - CV (lower CV = more stable)
        pf_stability = 1.0 - (std_pf / mean_pf) if mean_pf > 0 else 0.0

        # Trend: linear regression slope of PF over windows
        pf_trend_slope = 0.0
        if n > 2:
            x = np.arange(n)
            slope = np.polyfit(x, pf_arr, 1)[0]
            pf_trend_slope = float(slope)

        return WalkForwardMetrics(
            window_count=n,
            positive_window_rate=float(np.mean(pf_arr > 1.0)),
            mean_profit_factor=mean_pf,
            mean_sharpe=float(np.mean(sharpe_arr)),
            mean_win_rate=float(np.mean([w.win_rate for w in wf_result.windows])),
            mean_max_drawdown=float(np.mean([w.max_drawdown_pct for w in wf_result.windows])),
            std_profit_factor=std_pf,
            std_sharpe=float(np.std(sharpe_arr)) if n > 1 else 0.0,
            pf_stability=pf_stability,
            pf_trend_slope=pf_trend_slope,
            windows=windows_data,
        )

    def _run_monte_carlo(self, result: BacktestResult) -> MonteCarloResult:
        """Run Monte Carlo simulation on backtest trades."""
        from aurum1.backtesting.monte_carlo import run_monte_carlo

        trades = [t for t in result.trades if t.get("net_pnl", 0) != 0]
        if len(trades) < 50:
            return MonteCarloResult(
                n_simulations=0,
                median_final_equity=0,
                pct5_final_equity=0,
                pct95_final_equity=0,
                median_max_drawdown=0,
                pct95_max_drawdown=0,
                median_sharpe=0,
                ruin_probability=0,
                passed=False,
            )

        mc = run_monte_carlo(trades, n_simulations=10000, initial_equity=10000.0)
        return MonteCarloResult(
            n_simulations=mc.n_simulations,
            median_final_equity=mc.median_final_equity,
            pct5_final_equity=mc.pct5_final_equity,
            pct95_final_equity=mc.pct95_final_equity,
            median_max_drawdown=mc.median_max_drawdown,
            pct95_max_drawdown=mc.pct95_max_drawdown,
            median_sharpe=mc.median_sharpe,
            ruin_probability=mc.ruin_probability,
            passed=mc.ruin_probability < 0.01 and mc.median_max_drawdown < 0.25,
        )

    def _evaluate_gates(
        self,
        result: BacktestResult,
        comparisons: list[MetricComparison],
        stress_tests: list[StressTestResult],
        mc_result: MonteCarloResult | None,
        wf_metrics: WalkForwardMetrics | None,
    ) -> tuple[dict[str, bool], int]:
        """Evaluate decision gates."""
        pf_improvement = next(
            (c.absolute_change for c in comparisons if c.metric_name == "profit_factor"), 0.0
        )
        sharpe_improvement = next(
            (c.absolute_change for c in comparisons if c.metric_name == "sharpe"), 0.0
        )
        dd_change = next(
            (c.absolute_change for c in comparisons if c.metric_name == "max_drawdown"), 0.0
        )

        gates = {
            "G1: PF improvement > 0.05": pf_improvement > 0.05,
            "G2: Sharpe improvement > 0.08": sharpe_improvement > 0.08,
            "G3: No DD increase > 2pp": dd_change < 0.02,
            "G4: Walk-forward PF up in 60%+ windows": (
                wf_metrics is not None
                and wf_metrics.window_count > 0
                and wf_metrics.positive_window_rate >= 0.60
            ),
            "G5: Survives 2x cost stress": any(
                st.test_name == "2x_costs" and st.passed for st in stress_tests
            ),
            "G6: p < 0.05 on key metrics": any(
                c.is_significant for c in comparisons if c.metric_name in ("profit_factor", "sharpe")
            ),
            "G7: MC ruin < 1%": mc_result is not None and mc_result.passed,
        }

        gates_passed = sum(1 for v in gates.values() if v)
        return gates, gates_passed


def _has_macro_table(db_path: str | Path) -> bool:
    """Check if the SQLite database has a macro_data table."""
    try:
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='macro_data'"
            ).fetchone()
            return row is not None
    except Exception:
        return False


__all__ = ["ExperimentRunner", "BASELINE_METRICS"]
