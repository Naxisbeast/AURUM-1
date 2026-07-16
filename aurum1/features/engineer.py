"""Phase 2 feature engineering for AURUM-1.

The feature engineer consumes Phase 1 dataframes with UTC DatetimeIndexes and
computes technical, session, macro, COT, sentiment placeholder, confluence, and
optional target columns. It never reads raw SQLite tables or reparses OHLCV
timestamps; Phase 1 loaders own that contract.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from aurum1.data.ingestion import load_macro, load_ohlcv, merge_macro_onto_ohlcv


EMA_PERIODS = (9, 20, 50, 100, 200)
TARGET_COLUMNS = {"forward_return_5bar", "label"}
METADATA_COLUMNS = {"source", "instrument"}
MACRO_FEATURE_COLUMNS = ["real_yield", "dxy_daily_return", "vix_level", "vix_1d_change"]
SENTIMENT_COLUMNS = ["sentiment_bullish", "sentiment_bearish", "sentiment_neutral"]
WARMUP_BARS = 200
TARGET_HORIZON_BARS = 5


def assert_no_lookahead(
    feature_df: pd.DataFrame,
    source_close: pd.Series,
    min_lookbacks: dict[str, int],
) -> None:
    """Assert feature values do not appear before their minimum lookback is available."""

    source_index = pd.DatetimeIndex(source_close.index)
    feature_index = pd.DatetimeIndex(feature_df.index)
    missing_timestamps = feature_index.difference(source_index)
    if not missing_timestamps.empty:
        raise AssertionError(f"Feature index contains timestamps outside source data: {missing_timestamps[0]}")
    if len(feature_index) >= len(source_index) and feature_index.equals(source_index):
        raise AssertionError("Feature index must be a strict subset of the source index after warmup trimming")

    source_positions = {timestamp: position for position, timestamp in enumerate(source_index)}
    for column, min_lookback in min_lookbacks.items():
        if column in TARGET_COLUMNS or column not in feature_df.columns:
            continue
        non_null = feature_df[column].dropna()
        if non_null.empty:
            continue
        first_timestamp = non_null.index[0]
        source_position = source_positions.get(first_timestamp)
        if source_position is None:
            raise AssertionError(f"{column} has value at timestamp outside source data: {first_timestamp}")
        if source_position + 1 < min_lookback:
            raise AssertionError(
                f"{column} has non-NaN value at {first_timestamp} before "
                f"{min_lookback} source bars were available"
            )


class FeatureEngineer:
    """Builds deterministic feature matrices from Phase 1 market data contracts."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        feature_settings = settings.get("feature_engineering", {})
        self.lookahead_check = bool(feature_settings.get("lookahead_check", True))
        self.min_lookbacks: dict[str, int] = {}
        self._feature_names: list[str] = []

    def build_features(
        self,
        ohlcv: pd.DataFrame,
        macro: pd.DataFrame,
        cot: pd.DataFrame,
        sentiment: pd.DataFrame | None = None,
        htf_frames: dict[str, pd.DataFrame] | None = None,
        include_target: bool = False,
    ) -> pd.DataFrame:
        """Build the full Phase 2 feature matrix on an M15 OHLCV base frame."""

        self._validate_ohlcv_contract(ohlcv)
        frame = merge_macro_onto_ohlcv(ohlcv.copy(), macro.copy())
        frame = frame.rename(columns={"vix": "vix_level"})
        self._register_min_lookbacks({column: 1 for column in MACRO_FEATURE_COLUMNS if column in frame.columns})

        frame = self._merge_cot(frame, cot)
        frame = self._add_technical_features(frame)
        frame = self._add_session_and_time_features(frame)
        frame = self._add_sentiment_features(frame, sentiment)
        if htf_frames:
            frame = self._add_htf_confluence(frame, htf_frames)
        if include_target:
            frame = self._add_target(frame)

        output = frame.iloc[WARMUP_BARS:].copy()
        if include_target:
            output = output.iloc[:-TARGET_HORIZON_BARS].copy()

        output = output.replace([np.inf, -np.inf], np.nan)
        if output.isna().any().any():
            bad_columns = output.columns[output.isna().any()].tolist()
            raise ValueError(f"Feature matrix contains NaN after warmup in columns: {bad_columns}")

        self._feature_names = [
            column
            for column in output.columns
            if column not in METADATA_COLUMNS and column not in TARGET_COLUMNS
        ]
        if self.lookahead_check:
            assert_no_lookahead(output, ohlcv["close"], self.min_lookbacks)
        return output

    def get_feature_names(self) -> list[str]:
        """Return the exact model input column list from the latest feature build."""

        return list(self._feature_names)

    def _add_technical_features(self, frame: pd.DataFrame) -> pd.DataFrame:
        close = frame["close"].astype("float64")
        high = frame["high"].astype("float64")
        low = frame["low"].astype("float64")
        volume = frame["volume"].astype("float64")

        for period in EMA_PERIODS:
            ema_col = f"ema_{period}"
            slope_col = f"ema_{period}_slope"
            frame[ema_col] = _ema(close, period)
            frame[slope_col] = (frame[ema_col] - frame[ema_col].shift(1)) / close
            self._register_min_lookbacks({ema_col: period, slope_col: period + 1})

        alignment = pd.Series(0, index=frame.index, dtype="int64")
        for period in EMA_PERIODS:
            alignment = alignment + np.sign(close - frame[f"ema_{period}"]).fillna(0).astype("int64")
        frame["ema_alignment_score"] = alignment.astype("int64")
        self._register_min_lookbacks({"ema_alignment_score": max(EMA_PERIODS)})

        frame["atr_14"] = _atr_wilder(high, low, close, 14)
        frame["atr_percentile"] = frame["atr_14"].rolling(100, min_periods=100).apply(_last_rank_percentile, raw=False)
        self._register_min_lookbacks({"atr_14": 14, "atr_percentile": 113})

        frame["rsi_14"] = _rsi_wilder(close, 14)
        frame["rsi_divergence"] = _rsi_divergence(high, low, frame["rsi_14"], 14)
        self._register_min_lookbacks({"rsi_14": 14, "rsi_divergence": 28})

        macd_line = _ema(close, 12) - _ema(close, 26)
        frame["macd_line"] = macd_line
        frame["macd_signal"] = macd_line.ewm(span=9, adjust=False, min_periods=9).mean()
        frame["macd_histogram"] = frame["macd_line"] - frame["macd_signal"]
        frame["macd_momentum"] = frame["macd_histogram"] - frame["macd_histogram"].shift(1)
        self._register_min_lookbacks(
            {"macd_line": 26, "macd_signal": 34, "macd_histogram": 34, "macd_momentum": 35}
        )

        frame["bb_middle"] = close.rolling(20, min_periods=20).mean()
        rolling_std = close.rolling(20, min_periods=20).std()
        frame["bb_upper"] = frame["bb_middle"] + (2.0 * rolling_std)
        frame["bb_lower"] = frame["bb_middle"] - (2.0 * rolling_std)
        band_range = (frame["bb_upper"] - frame["bb_lower"]).replace(0.0, np.nan)
        frame["bb_pct_b"] = ((close - frame["bb_lower"]) / band_range).clip(-0.1, 1.1)
        frame["bb_width"] = (frame["bb_upper"] - frame["bb_lower"]) / frame["bb_middle"].replace(0.0, np.nan)
        self._register_min_lookbacks(
            {"bb_middle": 20, "bb_upper": 20, "bb_lower": 20, "bb_pct_b": 20, "bb_width": 20}
        )

        frame["adx_14"] = _adx_wilder(high, low, close, 14)
        self._register_min_lookbacks({"adx_14": 27})

        # ── New features (research additions) ──

        # Yang-Zhang volatility estimator (uses O,H,L,C for higher efficiency)
        frame["yang_zhang_vol"] = _yang_zhang_volatility(high, low, close, frame["open"], 14)
        self._register_min_lookbacks({"yang_zhang_vol": 14})

        # Kaufman Efficiency Ratio: net change / sum of absolute changes
        frame["efficiency_ratio"] = _kaufman_efficiency(close, 10)
        self._register_min_lookbacks({"efficiency_ratio": 10})

        # Breakout distance: how far price penetrated Donchian band, as % of ATR
        donchian_upper = high.rolling(20, min_periods=20).max().shift(1)
        donchian_lower = low.rolling(20, min_periods=20).min().shift(1)
        atr_safe = frame["atr_14"].replace(0.0, np.nan)
        frame["breakout_distance"] = np.where(
            close > donchian_upper,
            (close - donchian_upper) / atr_safe,
            np.where(close < donchian_lower, (donchian_lower - close) / atr_safe, 0.0),
        )
        self._register_min_lookbacks({"breakout_distance": 27})

        frame["rel_volume"] = volume / volume.rolling(20, min_periods=20).mean().replace(0.0, np.nan)
        frame["vwap_deviation"] = (close - _daily_vwap(close, volume)) / frame["atr_14"].replace(0.0, np.nan)
        self._register_min_lookbacks({"rel_volume": 20, "vwap_deviation": 14})
        return frame

    def _add_session_and_time_features(self, frame: pd.DataFrame) -> pd.DataFrame:
        hours = frame.index.hour
        dow = frame.index.dayofweek
        frame["session_asia"] = ((hours >= 0) & (hours < 8)).astype("int64")
        frame["session_london"] = ((hours >= 7) & (hours < 16)).astype("int64")
        frame["session_ny"] = ((hours >= 13) & (hours < 22)).astype("int64")
        frame["session_overlap"] = ((hours >= 13) & (hours < 16)).astype("int64")
        frame["hour_sin"] = np.sin(2.0 * np.pi * hours / 24.0)
        frame["hour_cos"] = np.cos(2.0 * np.pi * hours / 24.0)
        frame["dow_sin"] = np.sin(2.0 * np.pi * dow / 7.0)
        frame["dow_cos"] = np.cos(2.0 * np.pi * dow / 7.0)
        self._register_min_lookbacks(
            {
                "session_asia": 1,
                "session_london": 1,
                "session_ny": 1,
                "session_overlap": 1,
                "hour_sin": 1,
                "hour_cos": 1,
                "dow_sin": 1,
                "dow_cos": 1,
            }
        )
        return frame

    def _add_sentiment_features(self, frame: pd.DataFrame, sentiment: pd.DataFrame | None) -> pd.DataFrame:
        if sentiment is None or sentiment.empty:
            for column in SENTIMENT_COLUMNS:
                frame[column] = 0.0
        else:
            sentiment_work = sentiment.copy()
            if not isinstance(sentiment_work.index, pd.DatetimeIndex):
                raise ValueError("Sentiment input must have a DatetimeIndex")
            sentiment_work.index = _utc_index(sentiment_work.index)
            rename_map = {
                "bullish_score": "sentiment_bullish",
                "bearish_score": "sentiment_bearish",
                "neutral_score": "sentiment_neutral",
            }
            sentiment_work = sentiment_work.rename(columns=rename_map)
            for column in SENTIMENT_COLUMNS:
                if column not in sentiment_work.columns:
                    sentiment_work[column] = 0.0
            frame = _merge_asof_on_index(frame, sentiment_work[SENTIMENT_COLUMNS])
            frame[SENTIMENT_COLUMNS] = frame[SENTIMENT_COLUMNS].ffill().fillna(0.0)
        self._register_min_lookbacks({column: 1 for column in SENTIMENT_COLUMNS})
        return frame

    def _add_target(self, frame: pd.DataFrame) -> pd.DataFrame:
        frame["forward_return_5bar"] = frame["close"].shift(-TARGET_HORIZON_BARS) / frame["close"] - 1.0
        threshold = frame["atr_14"] / frame["close"]
        frame["label"] = np.select(
            [
                frame["forward_return_5bar"] > threshold,
                frame["forward_return_5bar"] < -threshold,
            ],
            [1, -1],
            default=0,
        ).astype("int64")
        return frame

    def _merge_cot(self, frame: pd.DataFrame, cot: pd.DataFrame) -> pd.DataFrame:
        if cot.empty or "cot_net_long_pct" not in cot.columns:
            frame["cot_net_long_pct"] = 0.0
            self._register_min_lookbacks({"cot_net_long_pct": 1})
            return frame
        cot_work = cot[["cot_net_long_pct"]].copy().sort_index()
        if not isinstance(cot_work.index, pd.DatetimeIndex):
            raise ValueError("COT input must have a DatetimeIndex")
        cot_work.index = _utc_index(cot_work.index)
        frame = _merge_asof_on_index(frame, cot_work)
        frame["cot_net_long_pct"] = pd.to_numeric(frame["cot_net_long_pct"], errors="coerce").ffill().fillna(0.0)
        self._register_min_lookbacks({"cot_net_long_pct": 1})
        return frame

    def _add_htf_confluence(self, frame: pd.DataFrame, htf_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
        for timeframe in ("H1", "H4"):
            if timeframe not in htf_frames:
                continue
            htf = self._compute_htf_features(htf_frames[timeframe], timeframe)
            frame = _merge_asof_on_index(frame, htf)
            frame[htf.columns] = frame[htf.columns].ffill()
            self._register_min_lookbacks({column: 1 for column in htf.columns})
        return frame

    def _compute_htf_features(self, frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        self._validate_ohlcv_contract(frame)
        close = frame["close"].astype("float64")
        high = frame["high"].astype("float64")
        low = frame["low"].astype("float64")
        htf_features = pd.DataFrame(index=frame.index)
        htf_alignment = pd.Series(0, index=frame.index, dtype="int64")
        for period in EMA_PERIODS:
            htf_alignment = htf_alignment + np.sign(close - _ema(close, period)).fillna(0).astype("int64")
        htf_features[f"htf_{timeframe}_ema_alignment_score"] = htf_alignment.astype("int64")
        htf_features[f"htf_{timeframe}_adx_14"] = _adx_wilder(high, low, close, 14)
        htf_features[f"htf_{timeframe}_atr_14"] = _atr_wilder(high, low, close, 14)
        return htf_features

    def _validate_ohlcv_contract(self, frame: pd.DataFrame) -> None:
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError("OHLCV input must use a DatetimeIndex from load_ohlcv")
        if frame.index.tz is None:
            raise ValueError("OHLCV input index must be timezone-aware UTC")
        required = {"open", "high", "low", "close", "volume"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"OHLCV input missing required columns: {sorted(missing)}")

    def _register_min_lookbacks(self, values: dict[str, int]) -> None:
        self.min_lookbacks.update(values)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _rsi_wilder(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    average_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    average_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = average_gain / average_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(100.0).where(average_gain.notna())


def _rsi_divergence(high: pd.Series, low: pd.Series, rsi: pd.Series, period: int) -> pd.Series:
    previous_low = low.shift(1).rolling(period, min_periods=period).min()
    previous_high = high.shift(1).rolling(period, min_periods=period).max()
    previous_rsi_low = rsi.shift(1).rolling(period, min_periods=period).min()
    previous_rsi_high = rsi.shift(1).rolling(period, min_periods=period).max()
    bullish = (low < previous_low) & (rsi > previous_rsi_low)
    bearish = (high > previous_high) & (rsi < previous_rsi_high)
    return pd.Series(np.select([bullish, bearish], [1, -1], default=0), index=high.index, dtype="int64")


def _adx_wilder(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0),
        index=high.index,
    )
    atr = _atr_wilder(high, low, close, period)
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _last_rank_percentile(values: pd.Series) -> float:
    return float(values.rank().iloc[-1] / 100.0)


def _daily_vwap(close: pd.Series, volume: pd.Series) -> pd.Series:
    day_key = close.index.normalize()
    cumulative_value = (close * volume).groupby(day_key).cumsum()
    cumulative_volume = volume.groupby(day_key).cumsum().replace(0.0, np.nan)
    return cumulative_value / cumulative_volume


def _yang_zhang_volatility(
    high: pd.Series, low: pd.Series, close: pd.Series, open_: pd.Series, period: int
) -> pd.Series:
    """Yang-Zhang volatility estimator.

    Uses open, high, low, close for 9.5x more efficient volatility estimation
    than simple close-to-close. Captures both overnight and intraday volatility.

    σ²_YZ = σ²_OH + σ²_CO + (1-k)σ²_HL

    Reference: Yang & Zhang (2000), "Drift-Independent Volatility Estimation"
    """
    k = 0.34 / (1.34 + (period + 1) / (period - 1))  # Optimal weight

    # Overnight volatility (close → open)
    log_co = (open_ / close.shift(1)).apply(np.log)
    var_co = log_co.rolling(period, min_periods=period).var()

    # Open-to-close volatility
    log_oc = (close / open_).apply(np.log)
    var_oc = log_oc.rolling(period, min_periods=period).var()

    # High-low volatility (Rogers-Satchell)
    log_hl = (high / low).apply(np.log)
    var_hl = (log_hl ** 2).rolling(period, min_periods=period).mean()

    sigma_sq = var_co + var_oc + (1 - k) * var_hl
    return sigma_sq.apply(np.sqrt)


def _kaufman_efficiency(close: pd.Series, period: int) -> pd.Series:
    """Kaufman Efficiency Ratio: directionality / noise.

    ER = |close - close_n| / sum(|close_i - close_(i-1)|)

    Ranges from 0 (random walk) to 1 (perfectly trending).
    Used to dynamically adjust observation windows.
    """
    direction = (close - close.shift(period)).abs()
    noise = close.diff().abs().rolling(period, min_periods=period).sum()
    return direction / noise.replace(0.0, np.nan)


def _merge_asof_on_index(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    left_work = left.copy()
    left_work["_timestamp"] = _utc_index(left_work.index)
    right_work = right.copy()
    right_work.index = _utc_index(right_work.index)
    right_work = right_work.sort_index().reset_index(names="_timestamp_right")
    merged = pd.merge_asof(
        left_work.sort_values("_timestamp").reset_index(drop=True),
        right_work,
        left_on="_timestamp",
        right_on="_timestamp_right",
        direction="backward",
    )
    merged = merged.set_index("_timestamp").sort_index()
    merged.index.name = left.index.name
    return merged.drop(columns=["_timestamp_right"])


def _utc_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if index.tz is None:
        return index.tz_localize("UTC").astype("datetime64[ns, UTC]")
    return index.tz_convert("UTC").astype("datetime64[ns, UTC]")


__all__ = ["FeatureEngineer", "assert_no_lookahead"]
