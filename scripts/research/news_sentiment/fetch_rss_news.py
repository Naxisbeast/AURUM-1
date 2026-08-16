"""Fetch recent gold/FX headlines from Google News RSS (live-forward).

Google News RSS has no reliable historical archive (the `when:` operator is
inconsistent), but it returns current headlines instantly and without an API
key. This script is the live-forward data path for the news-sentiment spike:
it pulls the latest gold-relevant headlines into the same `news_headlines`
schema, so `build_daily_sentiment.py` can score them.

This complements GDELT (historical, rate-limited) — RSS covers "what the news
is saying RIGHT NOW", GDELT covers "what the news said historically".

Usage:
    python scripts/research/news_sentiment/fetch_rss_news.py \
        --out aurum1/data/news_rss.sqlite3 \
        [--days 7] [--query "gold price OR XAU OR precious metals"]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import load_settings  # noqa: E402

RSS_BASE = "https://news.google.com/rss/search"


def _gold_terms() -> list[str]:
    try:
        settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
        news = settings.get("news") or settings.get("data", {}).get("news", {})
        return [str(t).lower() for t in news.get("gold_terms", [])]
    except Exception:  # noqa: BLE001
        return ["gold", "xau", "dollar", "usd", "forex", "fx"]


def _fetch(query: str, when: str) -> list[dict]:
    """Fetch and parse a Google News RSS query for a time window."""
    url = f"{RSS_BASE}?{urllib.parse.urlencode({'q': f'{query} when:{when}', 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read().decode("utf-8")
    root = ET.fromstring(data)
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        source = (item.findtext("source") or "").strip()
        if not title:
            continue
        items.append({"title": title, "url": link, "source": source, "pubDate": pub})
    return items


def _parse_pubdate(value: str) -> datetime | None:
    """RFC 822 pubDate (GMT) -> aware UTC datetime."""
    try:
        return datetime.strptime(value, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=UTC)
    except ValueError:
        try:
            return datetime.strptime(value, "%a, %d %b %Y %H:%M:%S %z").astimezone(UTC)
        except ValueError:
            return None


def _normalize(items: list[dict], gold_terms: list[str]) -> list[dict]:
    """Keep gold-relevant items, map to news_headlines schema."""
    rows = []
    for it in items:
        title = it["title"]
        if gold_terms and not any(t in title.lower() for t in gold_terms):
            continue
        published = _parse_pubdate(it["pubDate"])
        if published is None:
            continue
        rows.append(
            {
                "published_at": published.isoformat(),
                "title": title,
                "url": it["url"],
                "source": it["source"],
                "summary": "",
                "overall_sentiment_score": None,
                "relevance_score": 1.0 if any(t in title.lower() for t in gold_terms) else None,
            }
        )
    return rows


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
    parser.add_argument("--out", required=True, type=Path, help="SQLite DB path to write")
    parser.add_argument("--days", type=int, default=7, help="lookback window for RSS `when:`")
    parser.add_argument("--query", default="gold price OR XAU OR precious metals", help="search query")
    args = parser.parse_args()

    gold_terms = _gold_terms()
    print(f"Gold terms: {gold_terms}")
    print(f"Fetching Google News RSS, lookback {args.days}d")

    all_items: list[dict] = []
    for when in (f"{args.days}d", f"{args.days * 2}d", "1y"):
        try:
            items = _fetch(args.query, when)
            all_items.extend(items)
            print(f"  when:{when}: {len(items)} items")
        except Exception as exc:  # noqa: BLE001
            print(f"  when:{when}: ERROR {exc}")
        time.sleep(1.0)

    # Deduplicate by title.
    seen: set[str] = set()
    unique: list[dict] = []
    for it in all_items:
        key = it["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    rows = _normalize(unique, gold_terms)
    print(f"\nGold-relevant unique headlines: {len(rows)}")
    if rows:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        _write_db(args.out, rows)
        print(f"Wrote {len(rows)} rows to {args.out}")
        dates = sorted(r["published_at"] for r in rows)
        print(f"Date range: {dates[0]} .. {dates[-1]}")


if __name__ == "__main__":
    main()
