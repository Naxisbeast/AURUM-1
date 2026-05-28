from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from aurum1.data.ingestion import (
    AurumDataIngestor,
    OANDA_MAX_CANDLES_PER_REQUEST,
    ProviderError,
    initialize_database,
    load_ohlcv,
    load_settings,
    merge_macro_onto_ohlcv,
)


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "aurum1" / "config" / "settings.yaml"


def make_settings(db_path: Path) -> dict:
    settings = load_settings(SETTINGS_PATH)
    settings["data"]["db_path"] = str(db_path)
    settings["data"]["retry"] = {"attempts": 3, "base_delay_seconds": 0.0, "max_delay_seconds": 0.0}
    return settings


class Phase1IngestionTests(unittest.TestCase):
    def test_config_uses_env_var_names_without_credentials(self) -> None:
        settings = load_settings(SETTINGS_PATH)

        self.assertEqual(settings["broker"]["oanda"]["api_key_env"], "OANDA_API_KEY")
        self.assertEqual(settings["data"]["fred"]["api_key_env"], "FRED_API_KEY")
        self.assertEqual(settings["data"]["news"]["api_key_env"], "ALPHA_VANTAGE_API_KEY")
        self.assertNotIn("token", str(settings).lower())

    def test_initialize_database_creates_required_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "aurum.sqlite3"
            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
                }

        expected = {
            "ohlcv_M5",
            "ohlcv_M15",
            "ohlcv_H1",
            "ohlcv_H4",
            "ohlcv_D1",
            "macro_data",
            "cot_data",
            "news_headlines",
            "economic_events",
            "trades_log",
            "performance_log",
        }
        self.assertTrue(expected.issubset(tables))

    def test_load_ohlcv_returns_datetime_index(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "aurum.sqlite3"
            ingestor = AurumDataIngestor(make_settings(db_path))
            fixture = pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(
                        ["2026-05-27T10:15:00Z", "2026-05-27T10:00:00Z"],
                        utc=True,
                    ),
                    "open": [2334, 2330],
                    "high": [2335, 2332],
                    "low": [2331, 2328],
                    "close": [2332, 2331],
                    "volume": [20, 10],
                    "source": ["fixture", "fixture"],
                    "instrument": ["XAU_USD", "XAU_USD"],
                }
            )
            ingestor.persist_ohlcv("M15", fixture)

            loaded = load_ohlcv("M15", db_path)

        self.assertIsInstance(loaded.index, pd.DatetimeIndex)
        self.assertEqual(loaded.index.tz, UTC)
        self.assertEqual(loaded["close"].dtype, "float64")
        self.assertTrue(loaded.index.is_monotonic_increasing)

    def test_oanda_candles_normalize_to_ohlcv_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            ingestor = AurumDataIngestor(make_settings(Path(tempdir) / "aurum.sqlite3"))
            payload = {
                "candles": [
                    {
                        "complete": True,
                        "time": "2026-05-27T10:00:00.000000000Z",
                        "volume": 123,
                        "mid": {"o": "2330.1", "h": "2335.0", "l": "2328.5", "c": "2333.2"},
                    },
                    {
                        "complete": False,
                        "time": "2026-05-27T10:05:00.000000000Z",
                        "volume": 99,
                        "mid": {"o": "1", "h": "1", "l": "1", "c": "1"},
                    },
                ]
            }

            frame = ingestor._normalize_oanda_candles(payload, instrument="XAU_USD", source="oanda")

        self.assertEqual(len(frame), 1)
        self.assertEqual(list(frame.columns), ["timestamp", "open", "high", "low", "close", "volume", "source", "instrument"])
        self.assertEqual(frame.loc[0, "close"], 2333.2)
        self.assertEqual(frame.loc[0, "source"], "oanda")

    def test_fetch_ohlcv_falls_back_to_yfinance_when_oanda_fails(self) -> None:
        class FallbackIngestor(AurumDataIngestor):
            def _fetch_oanda_ohlcv(self, timeframe: str, count: int) -> pd.DataFrame:
                raise ProviderError("no credentials")

            def _fetch_yfinance_ohlcv(self, timeframe: str, count: int) -> pd.DataFrame:
                return pd.DataFrame(
                    {
                        "timestamp": pd.to_datetime(["2026-05-27T10:00:00Z"], utc=True),
                        "open": [1.0],
                        "high": [2.0],
                        "low": [0.5],
                        "close": [1.5],
                        "volume": [0.0],
                        "source": ["yfinance"],
                        "instrument": ["XAUUSD=X"],
                    }
                )

        with tempfile.TemporaryDirectory() as tempdir:
            ingestor = FallbackIngestor(make_settings(Path(tempdir) / "aurum.sqlite3"))
            frame = ingestor.fetch_ohlcv("M15", count=1)

        self.assertEqual(frame.loc[0, "source"], "yfinance")

    def test_fetch_ohlcv_range_deduplicates_overlap(self) -> None:
        class RangeIngestor(AurumDataIngestor):
            def __init__(self, settings: dict) -> None:
                super().__init__(settings)
                self.calls = 0

            def _fetch_oanda_ohlcv_range_chunk(
                self,
                timeframe: str,
                start: datetime,
                end: datetime,
            ) -> pd.DataFrame:
                self.calls += 1
                if self.calls == 1:
                    timestamps = ["2026-05-27T00:00:00Z", "2026-05-27T00:15:00Z"]
                else:
                    timestamps = ["2026-05-27T00:15:00Z", "2026-05-27T00:30:00Z"]
                return pd.DataFrame(
                    {
                        "timestamp": pd.to_datetime(timestamps, utc=True),
                        "open": [1.0, 2.0],
                        "high": [2.0, 3.0],
                        "low": [0.5, 1.5],
                        "close": [1.5, 2.5],
                        "volume": [10.0, 20.0],
                        "source": ["oanda", "oanda"],
                        "instrument": ["XAU_USD", "XAU_USD"],
                    }
                )

        with tempfile.TemporaryDirectory() as tempdir:
            ingestor = RangeIngestor(make_settings(Path(tempdir) / "aurum.sqlite3"))
            with patch("aurum1.data.ingestion.OANDA_MAX_CANDLES_PER_REQUEST", 2):
                frame = ingestor.fetch_ohlcv_range(
                    "M15",
                    datetime(2026, 5, 27, 0, 0, tzinfo=UTC),
                    datetime(2026, 5, 27, 0, 30, tzinfo=UTC),
                )

        self.assertEqual(OANDA_MAX_CANDLES_PER_REQUEST, 5000)
        self.assertFalse(frame["timestamp"].duplicated().any())
        self.assertEqual(len(frame), 3)
        self.assertTrue(frame["timestamp"].is_monotonic_increasing)

    def test_macro_data_computes_cpi_yoy_real_yield_and_market_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            ingestor = AurumDataIngestor(make_settings(Path(tempdir) / "aurum.sqlite3"))
            dates = pd.date_range("2025-01-01", periods=13, freq="MS", tz=UTC)
            cpi = pd.Series([100 + index for index in range(13)], index=dates)
            dgs10 = pd.Series([4.0] * 13, index=dates)
            dxy = pd.Series([100.0 + index for index in range(13)], index=dates)
            vix = pd.Series([15.0 + index for index in range(13)], index=dates)

            ingestor._fetch_fred_series = lambda series_id: dgs10 if series_id == "DGS10" else cpi  # type: ignore[method-assign]
            ingestor._fetch_yfinance_daily_series = lambda symbol: dxy if "DX" in symbol else vix  # type: ignore[method-assign]

            frame = ingestor.fetch_macro_data()

        last = frame.iloc[-1]
        self.assertAlmostEqual(last["cpi_yoy"], 12.0)
        self.assertAlmostEqual(last["real_yield"], -8.0)
        self.assertGreater(last["dxy_daily_return"], 0.0)
        self.assertEqual(last["vix_1d_change"], 1.0)

    def test_merge_macro_onto_ohlcv_no_nans_after_warmup(self) -> None:
        index = pd.date_range("2026-05-27T22:00:00Z", periods=20, freq="15min", tz=UTC)
        ohlcv = pd.DataFrame(
            {
                "open": [2330.0] * 20,
                "high": [2335.0] * 20,
                "low": [2328.0] * 20,
                "close": [2332.0] * 20,
                "volume": [100.0] * 20,
                "source": ["fixture"] * 20,
                "instrument": ["XAU_USD"] * 20,
            },
            index=index,
        )
        macro = pd.DataFrame(
            {
                "dgs10": [4.2, 4.3],
                "cpi": [315.0, 316.0],
                "cpi_yoy": [3.1, 3.2],
                "real_yield": [1.1, 1.1],
                "dxy": [104.0, 104.5],
                "dxy_daily_return": [0.001, 0.004],
                "vix": [16.0, 17.0],
                "vix_1d_change": [0.2, 1.0],
            },
            index=pd.to_datetime(["2026-05-27", "2026-05-28"], utc=True),
        )

        merged = merge_macro_onto_ohlcv(ohlcv, macro)

        self.assertTrue(merged.index.equals(ohlcv.index))
        self.assertFalse(merged.iloc[13:][["real_yield", "dxy", "vix"]].isna().any().any())
        self.assertEqual(merged.shape[0], ohlcv.shape[0])

    def test_cot_parser_extracts_gold_net_positioning(self) -> None:
        raw_csv = """Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,Open_Interest_All,M_Money_Positions_Long_All,M_Money_Positions_Short_All
GOLD - COMMODITY EXCHANGE INC.,2026-05-19,200000,120000,70000
SILVER - COMMODITY EXCHANGE INC.,2026-05-19,100000,60000,50000
"""
        with tempfile.TemporaryDirectory() as tempdir:
            ingestor = AurumDataIngestor(make_settings(Path(tempdir) / "aurum.sqlite3"))
            frame = ingestor._parse_cot_data(raw_csv)

        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.loc[0, "net_positioning"], 50000)
        self.assertAlmostEqual(frame.loc[0, "cot_net_long_pct"], 0.25)

    def test_cot_parser_raises_on_missing_columns(self) -> None:
        raw_csv = """Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,Open_Interest_All,Other_Long,Other_Short
GOLD - COMMODITY EXCHANGE INC.,2026-05-19,200000,120000,70000
"""
        with tempfile.TemporaryDirectory() as tempdir:
            ingestor = AurumDataIngestor(make_settings(Path(tempdir) / "aurum.sqlite3"))
            with self.assertRaises(ProviderError) as context:
                ingestor._parse_cot_data(raw_csv)

        message = str(context.exception)
        self.assertIn("M_Money_Positions_Long_All", message)
        self.assertIn("Comm_Positions_Long_All", message)
        self.assertIn("NonComm_Positions_Long_All", message)
        self.assertIn("Other_Long", message)

    def test_economic_calendar_blackout_window(self) -> None:
        html = """
        <table>
          <tr data-event-time="2026-05-27T12:30:00+00:00" data-currency="USD" data-impact="High" data-event="CPI Release"></tr>
        </table>
        """
        with tempfile.TemporaryDirectory() as tempdir:
            ingestor = AurumDataIngestor(make_settings(Path(tempdir) / "aurum.sqlite3"))
            frame = ingestor._parse_economic_calendar(html)
            ingestor.persist_economic_events(frame)

            self.assertTrue(ingestor.is_blackout(datetime(2026, 5, 27, 12, 15, tzinfo=UTC)))
            self.assertFalse(ingestor.is_blackout(datetime(2026, 5, 27, 13, 5, tzinfo=UTC)))

    def test_alpha_vantage_news_normalizes_gold_fx_headlines(self) -> None:
        payload = {
            "feed": [
                {
                    "time_published": "20260527T101500",
                    "title": "Gold rises as USD weakens",
                    "url": "https://example.test/news",
                    "source": "Example",
                    "summary": "XAU catches a bid.",
                    "overall_sentiment_score": "0.34",
                    "ticker_sentiment": [{"ticker": "FOREX:XAU", "relevance_score": "0.91"}],
                },
                {
                    "time_published": "20260527T101600",
                    "title": "Unrelated software update",
                    "summary": "No market impact.",
                    "overall_sentiment_score": "0.1",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tempdir:
            settings = make_settings(Path(tempdir) / "aurum.sqlite3")
            ingestor = AurumDataIngestor(settings)

            with patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "test-key"}):
                ingestor._http_get_json = lambda url, params=None, headers=None: payload  # type: ignore[method-assign]
                frame = ingestor.fetch_news_headlines()

        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.loc[0, "title"], "Gold rises as USD weakens")
        self.assertAlmostEqual(frame.loc[0, "relevance_score"], 0.91)

    def test_retry_logic_retries_then_succeeds_and_raises_after_final_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            ingestor = AurumDataIngestor(make_settings(Path(tempdir) / "aurum.sqlite3"))
            attempts = {"count": 0}

            def flaky() -> str:
                attempts["count"] += 1
                if attempts["count"] < 3:
                    raise RuntimeError("temporary")
                return "ok"

            self.assertEqual(ingestor.retry_call(flaky, label="flaky"), "ok")
            self.assertEqual(attempts["count"], 3)

            with self.assertRaises(ProviderError):
                ingestor.retry_call(lambda: (_ for _ in ()).throw(RuntimeError("down")), label="down")


@unittest.skipUnless(os.getenv("RUN_LIVE_SMOKE") == "1", "RUN_LIVE_SMOKE=1 not set")
class LiveSmokeTests(unittest.TestCase):
    def test_live_fetch_m15_or_skip_for_missing_optional_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            settings = make_settings(Path(tempdir) / "aurum.sqlite3")
            ingestor = AurumDataIngestor(settings)
            try:
                frame = ingestor.fetch_ohlcv("M15", count=10)
            except ProviderError as exc:
                self.skipTest(f"live providers unavailable: {exc}")
            self.assertFalse(frame.empty)


if __name__ == "__main__":
    unittest.main()
