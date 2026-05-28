"""Phase 1 data ingestion and SQLite persistence for AURUM-1.

This module fetches and normalizes market, macro, COT, calendar, and news
inputs used by later AURUM-1 phases. Live provider methods are intentionally
thin and isolated so tests can replace network calls with deterministic
fixtures. Credentials are read only from environment variables.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import os
import re
import sqlite3
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TypeVar

import pandas as pd

try:  # PyYAML is listed in requirements, but JSON fallback keeps tests portable.
    import yaml
except ImportError:  # pragma: no cover - exercised only in minimal environments.
    yaml = None  # type: ignore[assignment]


T = TypeVar("T")

LOGGER = logging.getLogger(__name__)

TIMEFRAMES = ("M5", "M15", "H1", "H4", "D1")
DEFAULT_OANDA_BACKTEST_COUNT = 5000
OANDA_MAX_CANDLES_PER_REQUEST = 5000
TIMEFRAME_DELTAS = {
    "M5": timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "H1": timedelta(hours=1),
    "H4": timedelta(hours=4),
    "D1": timedelta(days=1),
}
OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "source", "instrument"]
OHLCV_NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume"]
MACRO_COLUMNS = [
    "date",
    "dgs10",
    "cpi",
    "cpi_yoy",
    "real_yield",
    "dxy",
    "dxy_daily_return",
    "vix",
    "vix_1d_change",
]
MACRO_NUMERIC_COLUMNS = MACRO_COLUMNS[1:]
COT_COLUMNS = [
    "report_date",
    "market_name",
    "open_interest",
    "long_positions",
    "short_positions",
    "net_positioning",
    "cot_net_long_pct",
    "source",
]
NEWS_COLUMNS = [
    "published_at",
    "title",
    "url",
    "source",
    "summary",
    "overall_sentiment_score",
    "relevance_score",
]
EVENT_COLUMNS = [
    "event_time",
    "currency",
    "impact",
    "event_name",
    "source",
    "blackout_start",
    "blackout_end",
]


class ProviderError(RuntimeError):
    """Raised when a live data provider cannot satisfy a request."""


@dataclass(frozen=True)
class RetryConfig:
    """Retry parameters for transient provider failures."""

    attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0


def load_settings(path: str | Path) -> dict[str, Any]:
    """Load AURUM-1 settings from a YAML file.

    The default settings file is JSON-formatted YAML, so the fallback parser can
    still load it when PyYAML is not installed. Credential values are not
    resolved here; callers read the configured environment variable names.
    """

    config_path = Path(path)
    raw = config_path.read_text(encoding="utf-8")
    if yaml is not None:
        loaded = yaml.safe_load(raw)
    else:
        loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError(f"Settings file must contain a mapping: {config_path}")
    return loaded


def initialize_database(db_path: str | Path) -> None:
    """Create the Phase 1 SQLite schema if it does not already exist."""

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            for timeframe in TIMEFRAMES:
                conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS ohlcv_{timeframe} (
                        timestamp TEXT NOT NULL,
                        open REAL NOT NULL,
                        high REAL NOT NULL,
                        low REAL NOT NULL,
                        close REAL NOT NULL,
                        volume REAL NOT NULL,
                        source TEXT NOT NULL,
                        instrument TEXT NOT NULL,
                        PRIMARY KEY (timestamp, instrument)
                    )
                    """
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS macro_data (
                    date TEXT PRIMARY KEY,
                    dgs10 REAL,
                    cpi REAL,
                    cpi_yoy REAL,
                    real_yield REAL,
                    dxy REAL,
                    dxy_daily_return REAL,
                    vix REAL,
                    vix_1d_change REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cot_data (
                    report_date TEXT PRIMARY KEY,
                    market_name TEXT NOT NULL,
                    open_interest REAL,
                    long_positions REAL,
                    short_positions REAL,
                    net_positioning REAL,
                    cot_net_long_pct REAL,
                    source TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_headlines (
                    published_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT,
                    source TEXT,
                    summary TEXT,
                    overall_sentiment_score REAL,
                    relevance_score REAL,
                    PRIMARY KEY (published_at, title)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS economic_events (
                    event_time TEXT NOT NULL,
                    currency TEXT,
                    impact TEXT,
                    event_name TEXT NOT NULL,
                    source TEXT,
                    blackout_start TEXT NOT NULL,
                    blackout_end TEXT NOT NULL,
                    PRIMARY KEY (event_time, event_name)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    direction TEXT,
                    price REAL,
                    size REAL,
                    sl REAL,
                    tp REAL,
                    order_id TEXT,
                    status TEXT,
                    payload_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value REAL,
                    payload_json TEXT
                )
                """
            )


def load_ohlcv(timeframe: str, db_path: str | Path) -> pd.DataFrame:
    """Load persisted OHLCV candles with a UTC DatetimeIndex ready for features."""

    normalized_timeframe = timeframe.upper()
    if normalized_timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe {timeframe!r}; expected one of {TIMEFRAMES}")

    table = f"ohlcv_{normalized_timeframe}"
    with closing(sqlite3.connect(Path(db_path))) as conn:
        frame = pd.read_sql_query(f"SELECT * FROM {table}", conn)
    if frame.empty:
        empty = pd.DataFrame(columns=[column for column in OHLCV_COLUMNS if column != "timestamp"])
        empty.index = pd.DatetimeIndex([], tz=UTC, name="timestamp")
        return empty

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame = frame.set_index("timestamp")
    for column in OHLCV_NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
    return frame.sort_index()


def load_macro(db_path: str | Path) -> pd.DataFrame:
    """Load persisted macro data with a UTC daily DatetimeIndex ready for merge."""

    with closing(sqlite3.connect(Path(db_path))) as conn:
        frame = pd.read_sql_query("SELECT * FROM macro_data", conn)
    if frame.empty:
        empty = pd.DataFrame(columns=MACRO_NUMERIC_COLUMNS)
        empty.index = pd.DatetimeIndex([], tz=UTC, name="date")
        return empty

    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame = frame.set_index("date")
    for column in MACRO_NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    return frame.sort_index()


def load_cot(db_path: str | Path) -> pd.DataFrame:
    """Load persisted COT data with a UTC report-date DatetimeIndex ready for merge."""

    with closing(sqlite3.connect(Path(db_path))) as conn:
        frame = pd.read_sql_query("SELECT * FROM cot_data", conn)
    if frame.empty:
        empty = pd.DataFrame(columns=[column for column in COT_COLUMNS if column != "report_date"])
        empty.index = pd.DatetimeIndex([], tz=UTC, name="report_date")
        return empty

    frame["report_date"] = pd.to_datetime(frame["report_date"], utc=True)
    frame = frame.set_index("report_date")
    numeric_columns = ["open_interest", "long_positions", "short_positions", "net_positioning", "cot_net_long_pct"]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    return frame.sort_index()


def merge_macro_onto_ohlcv(ohlcv: pd.DataFrame, macro: pd.DataFrame) -> pd.DataFrame:
    """Merge daily macro observations onto intraday OHLCV rows without changing candle timestamps."""

    if not isinstance(ohlcv.index, pd.DatetimeIndex):
        raise ValueError("OHLCV input must have a DatetimeIndex")
    if ohlcv.index.tz is None:
        raise ValueError("OHLCV DatetimeIndex must be timezone-aware UTC")
    if not isinstance(macro.index, pd.DatetimeIndex):
        raise ValueError("Macro input must have a DatetimeIndex")

    missing_macro_columns = [column for column in MACRO_NUMERIC_COLUMNS if column not in macro.columns]
    if missing_macro_columns:
        raise ValueError(f"Macro input missing required columns: {missing_macro_columns}")
    if ohlcv.empty:
        merged_empty = ohlcv.copy()
        for column in MACRO_NUMERIC_COLUMNS:
            merged_empty[column] = pd.Series(dtype="float64")
        return merged_empty

    original_index = pd.DatetimeIndex(ohlcv.index).tz_convert(UTC).astype("datetime64[ns, UTC]")
    ohlcv_work = ohlcv.copy()
    ohlcv_work.index = original_index
    ohlcv_work["_macro_join_date"] = ohlcv_work.index.normalize()
    ohlcv_work["_original_timestamp"] = ohlcv_work.index

    macro_work = macro[MACRO_NUMERIC_COLUMNS].copy().sort_index()
    if macro_work.index.tz is None:
        macro_work.index = macro_work.index.tz_localize(UTC)
    else:
        macro_work.index = macro_work.index.tz_convert(UTC)
    macro_work.index = pd.DatetimeIndex(macro_work.index).astype("datetime64[ns, UTC]")
    macro_work = macro_work.sort_index().reset_index(names="_macro_join_date")

    merged = pd.merge_asof(
        ohlcv_work.sort_values("_macro_join_date").reset_index(drop=True),
        macro_work,
        on="_macro_join_date",
        direction="backward",
    )
    merged[MACRO_NUMERIC_COLUMNS] = merged[MACRO_NUMERIC_COLUMNS].ffill()
    merged = merged.set_index("_original_timestamp").sort_index()
    merged.index.name = ohlcv.index.name
    merged = merged.drop(columns=["_macro_join_date"])

    check_frame = merged.iloc[13:][MACRO_NUMERIC_COLUMNS]
    if check_frame.isna().any().any():
        bad_columns = check_frame.columns[check_frame.isna().any()].tolist()
        raise ValueError(f"Macro merge produced NaN values after warmup period in columns: {bad_columns}")
    return merged


class AurumDataIngestor:
    """Fetches Phase 1 data inputs and persists normalized records to SQLite."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.data_settings = settings.get("data", {})
        self.broker_settings = settings.get("broker", {})
        self.instrument = str(self.data_settings.get("instrument", "XAUUSD"))
        self.yfinance_symbol = str(self.data_settings.get("yfinance_symbol", "XAUUSD=X"))
        self.timeframes = tuple(self.data_settings.get("timeframes", TIMEFRAMES))
        self.db_path = Path(str(self.data_settings.get("db_path", "aurum1/data/aurum1.sqlite3")))
        self.http_timeout_seconds = float(self.data_settings.get("http_timeout_seconds", 30))
        retry_settings = self.data_settings.get("retry", {})
        self.retry_config = RetryConfig(
            attempts=int(retry_settings.get("attempts", 3)),
            base_delay_seconds=float(retry_settings.get("base_delay_seconds", 0.5)),
            max_delay_seconds=float(retry_settings.get("max_delay_seconds", 8.0)),
        )
        initialize_database(self.db_path)

    def fetch_ohlcv(self, timeframe: str, count: int = DEFAULT_OANDA_BACKTEST_COUNT) -> pd.DataFrame:
        """Fetch OHLCV candles for one timeframe, preferring OANDA over yfinance."""

        normalized_timeframe = timeframe.upper()
        if normalized_timeframe not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe {timeframe!r}; expected one of {TIMEFRAMES}")
        try:
            return self._fetch_oanda_ohlcv(normalized_timeframe, count)
        except Exception as exc:
            LOGGER.warning("OANDA OHLCV fetch failed for %s; falling back to yfinance: %s", timeframe, exc)
            return self._fetch_yfinance_ohlcv(normalized_timeframe, count)

    def fetch_ohlcv_range(
        self,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Fetch OANDA OHLCV candles for a UTC date range in 5000-candle chunks.

        OANDA limits candle responses to 5000 rows. This helper walks the
        requested time range in timeframe-sized pages, normalizes each chunk,
        and deduplicates overlapping timestamps before returning a single
        sorted OHLCV frame.
        """

        normalized_timeframe = timeframe.upper()
        if normalized_timeframe not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe {timeframe!r}; expected one of {TIMEFRAMES}")
        if normalized_timeframe not in TIMEFRAME_DELTAS:
            raise ValueError(f"Missing candle duration for timeframe {timeframe!r}")

        start_utc = self._to_utc(start)
        end_utc = self._to_utc(end)
        if start_utc >= end_utc:
            raise ValueError("fetch_ohlcv_range start must be before end")

        candle_delta = TIMEFRAME_DELTAS[normalized_timeframe]
        # Use one less than the hard provider cap because OANDA treats range
        # boundaries inclusively for completed candles.
        chunk_delta = candle_delta * max(1, OANDA_MAX_CANDLES_PER_REQUEST - 1)
        frames: list[pd.DataFrame] = []
        cursor = start_utc
        while cursor < end_utc:
            chunk_end = min(cursor + chunk_delta, end_utc)
            frame = self._fetch_oanda_ohlcv_range_chunk(normalized_timeframe, cursor, chunk_end)
            if not frame.empty:
                frames.append(frame)
            cursor = chunk_end

        if not frames:
            return self._empty_frame(OHLCV_COLUMNS)

        combined = pd.concat(frames, ignore_index=True)
        combined["timestamp"] = pd.to_datetime(combined["timestamp"], utc=True)
        combined = combined[(combined["timestamp"] >= pd.Timestamp(start_utc)) & (combined["timestamp"] <= pd.Timestamp(end_utc))]
        combined = combined.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
        return self._ensure_columns(combined.reset_index(drop=True), OHLCV_COLUMNS)

    def fetch_all_timeframes(self, count: int = DEFAULT_OANDA_BACKTEST_COUNT) -> dict[str, pd.DataFrame]:
        """Fetch all configured OHLCV timeframes concurrently."""

        results: dict[str, pd.DataFrame] = {}
        with ThreadPoolExecutor(max_workers=len(self.timeframes)) as executor:
            futures = {
                executor.submit(self.fetch_ohlcv, str(timeframe), count): str(timeframe).upper()
                for timeframe in self.timeframes
            }
            for future in as_completed(futures):
                timeframe = futures[future]
                results[timeframe] = future.result()
        return {timeframe: results[timeframe] for timeframe in self.timeframes if timeframe in results}

    def refresh_phase1_data(self, count: int = DEFAULT_OANDA_BACKTEST_COUNT) -> dict[str, pd.DataFrame]:
        """Fetch and persist the complete Phase 1 data set."""

        ohlcv = self.fetch_all_timeframes(count=count)
        for timeframe, frame in ohlcv.items():
            self.persist_ohlcv(timeframe, frame)
        self.persist_macro_data(self.fetch_macro_data())
        self.persist_cot_data(self.fetch_cot_data())
        self.persist_news_headlines(self.fetch_news_headlines())
        self.persist_economic_events(self.fetch_economic_calendar())
        return ohlcv

    def fetch_macro_data(self) -> pd.DataFrame:
        """Fetch FRED and market macro series and compute derived macro features."""

        fred_settings = self.data_settings.get("fred", {})
        fred_series = fred_settings.get("series", {})
        dgs10 = self._fetch_fred_series(str(fred_series.get("dgs10", "DGS10"))).rename("dgs10")
        cpi = self._fetch_fred_series(str(fred_series.get("cpi", "CPIAUCSL"))).rename("cpi")
        cpi_yoy = (cpi.pct_change(periods=12) * 100.0).rename("cpi_yoy")
        macro_yf = self.data_settings.get("macro_yfinance", {})
        dxy = self._fetch_yfinance_daily_series(str(macro_yf.get("dxy_symbol", "DX-Y.NYB"))).rename("dxy")
        vix = self._fetch_yfinance_daily_series(str(macro_yf.get("vix_symbol", "^VIX"))).rename("vix")

        frame = pd.concat([dgs10, cpi, cpi_yoy, dxy, vix], axis=1).sort_index().ffill()
        frame["real_yield"] = frame["dgs10"] - frame["cpi_yoy"]
        frame["dxy_daily_return"] = frame["dxy"].pct_change()
        frame["vix_1d_change"] = frame["vix"].diff()
        frame.index = pd.to_datetime(frame.index, utc=True).date
        frame = frame.reset_index(names="date")
        frame["date"] = frame["date"].map(lambda value: value.isoformat())
        return self._ensure_columns(frame[MACRO_COLUMNS], MACRO_COLUMNS)

    def fetch_cot_data(self) -> pd.DataFrame:
        """Fetch and parse real CFTC COT reports for gold futures positioning.

        The CFTC publishes annual historical ZIP files and a separate current
        weekly text file. Fetching a small set of recent annual files keeps the
        SQLite cache useful for backtests while the current file protects us
        when a new annual ZIP has not appeared yet.
        """

        frames = [self._parse_cot_data(raw) for raw in self._fetch_cot_raw_texts()]
        frames = [frame for frame in frames if not frame.empty]
        if not frames:
            return self._empty_frame(COT_COLUMNS)
        combined = pd.concat(frames, ignore_index=True)
        combined = combined.drop_duplicates(subset=["report_date", "market_name"], keep="last")
        combined = combined.sort_values("report_date").reset_index(drop=True)
        return self._ensure_columns(combined, COT_COLUMNS)

    def fetch_news_headlines(self) -> pd.DataFrame:
        """Fetch recent Alpha Vantage news sentiment headlines for Gold/FX topics."""

        news_settings = self.data_settings.get("news", {})
        api_key = self._env_value(news_settings.get("api_key_env"))
        if not api_key:
            raise ProviderError("Missing Alpha Vantage API key environment variable")
        params = {
            "function": "NEWS_SENTIMENT",
            "topics": news_settings.get("topics", "financial_markets,forex"),
            "sort": "LATEST",
            "limit": int(news_settings.get("limit", 50)),
            "apikey": api_key,
        }
        payload = self.retry_call(
            lambda: self._http_get_json(str(news_settings.get("base_url", "https://www.alphavantage.co/query")), params),
            label="alpha_vantage_news",
        )
        return self._normalize_news_payload(payload)

    def fetch_economic_calendar(self) -> pd.DataFrame:
        """Fetch upcoming high-impact USD/XAU events and calculate blackout windows."""

        calendar_settings = self.data_settings.get("calendar", {})
        raw_html = self.retry_call(
            lambda: self._http_get_text(str(calendar_settings.get("source_url"))),
            label="economic_calendar",
        )
        return self._parse_economic_calendar(raw_html)

    def is_blackout(self, ts: datetime) -> bool:
        """Return True when a timestamp falls inside any persisted event blackout."""

        timestamp = self._to_utc(ts).isoformat()
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM economic_events
                WHERE blackout_start <= ? AND blackout_end >= ?
                LIMIT 1
                """,
                (timestamp, timestamp),
            ).fetchone()
        return row is not None

    def persist_ohlcv(self, timeframe: str, frame: pd.DataFrame) -> None:
        """Persist normalized OHLCV records into the matching timeframe table."""

        table = f"ohlcv_{timeframe.upper()}"
        if table not in {f"ohlcv_{tf}" for tf in TIMEFRAMES}:
            raise ValueError(f"Unsupported OHLCV table for timeframe {timeframe!r}")
        records = self._records_for_sql(frame, OHLCV_COLUMNS, {"timestamp"})
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.executemany(
                    f"""
                    INSERT OR REPLACE INTO {table}
                    (timestamp, open, high, low, close, volume, source, instrument)
                    VALUES (:timestamp, :open, :high, :low, :close, :volume, :source, :instrument)
                    """,
                    records,
                )

    def persist_macro_data(self, frame: pd.DataFrame) -> None:
        """Persist macro data records."""

        records = self._records_for_sql(frame, MACRO_COLUMNS, set())
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO macro_data
                    (date, dgs10, cpi, cpi_yoy, real_yield, dxy, dxy_daily_return, vix, vix_1d_change)
                    VALUES (:date, :dgs10, :cpi, :cpi_yoy, :real_yield, :dxy, :dxy_daily_return, :vix, :vix_1d_change)
                    """,
                    records,
                )

    def persist_cot_data(self, frame: pd.DataFrame) -> None:
        """Persist COT positioning records."""

        records = self._records_for_sql(frame, COT_COLUMNS, set())
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO cot_data
                    (report_date, market_name, open_interest, long_positions, short_positions,
                     net_positioning, cot_net_long_pct, source)
                    VALUES (:report_date, :market_name, :open_interest, :long_positions, :short_positions,
                            :net_positioning, :cot_net_long_pct, :source)
                    """,
                    records,
                )

    def persist_news_headlines(self, frame: pd.DataFrame) -> None:
        """Persist normalized news headline records."""

        records = self._records_for_sql(frame, NEWS_COLUMNS, {"published_at"})
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO news_headlines
                    (published_at, title, url, source, summary, overall_sentiment_score, relevance_score)
                    VALUES (:published_at, :title, :url, :source, :summary,
                            :overall_sentiment_score, :relevance_score)
                    """,
                    records,
                )

    def persist_economic_events(self, frame: pd.DataFrame) -> None:
        """Persist economic-calendar blackout events."""

        records = self._records_for_sql(frame, EVENT_COLUMNS, {"event_time", "blackout_start", "blackout_end"})
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO economic_events
                    (event_time, currency, impact, event_name, source, blackout_start, blackout_end)
                    VALUES (:event_time, :currency, :impact, :event_name, :source,
                            :blackout_start, :blackout_end)
                    """,
                    records,
                )

    def retry_call(self, operation: Callable[[], T], *, label: str) -> T:
        """Run an operation with exponential backoff for transient failures."""

        last_error: Exception | None = None
        for attempt in range(1, self.retry_config.attempts + 1):
            try:
                return operation()
            except Exception as exc:  # pragma: no cover - exact providers vary.
                last_error = exc
                if attempt >= self.retry_config.attempts:
                    break
                delay = min(
                    self.retry_config.base_delay_seconds * (2 ** (attempt - 1)),
                    self.retry_config.max_delay_seconds,
                )
                LOGGER.warning("%s failed on attempt %s/%s: %s", label, attempt, self.retry_config.attempts, exc)
                time.sleep(delay)
        raise ProviderError(f"{label} failed after {self.retry_config.attempts} attempts") from last_error

    def _fetch_oanda_ohlcv(self, timeframe: str, count: int) -> pd.DataFrame:
        oanda_settings = self.broker_settings.get("oanda", {})
        api_key = self._env_value(oanda_settings.get("api_key_env"))
        if not api_key:
            raise ProviderError("Missing OANDA API key environment variable")
        environment = self._env_value(oanda_settings.get("environment_env")) or str(
            oanda_settings.get("default_environment", "practice")
        )
        base_url = oanda_settings.get("live_url") if environment == "live" else oanda_settings.get("practice_url")
        instrument = str(oanda_settings.get("instrument", "XAU_USD"))
        url = f"{base_url}/v3/instruments/{instrument}/candles"
        params = {"granularity": timeframe, "count": count, "price": "M"}
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = self.retry_call(lambda: self._http_get_json(str(url), params, headers), label=f"oanda_{timeframe}")
        return self._normalize_oanda_candles(payload, instrument=instrument, source="oanda")

    def _fetch_oanda_ohlcv_range_chunk(self, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        oanda_settings = self.broker_settings.get("oanda", {})
        api_key = self._env_value(oanda_settings.get("api_key_env"))
        if not api_key:
            raise ProviderError("Missing OANDA API key environment variable")
        environment = self._env_value(oanda_settings.get("environment_env")) or str(
            oanda_settings.get("default_environment", "practice")
        )
        base_url = oanda_settings.get("live_url") if environment == "live" else oanda_settings.get("practice_url")
        instrument = str(oanda_settings.get("instrument", "XAU_USD"))
        url = f"{base_url}/v3/instruments/{instrument}/candles"
        params = {
            "granularity": timeframe,
            "from": self._to_utc(start).isoformat(),
            "to": self._to_utc(end).isoformat(),
            "price": "M",
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = self.retry_call(
            lambda: self._http_get_json(str(url), params, headers),
            label=f"oanda_{timeframe}_range",
        )
        return self._normalize_oanda_candles(payload, instrument=instrument, source="oanda")

    def _fetch_yfinance_ohlcv(self, timeframe: str, count: int) -> pd.DataFrame:
        interval_by_timeframe = {"M5": "5m", "M15": "15m", "H1": "60m", "H4": "60m", "D1": "1d"}
        period_by_timeframe = {"M5": "60d", "M15": "60d", "H1": "730d", "H4": "730d", "D1": "10y"}
        interval = interval_by_timeframe[timeframe]
        raw = self.retry_call(
            lambda: self._download_yfinance(self.yfinance_symbol, period_by_timeframe[timeframe], interval),
            label=f"yfinance_{timeframe}",
        )
        if timeframe == "H4":
            raw = self._resample_yfinance_h4(raw)
        return self._normalize_yfinance_ohlcv(raw, source="yfinance", count=count)

    def _fetch_fred_series(self, series_id: str) -> pd.Series:
        fred_settings = self.data_settings.get("fred", {})
        api_key = self._env_value(fred_settings.get("api_key_env"))
        if not api_key:
            raise ProviderError(f"Missing FRED API key environment variable for {series_id}")
        params = {"series_id": series_id, "api_key": api_key, "file_type": "json", "sort_order": "asc"}
        payload = self.retry_call(
            lambda: self._http_get_json(str(fred_settings.get("base_url")), params),
            label=f"fred_{series_id}",
        )
        observations = payload.get("observations", [])
        points: list[tuple[pd.Timestamp, float]] = []
        for observation in observations:
            value = observation.get("value")
            if value in (None, "."):
                continue
            points.append((pd.Timestamp(observation["date"], tz=UTC), float(value)))
        if not points:
            raise ProviderError(f"FRED series {series_id} returned no usable observations")
        index, values = zip(*points, strict=True)
        return pd.Series(values, index=pd.DatetimeIndex(index), name=series_id.lower())

    def _fetch_yfinance_daily_series(self, symbol: str) -> pd.Series:
        raw = self.retry_call(lambda: self._download_yfinance(symbol, "2y", "1d"), label=f"yfinance_daily_{symbol}")
        if raw.empty:
            raise ProviderError(f"yfinance returned no data for {symbol}")
        close = self._close_column(raw)
        close.index = pd.to_datetime(close.index, utc=True)
        return close.astype(float)

    def _fetch_cot_raw_text(self) -> str:
        return self._fetch_cot_raw_texts(max_successes=1)[0]

    def _fetch_cot_raw_texts(self, max_successes: int | None = None) -> list[str]:
        texts: list[str] = []
        errors: list[str] = []
        for url in self._candidate_cot_urls():
            try:
                content = self.retry_call(lambda url=url: self._http_get_bytes(url), label=f"cftc_cot:{url}")
            except Exception as exc:
                errors.append(f"{url}: {exc}")
                continue
            texts.append(self._decode_cot_content(content))
            if max_successes is not None and len(texts) >= max_successes:
                break
        if not texts:
            searched = "; ".join(errors) if errors else "no COT URLs configured"
            raise ProviderError(f"CFTC COT fetch failed for all candidate URLs: {searched}")
        return texts

    def _decode_cot_content(self, content: bytes) -> str:
        if zipfile.is_zipfile(io.BytesIO(content)):
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                first_name = archive.namelist()[0]
                return archive.read(first_name).decode("utf-8", errors="replace")
        return content.decode("utf-8", errors="replace")

    def _candidate_cot_urls(self) -> list[str]:
        cot_settings = self.data_settings.get("cot", {})
        urls: list[str] = []
        current_year = datetime.now(UTC).year
        history_years = int(cot_settings.get("history_years", 2))
        years = range(current_year, current_year - max(history_years, 1), -1)

        template = str(cot_settings.get("source_url_template", "")).strip()
        if template:
            urls.extend(template.format(year=year) for year in years)

        configured_url = str(cot_settings.get("source_url", "")).strip()
        if configured_url:
            if "{year}" in configured_url:
                urls.extend(configured_url.format(year=year) for year in years)
            else:
                urls.append(configured_url)
                match = re.search(r"(?P<prefix>.*?)(?P<year>20\d{2})(?P<suffix>[^/]*)$", configured_url)
                if match:
                    prefix = match.group("prefix")
                    suffix = match.group("suffix")
                    configured_year = int(match.group("year"))
                    for year in range(configured_year - 1, configured_year - max(history_years, 1), -1):
                        urls.append(f"{prefix}{year}{suffix}")

        current_url = str(cot_settings.get("current_url", "https://www.cftc.gov/dea/newcot/f_disagg.txt")).strip()
        if current_url:
            urls.append(current_url)

        deduped: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if url and url not in seen:
                deduped.append(url)
                seen.add(url)
        return deduped

    def _parse_cot_data(self, raw_csv: str) -> pd.DataFrame:
        """Parse CFTC disaggregated futures-only COT data.

        The net-long percentage is calculated exactly as
        ``(Comm_Long - Comm_Short) / Open_Interest_All``, where Comm_Long and
        Comm_Short are the resolved long/short columns. Resolution prefers
        money-manager disaggregated fields, then commercial fields, then legacy
        non-commercial fields.
        """

        frame = pd.read_csv(io.StringIO(raw_csv), low_memory=False)
        if frame.empty:
            return self._empty_frame(COT_COLUMNS)
        column_map = {_normalize_column_name(column): column for column in frame.columns}
        if not any(candidate in column_map for candidate in ["market_and_exchange_names", "market", "market_name"]):
            frame = self._read_headerless_disaggregated_cot(raw_csv)
            column_map = {_normalize_column_name(column): column for column in frame.columns}

        market_col = self._find_column(column_map, ["market_and_exchange_names", "market", "market_name"])
        date_col = self._find_column(column_map, ["report_date_as_yyyy_mm_dd", "report_date", "date"])
        open_interest_col = self._find_column(column_map, ["open_interest_all", "open_interest"])
        long_candidates = [
            "M_Money_Positions_Long_All",
            "Comm_Positions_Long_All",
            "NonComm_Positions_Long_All",
        ]
        short_candidates = [
            "M_Money_Positions_Short_All",
            "Comm_Positions_Short_All",
            "NonComm_Positions_Short_All",
        ]
        long_col = self._find_column(
            column_map,
            long_candidates,
        )
        short_col = self._find_column(
            column_map,
            short_candidates,
        )
        market_filter = str(self.data_settings.get("cot", {}).get("market_filter", "GOLD")).upper()
        gold = frame[frame[market_col].astype(str).str.upper().str.contains(market_filter, na=False)].copy()
        if gold.empty:
            return self._empty_frame(COT_COLUMNS)
        gold["report_date"] = pd.to_datetime(gold[date_col], utc=True).dt.date.map(lambda value: value.isoformat())
        gold["market_name"] = gold[market_col].astype(str)
        gold["open_interest"] = pd.to_numeric(gold[open_interest_col], errors="coerce")
        gold["long_positions"] = pd.to_numeric(gold[long_col], errors="coerce")
        gold["short_positions"] = pd.to_numeric(gold[short_col], errors="coerce")
        gold["net_positioning"] = gold["long_positions"] - gold["short_positions"]
        gold["cot_net_long_pct"] = gold["net_positioning"] / gold["open_interest"].replace(0, math.nan)
        gold["source"] = "cftc"
        result = gold[COT_COLUMNS].sort_values("report_date").reset_index(drop=True)
        return self._ensure_columns(result, COT_COLUMNS)

    def _read_headerless_disaggregated_cot(self, raw_csv: str) -> pd.DataFrame:
        """Read CFTC historical disaggregated text rows without a header.

        The official CFTC historical compressed files document
        ``Market_and_Exchange_Names`` as field 1, ``As_of_Date_Form_YYYY-MM-DD``
        as field 3, ``Open_Interest_All`` as field 8, and managed-money long
        and short positions as fields 14 and 15.
        """

        frame = pd.read_csv(io.StringIO(raw_csv), header=None, low_memory=False)
        if frame.empty:
            return self._empty_frame(COT_COLUMNS)
        required_index = 14
        if frame.shape[1] <= required_index:
            raise ProviderError(
                "Headerless CFTC disaggregated file has too few columns; "
                f"expected at least {required_index + 1}, found {frame.shape[1]}"
            )
        return pd.DataFrame(
            {
                "Market_and_Exchange_Names": frame.iloc[:, 0],
                "Report_Date_as_YYYY-MM-DD": frame.iloc[:, 2],
                "Open_Interest_All": frame.iloc[:, 7],
                "M_Money_Positions_Long_All": frame.iloc[:, 13],
                "M_Money_Positions_Short_All": frame.iloc[:, 14],
            }
        )

    def _parse_economic_calendar(self, html: str) -> pd.DataFrame:
        events = self._calendar_events_from_data_attributes(html)
        if not events:
            events = self._calendar_events_from_csv_like_text(html)
        settings = self.data_settings.get("calendar", {})
        allowed_currencies = {str(value).upper() for value in settings.get("currencies", ["USD", "XAU"])}
        high_impact_terms = [str(value).upper() for value in settings.get("high_impact_terms", [])]
        source = str(settings.get("source_name", "economic_calendar"))
        window = timedelta(minutes=int(settings.get("blackout_window_minutes", 30)))
        rows: list[dict[str, Any]] = []
        for event in events:
            currency = str(event.get("currency", "")).upper()
            impact = str(event.get("impact", "")).upper()
            name = str(event.get("event_name", "")).strip()
            if currency and currency not in allowed_currencies:
                continue
            if "HIGH" not in impact and not any(term in name.upper() for term in high_impact_terms):
                continue
            event_time = self._parse_datetime(str(event["event_time"]))
            rows.append(
                {
                    "event_time": event_time.isoformat(),
                    "currency": currency,
                    "impact": impact or "HIGH",
                    "event_name": name,
                    "source": source,
                    "blackout_start": (event_time - window).isoformat(),
                    "blackout_end": (event_time + window).isoformat(),
                }
            )
        if not rows:
            return self._empty_frame(EVENT_COLUMNS)
        return self._ensure_columns(pd.DataFrame(rows).sort_values("event_time").reset_index(drop=True), EVENT_COLUMNS)

    def _normalize_news_payload(self, payload: Mapping[str, Any]) -> pd.DataFrame:
        feed = payload.get("feed", [])
        gold_terms = [str(term).lower() for term in self.data_settings.get("news", {}).get("gold_terms", [])]
        rows: list[dict[str, Any]] = []
        for item in feed:
            title = str(item.get("title", "")).strip()
            summary = str(item.get("summary", "")).strip()
            haystack = f"{title} {summary}".lower()
            if gold_terms and not any(term in haystack for term in gold_terms):
                continue
            published_at = self._parse_alpha_vantage_time(str(item.get("time_published", "")))
            rows.append(
                {
                    "published_at": published_at.isoformat(),
                    "title": title,
                    "url": item.get("url"),
                    "source": item.get("source"),
                    "summary": summary,
                    "overall_sentiment_score": _to_float_or_none(item.get("overall_sentiment_score")),
                    "relevance_score": self._max_relevance_score(item.get("ticker_sentiment", [])),
                }
            )
        if not rows:
            return self._empty_frame(NEWS_COLUMNS)
        return self._ensure_columns(pd.DataFrame(rows).sort_values("published_at").reset_index(drop=True), NEWS_COLUMNS)

    def _normalize_oanda_candles(self, payload: Mapping[str, Any], *, instrument: str, source: str) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for candle in payload.get("candles", []):
            if not candle.get("complete", True):
                continue
            price = candle.get("mid") or candle.get("bid") or candle.get("ask")
            if not isinstance(price, Mapping):
                continue
            rows.append(
                {
                    "timestamp": self._parse_datetime(str(candle["time"])),
                    "open": float(price["o"]),
                    "high": float(price["h"]),
                    "low": float(price["l"]),
                    "close": float(price["c"]),
                    "volume": float(candle.get("volume", 0)),
                    "source": source,
                    "instrument": instrument,
                }
            )
        return self._finalize_ohlcv(rows)

    def _normalize_yfinance_ohlcv(self, raw: pd.DataFrame, *, source: str, count: int) -> pd.DataFrame:
        if raw.empty:
            raise ProviderError("yfinance returned no OHLCV rows")
        frame = raw.copy()
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = [str(column[0]) for column in frame.columns]
        rename = {column: column.lower().replace(" ", "_") for column in frame.columns}
        frame = frame.rename(columns=rename)
        required = {"open", "high", "low", "close"}
        if missing := required.difference(frame.columns):
            raise ProviderError(f"yfinance OHLCV data missing columns: {sorted(missing)}")
        if "volume" not in frame.columns:
            frame["volume"] = 0.0
        frame = frame.tail(count).reset_index()
        timestamp_col = "Datetime" if "Datetime" in frame.columns else "Date" if "Date" in frame.columns else frame.columns[0]
        rows = [
            {
                "timestamp": self._parse_datetime(str(row[timestamp_col])),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0) or 0.0),
                "source": source,
                "instrument": self.yfinance_symbol,
            }
            for _, row in frame.iterrows()
            if not pd.isna(row["open"])
        ]
        return self._finalize_ohlcv(rows)

    def _resample_yfinance_h4(self, raw: pd.DataFrame) -> pd.DataFrame:
        frame = raw.copy()
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = [str(column[0]) for column in frame.columns]
        agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
        if "Volume" in frame.columns:
            agg["Volume"] = "sum"
        return frame.resample("4h", label="right", closed="right").agg(agg).dropna(how="any")

    def _download_yfinance(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - depends on local environment.
            raise ProviderError("yfinance is not installed") from exc
        return yf.download(symbol, period=period, interval=interval, auto_adjust=False, progress=False, threads=False)

    def _http_get_json(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        response = self._requests_get(url, params=params, headers=headers)
        return response.json()

    def _http_get_text(self, url: str) -> str:
        return self._requests_get(url).text

    def _http_get_bytes(self, url: str) -> bytes:
        return self._requests_get(url).content

    def _requests_get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - depends on local environment.
            raise ProviderError("requests is not installed") from exc
        response = requests.get(url, params=params, headers=headers, timeout=self.http_timeout_seconds)
        response.raise_for_status()
        return response

    def _calendar_events_from_data_attributes(self, html: str) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for match in re.finditer(r"<tr\b(?P<attrs>[^>]*)>(?P<body>.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL):
            attrs = match.group("attrs")
            body = match.group("body")
            event_time = _html_attr(attrs, "data-event-time") or _html_attr(attrs, "data-date")
            currency = _html_attr(attrs, "data-currency")
            impact = _html_attr(attrs, "data-impact")
            event_name = _html_attr(attrs, "data-event") or _strip_html(body)
            if event_time and event_name:
                rows.append(
                    {
                        "event_time": event_time,
                        "currency": currency or "",
                        "impact": impact or "",
                        "event_name": event_name,
                    }
                )
        return rows

    def _calendar_events_from_csv_like_text(self, text: str) -> list[dict[str, str]]:
        sample = text.strip()
        if not sample:
            return []
        try:
            reader = csv.DictReader(io.StringIO(sample))
            rows = []
            for row in reader:
                event_time = row.get("event_time") or row.get("date") or row.get("datetime")
                event_name = row.get("event_name") or row.get("event") or row.get("name")
                if event_time and event_name:
                    rows.append(
                        {
                            "event_time": event_time,
                            "currency": row.get("currency", ""),
                            "impact": row.get("impact", ""),
                            "event_name": event_name,
                        }
                    )
            return rows
        except csv.Error:
            return []

    def _finalize_ohlcv(self, rows: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
        frame = pd.DataFrame(rows)
        if frame.empty:
            return self._empty_frame(OHLCV_COLUMNS)
        frame = self._ensure_columns(frame, OHLCV_COLUMNS)
        frame = frame.sort_values("timestamp").drop_duplicates(subset=["timestamp", "instrument"], keep="last")
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        return frame.reset_index(drop=True)

    def _records_for_sql(self, frame: pd.DataFrame, columns: list[str], datetime_columns: set[str]) -> list[dict[str, Any]]:
        if frame.empty:
            return []
        normalized = self._ensure_columns(frame.copy(), columns)
        for column in datetime_columns:
            normalized[column] = pd.to_datetime(normalized[column], utc=True).map(lambda value: value.isoformat())
        records: list[dict[str, Any]] = []
        for record in normalized.to_dict(orient="records"):
            cleaned = {key: (None if pd.isna(value) else value) for key, value in record.items()}
            records.append(cleaned)
        return records

    def _env_value(self, env_var_name: Any) -> str | None:
        if not env_var_name:
            return None
        return os.getenv(str(env_var_name))

    def _parse_datetime(self, value: str) -> datetime:
        clean = value.strip()
        if clean.endswith("Z"):
            clean = f"{clean[:-1]}+00:00"
        parsed = datetime.fromisoformat(clean)
        return self._to_utc(parsed)

    def _parse_alpha_vantage_time(self, value: str) -> datetime:
        if not value:
            return datetime.now(UTC)
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)

    def _to_utc(self, ts: datetime) -> datetime:
        if ts.tzinfo is None:
            return ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC)

    def _max_relevance_score(self, ticker_sentiment: Any) -> float | None:
        if not isinstance(ticker_sentiment, list):
            return None
        scores = [_to_float_or_none(item.get("relevance_score")) for item in ticker_sentiment if isinstance(item, dict)]
        valid_scores = [score for score in scores if score is not None]
        return max(valid_scores) if valid_scores else None

    def _close_column(self, frame: pd.DataFrame) -> pd.Series:
        if isinstance(frame.columns, pd.MultiIndex):
            for candidate in ("Adj Close", "Close"):
                matches = [column for column in frame.columns if column[0] == candidate]
                if matches:
                    return frame[matches[0]]
        if "Adj Close" in frame.columns:
            return frame["Adj Close"]
        if "Close" in frame.columns:
            return frame["Close"]
        raise ProviderError("No close column found in yfinance data")

    def _find_column(self, column_map: Mapping[str, str], candidates: list[str]) -> str:
        for candidate in candidates:
            normalized = _normalize_column_name(candidate)
            if normalized in column_map:
                return column_map[normalized]
        present_columns = sorted(column_map.values())
        raise ProviderError(f"Could not find any searched columns {candidates}; present columns: {present_columns}")

    def _empty_frame(self, columns: list[str]) -> pd.DataFrame:
        return pd.DataFrame(columns=columns)

    def _ensure_columns(self, frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
        for column in columns:
            if column not in frame.columns:
                frame[column] = None
        return frame[columns]


def _normalize_column_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _html_attr(attrs: str, name: str) -> str | None:
    pattern = rf'{re.escape(name)}\s*=\s*["\'](?P<value>.*?)["\']'
    match = re.search(pattern, attrs, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return match.group("value").strip()


def _strip_html(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", without_tags).strip()


def _to_float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
