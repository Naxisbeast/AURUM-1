"""Walk-forward validation for AURUM-1 backtests."""

from __future__ import annotations

import copy
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from aurum1.backtesting.engine import BacktestEngine, BacktestResult
from aurum1.features.engineer import FeatureEngineer
from aurum1.signals import MachineMode


@dataclass
class WalkForwardResult:
    windows: list[BacktestResult]
    mean_sharpe: float
    mean_profit_factor: float
    mean_win_rate: float
    mean_max_drawdown: float
    mean_cagr: float
    std_sharpe: float
    std_max_drawdown: float
    positive_window_rate: float
    worst_window_max_drawdown: float
    criteria_passed: int
    criteria_detail: dict[str, bool]
    promotion_gate_passed: bool


class WalkForwardValidator:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings

    def run(
        self,
        ohlcv: pd.DataFrame,
        macro: pd.DataFrame,
        cot: pd.DataFrame,
        htf_frames: dict[str, pd.DataFrame] | None = None,
        mode: MachineMode = MachineMode.RULE_REGIME,
        initial_equity: float = 10000.0,
    ) -> WalkForwardResult:
        params = self.settings.get("backtesting", {})
        train_bars = int(params.get("train_bars", 6552))
        test_bars = int(params.get("test_bars", 1638))
        step_bars = int(params.get("step_bars", test_bars))
        lock_geometry = bool(params.get("lock_geometry", False))
        allow_overlap = bool(params.get("allow_overlap", False))
        if step_bars < test_bars and not allow_overlap:
            raise ValueError("Overlapping walk-forward windows require backtesting.allow_overlap=true")
        windows: list[BacktestResult] = []
        start = 0
        if lock_geometry:
            # estimate how many OOS windows will be produced with the locked geometry
            est = 0
            s = 0
            while s + train_bars + test_bars <= len(ohlcv):
                est += 1
                s += step_bars
            if est < 3:
                raise RuntimeError("Locked walk-forward geometry requires at least 3 OOS windows; dataset too short")
        while start + train_bars + test_bars <= len(ohlcv):
            train = ohlcv.iloc[start : start + train_bars]
            test = ohlcv.iloc[start + train_bars : start + train_bars + test_bars]
            engine = BacktestEngine(self._window_settings())
            disable_ml = bool(engine.settings.get("backtesting", {}).get("disable_ml", False))
            if not disable_ml:
                try:
                    train_features = FeatureEngineer({"feature_engineering": {"lookahead_check": False}}).build_features(
                        train,
                        macro,
                        cot,
                        include_target=True,
                    )
                    if len(train_features) >= 20:
                        classifier = RegimeClassifier(engine.settings)
                        classifier.train(train_features, update_latest=False)
                        engine.regime_classifier = classifier
                        if bool(engine.settings.get("models", {}).get("enable_direction_predictor", False)):
                            if "label" in train_features.columns and len(train_features) >= 80:
                                predictor = DirectionPredictor(engine.settings)
                                predictor.train(train_features, update_latest=False)
                                engine.direction_predictor = predictor
                except Exception as exc:
                    logging.getLogger("aurum1.walk_forward").warning(
                        "Model training failed for window start=%s: %s",
                        start, exc,
                    )
            windows.append(engine.run(test, macro, cot, htf_frames=htf_frames, mode=mode, initial_equity=initial_equity))
            start += step_bars
        return _aggregate_windows(windows)

    def _window_settings(self) -> dict[str, Any]:
        settings = copy.deepcopy(self.settings)
        settings.setdefault("models", {})
        settings["models"]["model_dir"] = str(Path(tempfile.mkdtemp()) / "walk_forward_models")
        return settings


def _aggregate_windows(windows: list[BacktestResult]) -> WalkForwardResult:
    sharpes = np.asarray([window.sharpe_ratio for window in windows], dtype=float)
    profit_factors = np.asarray([window.profit_factor for window in windows], dtype=float)
    win_rates = np.asarray([window.win_rate for window in windows], dtype=float)
    drawdowns = np.asarray([window.max_drawdown_pct for window in windows], dtype=float)
    cagrs = np.asarray([window.cagr for window in windows], dtype=float)
    mean_sharpe = float(np.mean(sharpes)) if sharpes.size else 0.0
    mean_profit_factor = float(np.mean(profit_factors)) if profit_factors.size else 0.0
    mean_win_rate = float(np.mean(win_rates)) if win_rates.size else 0.0
    mean_drawdown = float(np.mean(drawdowns)) if drawdowns.size else 0.0
    positive_window_rate = float(np.mean(sharpes > 0.0)) if sharpes.size else 0.0
    worst_window_max_drawdown = float(np.max(drawdowns)) if drawdowns.size else 0.0
    criteria_detail = {
        "mean_sharpe": mean_sharpe > 0.50,
        "mean_profit_factor": mean_profit_factor > 1.30,
        "mean_win_rate": mean_win_rate > 0.50,
        "mean_max_drawdown": mean_drawdown < 0.05,
        "worst_window_max_drawdown": worst_window_max_drawdown < 0.10,
        "positive_window_rate": positive_window_rate > 0.80,
    }
    criteria_passed = sum(1 for passed in criteria_detail.values() if passed)
    return WalkForwardResult(
        windows=windows,
        mean_sharpe=mean_sharpe,
        mean_profit_factor=mean_profit_factor,
        mean_win_rate=mean_win_rate,
        mean_max_drawdown=mean_drawdown,
        mean_cagr=float(np.mean(cagrs)) if cagrs.size else 0.0,
        std_sharpe=float(np.std(sharpes)) if sharpes.size else 0.0,
        std_max_drawdown=float(np.std(drawdowns)) if drawdowns.size else 0.0,
        positive_window_rate=positive_window_rate,
        worst_window_max_drawdown=worst_window_max_drawdown,
        criteria_passed=criteria_passed,
        criteria_detail=criteria_detail,
        promotion_gate_passed=criteria_passed >= 5,
    )


__all__ = ["WalkForwardResult", "WalkForwardValidator"]
