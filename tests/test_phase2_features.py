from __future__ import annotations

from datetime import UTC, time

import numpy as np
import pandas as pd

from aurum1.features.engineer import FeatureEngineer, assert_no_lookahead


def make_ohlcv(periods: int = 500, freq: str = "15min", start: str = "2026-05-20T00:00:00Z") -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq=freq, tz=UTC)
    step = np.arange(periods, dtype="float64")
    close = 2300.0 + (step * 0.08) + (np.sin(step / 7.0) * 2.0)
    open_ = close + (np.cos(step / 5.0) * 0.3)
    high = np.maximum(open_, close) + 1.8 + (np.sin(step / 11.0) * 0.1)
    low = np.minimum(open_, close) - 1.8 - (np.cos(step / 13.0) * 0.1)
    volume = 1000.0 + ((step % 50.0) * 10.0) + (np.sin(step / 3.0) * 5.0)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "source": "fixture",
            "instrument": "XAU_USD",
        },
        index=index,
    )


def make_macro(ohlcv: pd.DataFrame) -> pd.DataFrame:
    start = ohlcv.index.min().normalize()
    end = ohlcv.index.max().normalize() + pd.Timedelta(days=1)
    index = pd.date_range(start, end, freq="D", tz=UTC)
    step = np.arange(len(index), dtype="float64")
    return pd.DataFrame(
        {
            "dgs10": 4.0 + (step * 0.01),
            "cpi": 315.0 + (step * 0.2),
            "cpi_yoy": 3.0 + (step * 0.01),
            "real_yield": 1.0 + (step * 0.005),
            "dxy": 104.0 + (step * 0.1),
            "dxy_daily_return": 0.001 + (step * 0.0001),
            "vix": 16.0 + (step * 0.2),
            "vix_1d_change": 0.1 + (step * 0.01),
        },
        index=index,
    )


def make_cot(ohlcv: pd.DataFrame) -> pd.DataFrame:
    start = ohlcv.index.min().normalize() - pd.Timedelta(days=14)
    index = pd.date_range(start, periods=12, freq="7D", tz=UTC)
    step = np.arange(len(index), dtype="float64")
    return pd.DataFrame(
        {
            "market_name": "GOLD - COMMODITY EXCHANGE INC.",
            "open_interest": 200000.0 + step,
            "long_positions": 120000.0 + step,
            "short_positions": 70000.0 + step,
            "net_positioning": 50000.0,
            "cot_net_long_pct": 0.20 + (step * 0.005),
            "source": "fixture",
        },
        index=index,
    )


def build_fixture_features(
    *,
    include_target: bool = False,
    htf_frames: dict[str, pd.DataFrame] | None = None,
) -> tuple[FeatureEngineer, pd.DataFrame, pd.DataFrame]:
    ohlcv = make_ohlcv()
    engineer = FeatureEngineer({"feature_engineering": {"lookahead_check": True}})
    features = engineer.build_features(
        ohlcv,
        make_macro(ohlcv),
        make_cot(ohlcv),
        htf_frames=htf_frames,
        include_target=include_target,
    )
    return engineer, features, ohlcv


def test_feature_matrix_has_no_nan_after_warmup() -> None:
    _, features, _ = build_fixture_features()

    assert not features.isna().any().any()
    assert len(features) == 300


def test_ema_alignment_score_range() -> None:
    _, features, _ = build_fixture_features()

    assert features["ema_alignment_score"].between(-5, 5).all()
    assert pd.api.types.is_integer_dtype(features["ema_alignment_score"])


def test_atr_is_always_positive() -> None:
    _, features, _ = build_fixture_features()

    assert (features["atr_14"] > 0).all()


def test_session_flags_are_binary() -> None:
    _, features, _ = build_fixture_features()
    session_columns = ["session_asia", "session_london", "session_ny", "session_overlap"]

    for column in session_columns:
        assert set(features[column].unique()).issubset({0, 1})
    overlap = features["session_overlap"] == 1
    assert (features.loc[overlap, "session_london"] == 1).all()
    assert (features.loc[overlap, "session_ny"] == 1).all()


def test_vwap_resets_each_trading_day() -> None:
    _, features, _ = build_fixture_features()
    midnight_rows = features[[timestamp.time() == time(0, 0) for timestamp in features.index]]

    assert not midnight_rows.empty
    assert (midnight_rows["vwap_deviation"].abs() < 1e-12).all()


def test_no_lookahead_bias() -> None:
    engineer, features, ohlcv = build_fixture_features()

    assert_no_lookahead(features, ohlcv["close"], engineer.min_lookbacks)


def test_target_columns_absent_by_default() -> None:
    _, features, _ = build_fixture_features(include_target=False)

    assert "forward_return_5bar" not in features.columns
    assert "label" not in features.columns


def test_target_columns_present_when_requested() -> None:
    _, features, _ = build_fixture_features(include_target=True)

    assert "forward_return_5bar" in features.columns
    assert "label" in features.columns
    assert set(features["label"].unique()).issubset({-1, 0, 1})


def test_multi_timeframe_confluence_columns_present() -> None:
    htf_frames = {
        "H1": make_ohlcv(periods=900, freq="1h", start="2026-04-15T00:00:00Z"),
        "H4": make_ohlcv(periods=900, freq="4h", start="2026-01-01T00:00:00Z"),
    }

    _, features, _ = build_fixture_features(htf_frames=htf_frames)

    assert "htf_H1_ema_alignment_score" in features.columns
    assert "htf_H4_adx_14" in features.columns


def test_get_feature_names_excludes_target_and_metadata() -> None:
    engineer, _, _ = build_fixture_features(include_target=True)
    feature_names = engineer.get_feature_names()

    assert "forward_return_5bar" not in feature_names
    assert "label" not in feature_names
    assert "source" not in feature_names
    assert len(feature_names) >= 40
