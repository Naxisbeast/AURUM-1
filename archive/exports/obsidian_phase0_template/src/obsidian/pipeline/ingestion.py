"""OANDA OHLCV ingestion helpers for OBSIDIAN Phase 0."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

import pandas as pd

from obsidian.config import ObsidianConfig
from obsidian.pipeline.cache import REQUIRED_OHLCV_COLUMNS, deduplicate_ohlcv, normalize_instrument, normalize_timeframe
from obsidian.utils.time import canonical_utc_iso, parse_utc_timestamp, timeframe_delta


OANDA_MAX_CANDLES_PER_REQUEST = 5000


class ProviderError(RuntimeError):
    """Raised when a data provider cannot satisfy a request."""


def normalize_oanda_candles(
    payload: Mapping[str, Any],
    *,
    instrument: str = "XAU_USD",
    timeframe: str = "M15",
    closed_only: bool = True,
) -> pd.DataFrame:
    normalized_instrument = normalize_instrument(instrument)
    normalized_timeframe = normalize_timeframe(timeframe)
    rows: list[dict[str, Any]] = []
    for candle in payload.get("candles", []):
        complete = bool(candle.get("complete", False))
        if closed_only and not complete:
            continue
        price = candle.get("mid") or candle.get("bid") or candle.get("ask")
        if not isinstance(price, Mapping):
            continue
        rows.append(
            {
                "timestamp_utc": canonical_utc_iso(str(candle["time"])),
                "open": float(price["o"]),
                "high": float(price["h"]),
                "low": float(price["l"]),
                "close": float(price["c"]),
                "volume": float(candle.get("volume", 0.0) or 0.0),
                "complete": complete,
                "instrument": normalized_instrument,
                "timeframe": normalized_timeframe,
            }
        )
    frame = pd.DataFrame(rows, columns=REQUIRED_OHLCV_COLUMNS)
    if frame.empty:
        return frame
    deduped, _ = deduplicate_ohlcv(frame)
    return deduped


def fetch_oanda_range(
    config: ObsidianConfig,
    *,
    instrument: str = "XAU_USD",
    timeframe: str = "M15",
    start_utc: Any,
    end_utc: Any,
    closed_only: bool = True,
    session: Any | None = None,
) -> pd.DataFrame:
    start = parse_utc_timestamp(start_utc)
    end = parse_utc_timestamp(end_utc)
    if start >= end:
        raise ValueError("start_utc must be before end_utc")

    delta = timeframe_delta(timeframe)
    chunk_delta = delta * max(1, OANDA_MAX_CANDLES_PER_REQUEST - 1)
    frames: list[pd.DataFrame] = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + chunk_delta, end)
        frame = fetch_oanda_chunk(
            config,
            instrument=instrument,
            timeframe=timeframe,
            start_utc=cursor,
            end_utc=chunk_end,
            closed_only=closed_only,
            session=session,
        )
        if not frame.empty:
            frames.append(frame)
        cursor = chunk_end

    if not frames:
        return pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    combined["timestamp_utc"] = pd.to_datetime(combined["timestamp_utc"], utc=True)
    combined = combined[
        (combined["timestamp_utc"] >= pd.Timestamp(start))
        & (combined["timestamp_utc"] <= pd.Timestamp(end))
    ]
    combined["timestamp_utc"] = combined["timestamp_utc"].map(canonical_utc_iso)
    deduped, _ = deduplicate_ohlcv(combined)
    return deduped


def fetch_oanda_chunk(
    config: ObsidianConfig,
    *,
    instrument: str,
    timeframe: str,
    start_utc: Any,
    end_utc: Any,
    closed_only: bool = True,
    session: Any | None = None,
) -> pd.DataFrame:
    api_key = config.oanda.api_key
    if not api_key:
        raise ProviderError(f"Missing OANDA API key environment variable: {config.oanda.api_key_env}")
    requests_session = session or _requests_module()
    normalized_instrument = normalize_instrument(instrument)
    normalized_timeframe = normalize_timeframe(timeframe)
    url = f"{config.oanda.base_url}/v3/instruments/{normalized_instrument}/candles"
    params = {
        "from": canonical_utc_iso(start_utc),
        "to": canonical_utc_iso(end_utc),
        "granularity": normalized_timeframe,
        "price": "M",
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests_session.get(url, params=params, headers=headers, timeout=config.request_timeout_seconds)
    response.raise_for_status()
    return normalize_oanda_candles(
        response.json(),
        instrument=normalized_instrument,
        timeframe=normalized_timeframe,
        closed_only=closed_only,
    )


def fetch_oanda_history(
    config: ObsidianConfig,
    *,
    instrument: str = "XAU_USD",
    timeframe: str = "M15",
    years: float = 1.0,
    end_utc: Any | None = None,
    closed_only: bool = True,
    session: Any | None = None,
) -> pd.DataFrame:
    end = parse_utc_timestamp(end_utc) if end_utc is not None else pd.Timestamp.now(tz="UTC")
    if end.tzinfo is None:
        end = end.tz_localize("UTC")
    end = end.tz_convert("UTC")
    start = end - timedelta(days=365.25 * float(years))
    return fetch_oanda_range(
        config,
        instrument=instrument,
        timeframe=timeframe,
        start_utc=start,
        end_utc=end,
        closed_only=closed_only,
        session=session,
    )


def _requests_module() -> Any:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - requests is in requirements.
        raise ProviderError("requests is required for OANDA fetching") from exc
    return requests
