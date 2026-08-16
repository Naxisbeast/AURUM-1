"""Score news headlines with FinBERT and aggregate into a daily sentiment series.

Reads gold-relevant headlines (from the GDELT fetch or any source normalized
into the `news_headlines` schema), scores each with FinBERT (ProsusAI/finbert),
weights by gold relevance, and produces a daily bullish/bearish "context"
series aligned to XAU/USD daily closes from the market cache.

The output is a CSV/JSON daily series: date, n_headlines, n_relevant,
net_sentiment (relevance-weighted mean of positive-negative), bull_fraction,
bear_fraction, neutral_fraction. This is what `signal_check.py` consumes.

Usage:
    python scripts/research/news_sentiment/build_daily_sentiment.py \
        --news aurum1/data/news_gdelt.sqlite3 \
        --market aurum1/data/backtest_market_cache.sqlite3 \
        --out reports/research/news_daily_sentiment.csv \
        [--start 2026-04-01] [--end 2026-06-30] [--batch 64]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLD_TERMS = ["gold", "xau", "dollar", "usd", "forex", "fx"]


# --------------------------------------------------------------------------
# FinBERT scoring (self-contained; pattern mirrors the archived SentimentScorer
# but without the aurum1.models.utils dependency, which is archived/empty).
# --------------------------------------------------------------------------
class FinBertScorer:
    """Lazy, batched FinBERT headline scorer using transformers pipeline."""

    def __init__(self, batch_size: int = 64) -> None:
        self.batch_size = batch_size
        self._pipeline = None

    def _load(self):
        if self._pipeline is None:
            from transformers import pipeline
            self._pipeline = pipeline(
                "text-classification",
                model="ProsusAI/finbert",
                tokenizer="ProsusAI/finbert",
                return_all_scores=True,
            )
        return self._pipeline

    def score(self, headlines: list[str]) -> list[dict[str, float]]:
        """Score headlines -> list of {'positive','negative','neutral'}."""
        if not headlines:
            return []
        pipe = self._load()
        results: list[dict[str, float]] = []
        for i in range(0, len(headlines), self.batch_size):
            batch = headlines[i:i + self.batch_size]
            raw = pipe(batch)
            for item in raw:
                results.append(self._normalize(item))
        return results

    @staticmethod
    def _normalize(raw) -> dict[str, float]:
        items = [raw] if isinstance(raw, dict) else list(raw)
        scores = {"positive": 0.0, "negative": 0.0, "neutral": 0.0}
        for it in items:
            label = str(it.get("label", "")).lower()
            score = float(it.get("score", 0.0))
            if "pos" in label:
                scores["positive"] = score
            elif "neg" in label:
                scores["negative"] = score
            elif "neu" in label:
                scores["neutral"] = score
        total = sum(scores.values())
        if total <= 0.0:
            return {"positive": 0.0, "negative": 0.0, "neutral": 1.0}
        return {k: v / total for k, v in scores.items()}


def _gold_relevance(title: str) -> float:
    """Gold relevance weight: 1.0 if a strong gold term is in the title, else 0.5."""
    title_l = title.lower()
    strong = {"gold", "xau", "precious metals", "bullion"}
    weak = {"dollar", "usd", "forex", "fx", "fed", "inflation", "rate"}
    if any(t in title_l for t in strong):
        return 1.0
    if any(t in title_l for t in weak):
        return 0.5
    return 0.0


def load_news(db_path: Path) -> pd.DataFrame:
    """Load gold-relevant headlines from a news_headlines table."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT published_at, title, url, source, summary, "
            "overall_sentiment_score, relevance_score FROM news_headlines"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return pd.DataFrame(columns=["published_at", "title", "url", "source", "summary"])
    frame = pd.DataFrame(
        rows,
        columns=["published_at", "title", "url", "source", "summary",
                 "overall_sentiment_score", "relevance_score"],
    )
    frame["published_at"] = pd.to_datetime(frame["published_at"], utc=True)
    return frame


