# News/Sentiment Research — Feasibility Report

**Date**: 2026-08-16
**Status**: Exploration spike complete
**Purpose**: Go/no-go on a news-driven strategy thread (second stream + daily-context overlay) for AURUM-1

---

## 1. What was built (and verified working)

A complete news-sentiment research pipeline, in `scripts/research/news_sentiment/`:

| Component | File | Verified |
|-----------|------|----------|
| **GDELT historical fetcher** | `fetch_gdelt_news.py` | ✅ Reachable; returns gold articles with dates back to 2015. **Heavily rate-limited** (see §3). |
| **RSS live-forward fetcher** | `fetch_rss_news.py` | ✅ Works instantly, no API key. 199 real gold headlines fetched. |
| **FinBERT daily sentiment** | `build_daily_sentiment.py` | ✅ Scores headlines (ProsusAI/finbert), aggregates to daily bullish/bearish, aligns to gold daily closes. |
| **Signal check** | `signal_check.py` | ✅ IC (Pearson/Spearman), lag analysis, day-bias hit-rate, regime breakdown. Honest verdict logic. |

The pipeline reuses the **dormant infra** that was already in the codebase:
`aurum1/data/ingestion.py` `NEWS_COLUMNS` schema, `news_headlines` tables, and
`settings.yaml` `news.gold_terms`. This confirms the original AURUM architecture
(price + macro + news + sentiment) was designed but never activated — the news
layer was the missing piece, and the plumbing to slot it in exists.

## 2. Data availability & quality

**Sources tested:**

| Source | History | Rate limit | Key needed | Verdict |
|--------|---------|-----------|-----------|---------|
| **GDELT DOC 2.0** | Back to 2015 | ~1 req / 5s, aggressive IP throttle | No | ✅ best for backtest; slow to pull |
| **Google News RSS** | Recent only (~weeks; `when:` operator inconsistent) | None practical | No | ✅ best for live-forward; sparse history |
| **Alpha Vantage NEWS_SENTIMENT** | Recent (~50) | 5/min, 500/day (free) | Yes (`ALPHA_VANTAGE_API_KEY`) | Not used (no key); viable future option |

**Coverage quality (RSS sample, 199 headlines):**
- 69 distinct days over Dec 2025 → Aug 2026, but **concentrated in the recent ~2 weeks** (Aug 10–14: 14–20 headlines/day).
- **Density is the problem**: only ~1.7 headlines/day on average over the aligned period. That's thin for a daily sentiment series.
- **Market-cache mismatch**: local gold caches reach Jun 29 (backtest) / Jul 16 (forward shadow), while the dense RSS days are Aug 10–16 — so the richest news window can't currently be aligned to local gold closes.

## 3. Signal check — preliminary (thin-sample) read

Run on 43 aligned days (Dec 2025 → Jun 2026), two independent scorers:

| Metric | FinBERT | Lexicon (cross-check) |
|--------|---------|----------------------|
| IC lag0 (same-day) | **+0.063** | +0.035 |
| IC lag1 (next-day, tradeable) | **−0.026** | +0.001 |
| IC lag2 | −0.056 | −0.19 |
| IC lag3 | −0.14 | — |
| Day-bias hit-rate | 0.60 | 0.49 |

**Honest reading:**

- **Contemporaneous correlation exists** (news and gold move together same-day,
  IC ≈ 0.06). This is expected — news is *caused by* and *describes* price moves.
- **No predictive forward signal** at daily frequency. The tradeable lags
  (lag1/lag2) are ~0 to slightly negative in BOTH methods. You cannot act on
  lag0 before the return has happened.
- The FinBERT hit-rate (0.60) looks promising but is on 43 days of sparse data;
  the lexicon cross-check (0.49 = coin flip) does **not** confirm it. When two
  methods disagree this much on a thin sample, the honest conclusion is
  **insufficient evidence, trending toward "news is already priced in."**

> ⚠️ **Caveat**: n=43 with ~1.7 headlines/day is far too small for a verdict.
> The negative finding is *provisional* — the pipeline needs a proper historical
> sample (GDELT, patiently pulled) before this is conclusive.

## 4. Verdict — NO-GO on the signal as tested, with a conditional path forward

**Verdict: NO-GO for now.** Based on the available evidence:

1. **No forward predictive signal** was found at daily frequency (both scorers).
   This matches the prior AURUM finding (`research/rejected/README.md`:
   "Sentiment scoring — data quality too poor for M15 trading") and the
   efficient-market intuition that gold news is largely priced in by M15/daily.
2. **The "daily context/bias" overlay idea** (knowing whether the day is
   bearish/bullish before acting) is *not supported* by the predictive-lag data —
   if sentiment doesn't predict next-day direction, an overlay has nothing to
   filter on.

**Conditional path forward (IF you want to continue):**

The negative result is provisional because the sample was too small and too
sparse. A real test requires:
1. **A patient GDELT historical pull** (6s/request over several sessions, or a
   scheduled background job) to build a proper multi-year daily sentiment series.
2. Re-run the signal check on that full sample before any strategy build.
3. **Alpha Vantage as an alternative** — if you get a free key, its precomputed
   `overall_sentiment_score` + relevance could enrich the signal at lower cost.

If, after a proper historical sample, sentiment still shows no forward IC, the
thread should be **archived as a completed negative** (like D6) — not because ML
or NLP is bad, but because news on XAU/USD is efficiently priced.

## 5. What this means for the portfolio goal

The second-stream / portfolio-diversification goal (gate criterion 4) is **not
advanced by news sentiment on the current evidence**. The research-thread
harness is now in place and reusable (fetchers + scorer + signal check), so if a
*new* data source or idea emerges (e.g. economic-calendar event windows, COT
positioning, a different instrument), the pipeline can test it cheaply.

## Files

- `scripts/research/news_sentiment/fetch_gdelt_news.py` — GDELT historical
- `scripts/research/news_sentiment/fetch_rss_news.py` — Google News RSS live
- `scripts/research/news_sentiment/build_daily_sentiment.py` — FinBERT + daily agg
- `scripts/research/news_sentiment/signal_check.py` — IC / hit-rate / regime
- `reports/research/news_daily_sentiment_rss.csv` — real-data output
- `reports/research/news_signal_check_rss.json` — signal-check JSON
