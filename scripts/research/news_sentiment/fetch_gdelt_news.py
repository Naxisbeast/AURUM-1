"""Fetch historical gold/FX news headlines from the GDELT DOC 2.0 API.

The GDELT Global Knowledge Graph DOC 2.0 API returns article metadata
(title, url, publish/seendate) going back to ~2015 — the historical depth
needed to backtest a news-sentiment signal, which free RSS feeds cannot
provide. This script pulls gold/FX-relevant headlines for the backtest
window and normalizes them into the dormant `news_headlines` table schema
(see `aurum1/data/ingestion.py` NEWS_COLUMNS) so the existing infra can
read them.

Data quality is filtered by gold terms from `settings.yaml news.gold_terms`
(a gold_terms match in title OR domain/source context is kept). GDELT
throttles aggressively, so the pull is date-chunked with small sleeps.

Usage:
    python scripts/research/news_sentiment/fetch_gdelt_news.py \
        --start 2016-01-01 --end 2026-06-30 \
        --out aurum1/data/news_gdelt.sqlite3 \
        [--dry-run] [--query "gold price OR XAU OR precious metals"]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import load_settings  # noqa: E402

GDELT_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_CHUNK_DAYS = 30
# GDELT DOC 2.0 enforces ~1 request / 5s; use 6s to stay clear of the limiter.
# A 429 typically needs a 45-60s cooldown to clear, so don't hammer it.
REQUEST_DELAY_SECONDS = 6.0


def _load_gold_terms() -> list[str]:
    """Load the gold-relevance terms from settings.yaml (same list D4 uses).

    The news config lives under `data.news` in settings.yaml; fall back to
    sensible defaults if the schema differs.
    """
    try:
        settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
        news = settings.get("news") or settings.get("data", {}).get("news", {})
        return [str(t).lower() for t in news.get("gold_terms", [])]
    except Exception:  # noqa: BLE001 - settings schema varies; fall back
        return ["gold", "xau", "dollar", "usd", "forex", "fx"]


def _query_gdelt_day(query: str, day: datetime, *, max_records: int = 250,
                     retries: int = 3) -> list[dict]:
    """Fetch up to max_records articles for a single UTC day from GDELT.

    GDELT `startdatetime`/`enddatetime` are inclusive (YYYYMMDDHHMMSS), so we
    pass the day bounds and rely on server-side filtering. Retries transient
    HTTP 429 (too many requests) with exponential backoff, since GDELT
    throttles burst queries hard.
    """
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": str(max_records),
        "format": "json",
        "startdatetime": day.strftime("%Y%m%d000000"),
        "enddatetime": (day + timedelta(days=1) - timedelta(seconds=1)).strftime("%Y%m%d235959"),
        "sort": "datedesc",
    }
    url = f"{GDELT_BASE}?{urllib.parse.urlencode(params)}"
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                body = resp.read().decode("utf-8")
            # GDELT's rate limiter returns a 200 with a plain-text/HTML warning
            # ("Please limit requests to one every 5 seconds...") instead of a
            # proper 429. Detect it and back off.
            lowered = body[:500].lower()
            if "limit requests" in lowered or "please limit" in lowered:
                if attempt < retries:
                    wait = REQUEST_DELAY_SECONDS * (2 ** attempt)
                    print(f"    !! rate-limited on {day.date()}, retry {attempt}/{retries} in {wait:.0f}s")
                    time.sleep(wait)
                    continue
                print(f"    !! GDELT rate limit persisted for {day.date()} after {retries} attempts")
                return []
            payload = json.loads(body)
            articles = payload.get("articles") or payload.get("Article") or []
            return [a for a in articles if isinstance(a, dict)]
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries:
                wait = REQUEST_DELAY_SECONDS * (2 ** attempt)
                print(f"    !! 429 on {day.date()}, retry {attempt}/{retries} in {wait:.0f}s")
                time.sleep(wait)
                continue
            print(f"    !! GDELT HTTP error for {day.date()}: {exc}")
            return []
        except Exception as exc:  # noqa: BLE001 - transient hiccups should not kill the pull
            print(f"    !! GDELT error for {day.date()}: {exc}")
            return []
    return []


def _normalize_articles(articles: list[dict], gold_terms: list[str]) -> list[dict]:
    """Normalize GDELT articles into the news_headlines schema.

    GDELT `seendate` (UTC, YYYYMMDDHHMMSS) is used as the publish timestamp.
    Gold relevance: keep an article if a gold term appears in the title (the
    most reliable signal); otherwise drop it. GDELT has no per-article
    sentiment, so those columns stay NULL for the fetcher to fill later.
    """
    rows: list[dict] = []
    for art in articles:
        title = str(art.get("title") or "").strip()
        if not title:
            continue
        if gold_terms and not any(term in title.lower() for term in gold_terms):
            continue
        seendate = str(art.get("seendate") or "").strip()
        try:
            published = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            # Some records use a bare YYYYMMDD; fall back to that date at 00:00 UTC.
            try:
                published = datetime.strptime(seendate[:8], "%Y%m%d").replace(tzinfo=UTC)
            except ValueError:
                continue
        rows.append(
            {
                "published_at": published.isoformat(),
                "title": title,
                "url": art.get("url"),
                "source": art.get("domain") or art.get("source"),
                "summary": str(art.get("seendate") or ""),
                "overall_sentiment_score": None,  # filled by FinBERT stage
                "relevance_score": 1.0 if gold_terms and any(t in title.lower() for t in gold_terms) else None,
            }
        )
    return rows


def _chunk_days(start: datetime, end: datetime, chunk_days: int) -> list[tuple[datetime, datetime]]:
    """Split [start, end] into inclusive chunk intervals of chunk_days."""
    chunks: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _write_db(path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS news_headlines (
                published_at TEXT, title TEXT, url TEXT, source TEXT,
                summary TEXT, overall_sentiment_score REAL, relevance_score REAL,
                PRIMARY KEY (published_at, title)
            )
            """
        )
        if rows:
            conn.executemany(
                """
                INSERT OR REPLACE INTO news_headlines
                (published_at, title, url, source, summary, overall_sentiment_score, relevance_score)
                VALUES (:published_at, :title, :url, :source, :summary,
                        :overall_sentiment_score, :relevance_score)
                """,
                rows,
            )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--out", required=True, type=Path, help="SQLite DB path to write")
    parser.add_argument("--dry-run", action="store_true", help="Count matches without writing")
    parser.add_argument(
        "--query",
        default="gold price OR XAU OR precious metals",
        help="GDELT query string (OR-separated gold/FX terms)",
    )
    parser.add_argument("--max-records", type=int, default=250, help="GDELT maxrecords per request")
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS, help="days per GDELT request")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    gold_terms = _load_gold_terms()
    print(f"Gold terms: {gold_terms}")
    print(f"Pulling {start.date()} -> {end.date()} ({args.chunk_days}-day chunks)")

    chunks = _chunk_days(start, end, args.chunk_days)
    all_rows: list[dict] = []
    for i, (c_start, c_end) in enumerate(chunks):
        # GDELT returns the newest records first; query day-by-day to avoid
        # the 250-record cap dropping older articles in a multi-day chunk.
        for d_off in range((c_end - c_start).days + 1):
            day = c_start + timedelta(days=d_off)
            articles = _query_gdelt_day(args.query, day, max_records=args.max_records)
            normalized = _normalize_articles(articles, gold_terms)
            all_rows.extend(normalized)
            time.sleep(REQUEST_DELAY_SECONDS)
        print(f"  chunk {i+1}/{len(chunks)} done ({c_start.date()}..{c_end.date()}), "
              f"running total {len(all_rows)}")

    print(f"\nTotal gold-relevant headlines: {len(all_rows)}")
    if not args.dry_run and all_rows:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        _write_db(args.out, all_rows)
        print(f"Wrote {len(all_rows)} rows to {args.out}")
    elif args.dry_run:
        print("[dry-run] no DB written")


if __name__ == "__main__":
    main()