def load_daily_closes(market_db: Path) -> pd.Series:
    """Daily XAU/USD close series from the M15 cache (resampled to 1D)."""
    conn = sqlite3.connect(str(market_db))
    try:
        rows = conn.execute(
            "SELECT timestamp, close FROM ohlcv_M15 ORDER BY timestamp"
        ).fetchall()
    finally:
        conn.close()
    df = pd.DataFrame(rows, columns=["timestamp", "close"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    daily = df.set_index("timestamp").resample("1D")["close"].last().dropna()
    return daily


def build_daily_sentiment(
    news: pd.DataFrame,
    scorer: FinBertScorer,
    *,
    start: str | None,
    end: str | None,
) -> pd.DataFrame:
    """Score all headlines, aggregate to a daily sentiment series."""
    if news.empty:
        return pd.DataFrame()

    # Score each headline.
    titles = news["title"].tolist()
    print(f"Scoring {len(titles)} headlines with FinBERT (batch {scorer.batch_size})...")
    scores = scorer.score(titles)
    news = news.copy()
    news["pos"] = [s["positive"] for s in scores]
    news["neg"] = [s["negative"] for s in scores]
    news["neu"] = [s["neutral"] for s in scores]
    news["rel"] = [_gold_relevance(t) for t in titles]

    # Drop low-relevance headlines.
    keep = news[news["rel"] > 0.0].copy()
    if keep.empty:
        print("WARNING: no gold-relevant headlines survived relevance filtering")
        return pd.DataFrame()

    keep["date"] = keep["published_at"].dt.date
    daily = (
        keep.groupby("date")
        .agg(
            n_headlines=("title", "count"),
            n_relevant=("rel", lambda s: int((s > 0).sum())),
            net_sentiment=("pos", lambda s: np.average(s - keep.loc[s.index, "neg"], weights=keep.loc[s.index, "rel"])),
            bull_fraction=("pos", lambda s: float((s > 0.5).mean())),
            bear_fraction=("neg", lambda s: float((s > 0.5).mean())),
            neutral_fraction=("neu", lambda s: float((s > 0.5).mean())),
        )
        .reset_index()
    )
    daily["date"] = pd.to_datetime(daily["date"], utc=True)
    if start:
        daily = daily[daily["date"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        daily = daily[daily["date"] <= pd.Timestamp(end, tz="UTC")]
    return daily.sort_values("date").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--news", required=True, type=Path, help="news_headlines SQLite DB")
    parser.add_argument("--market", required=True, type=Path, help="market cache SQLite (for daily closes)")
    parser.add_argument("--out", required=True, type=Path, help="output CSV path")
    parser.add_argument("--start", default=None, help="YYYY-MM-DD inclusive filter")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD inclusive filter")
    parser.add_argument("--batch", type=int, default=64, help="FinBERT batch size")
    parser.add_argument("--scorer", default="finbert", choices=["finbert", "lexicon"],
                        help="finbert (model) or lexicon (fast keyword baseline)")
    args = parser.parse_args()

    news = load_news(args.news)
    print(f"Loaded {len(news)} headlines from {args.news}")

    if args.scorer == "finbert":
        scorer = FinBertScorer(batch_size=args.batch)
        daily = build_daily_sentiment(news, scorer, start=args.start, end=args.end)
    else:
        # Lexicon baseline: quick keyword-based net sentiment for a fast signal check.
        daily = _build_lexicon_daily(news, start=args.start, end=args.end)

    if daily.empty:
        print("No daily sentiment rows produced. Check the news DB has data.")
        return

    # Align with daily returns.
    closes = load_daily_closes(args.market)
    daily["gold_close"] = daily["date"].map(closes)
    daily["gold_return"] = daily["gold_close"].pct_change()
    daily = daily.dropna(subset=["gold_close"]).reset_index(drop=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    daily.to_csv(args.out, index=False)
    print(f"\nDaily sentiment series -> {args.out}")
    print(f"Days: {len(daily)}, from {daily['date'].min().date()} to {daily['date'].max().date()}")
    print(f"Net sentiment range: [{daily['net_sentiment'].min():.3f}, {daily['net_sentiment'].max():.3f}]")
    print(f"Mean daily headlines: {daily['n_headlines'].mean():.1f}")


def _build_lexicon_daily(news: pd.DataFrame, *, start: str | None, end: str | None) -> pd.DataFrame:
    """Fast lexicon-based net sentiment (no model load). Positive/negative word lists."""
    pos_words = {"gain", "rise", "up", "rally", "surge", "jump", "high", "strong", "record", "boost", "increase", "climb"}
    neg_words = {"fall", "drop", "down", "slump", "slide", "low", "weak", "fear", "crash", "decline", "cut", "loss", "plunge"}
    rows = []
    for _, row in news.iterrows():
        title = str(row.get("title", "")).lower()
        words = set(title.split())
        net = (len(words & pos_words) - len(words & neg_words)) / max(1, len(words))
        rows.append({"date": pd.Timestamp(row["published_at"]).date(), "title": title, "net": net, "rel": _gold_relevance(title)})
    df = pd.DataFrame(rows)
    df = df[df["rel"] > 0]
    if df.empty:
        return pd.DataFrame()
    daily = df.groupby("date").agg(n_headlines=("title", "count"), net_sentiment=("net", "mean")).reset_index()
    daily["date"] = pd.to_datetime(daily["date"], utc=True)
    if start:
        daily = daily[daily["date"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        daily = daily[daily["date"] <= pd.Timestamp(end, tz="UTC")]
    daily["bull_fraction"] = (daily["net_sentiment"] > 0).astype(float)
    daily["bear_fraction"] = (daily["net_sentiment"] < 0).astype(float)
    daily["neutral_fraction"] = (daily["net_sentiment"] == 0).astype(float)
    return daily.sort_values("date").reset_index(drop=True)


if __name__ == "__main__":
    main()
