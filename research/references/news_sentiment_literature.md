# News/Sentiment for Gold — Literature & Data Sources

**Date**: 2026-08-16
**Status**: Deep-research review complete (101-agent workflow, adversarial claim verification)
**Purpose**: Ground the news-sentiment research thread in published evidence. Answers: "is this worth it?" and "how do people actually do it?"

---

## TL;DR — the balanced verdict

- **Forecast accuracy improves** with news sentiment for gold — multiple peer-reviewed ML studies show sentiment cuts prediction error.
- **But post-cost trading value for spot gold is NOT demonstrated.** The strongest economic-value study (peer-reviewed) finds no text sentiment measure beats the historical-mean benchmark for gold out-of-sample.
- **The one peer-reviewed exception** works on precious-metal ETFs (GLD/SLV), not XAU/USD spot, with caveats.
- **The disciplined move for AURUM-1**: do NOT pursue a news overlay mid-gate. Finish the 200-trade DSR gate on price-only D4 first.

---

## A. The strongest evidence (verified, peer-reviewed)

### A1. The skeptical economic-value study — READ FIRST ⚠️
**Dong, Mai, Pukthuanthong & Zhou, "Investor Sentiment and Asset Returns: Actions Speak Louder Than Words", Journal of Portfolio Management 51(4):96-127 (Feb 2025).**
- Across gold, stocks, T-bonds, Bitcoin: text/news sentiment has **almost no next-day predictive power for gold**.
- **No sentiment measure (text OR trade-based) beats the historical-mean benchmark out-of-sample for gold.**
- Economic gains are **confined to Bitcoin** trade sentiment.
- Trade-based "action" sentiment beats text sentiment for most assets — **but not for gold**.
- Methodologically the strongest source in the whole review (OOS, economic-value framing, peer-reviewed journal, senior finance academics).
- Sources: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5096165 , https://www.pm-research.com/content/iijpormgmt/51/4/96

### A2. The one peer-reviewed after-costs positive (with caveats)
**Novotny & Hájek, "A hybrid adaptive trading strategy integrating investor sentiment for precious metal ETFs", Financial Innovation 12(1):74 (Feb 2026).**
- SVR + PPO reinforcement-learning strategy with sentiment (VIX + SF Fed news index) beat buy-and-hold and a 5-day MA across **GLD, SLV, PPLT, PALL**.
- Remained profitable across a 5-50 bps cost grid and under 30% short-term capital-gains tax; OOS Sharpe ~1.4-1.9.
- **Caveats**: ETFs not XAU/USD spot; cost/tax modules "stylized"; only 2-year OOS window; high cumulative return invites look-ahead scrutiny.
- Source: https://link.springer.com/article/10.1186/s40854-026-00911-2

### A3. Foundational gold-safe-haven finding
**Roache & Rossi, "The Effects of Economic News on Commodity Prices: Is Gold Special?", Quarterly Review of Economics and Finance 50(3):377-385 (2010).**
- Commodities were **relatively insensitive to macro news at daily frequency** (1997-2009) vs other assets.
- News-based models **poorly forecast commodity prices at daily frequency**.
- **Gold is the unique commodity that rises on bad economic news** (safe-haven asymmetry) — the fundamental inversion.
- Note: 2010 pre-LLM finding; a 2025 Stirling study finds gold's safe-haven property has weakened and gold now tracks equities.
- Source: https://ideas.repec.org/a/eee/quaeco/v50y2010i3p377-385.html

## B. Supportive forecast-accuracy evidence (real, but lower-tier)

These show sentiment **cuts forecast error** for gold, but none demonstrate post-cost trading profit:

1. **Mrad et al., "Enhancing Gold Price Forecast Accuracy through Feature Engineering and Financial News Sentiment Integration" (IEEE IMCET 2026)** — LR + GPT-4o sentiment + event flags; RMSE $2.35 on April 2025 gold. ⚠️ Single ~22-trading-day OOS window; RMSE ~0.07% of price is implausibly low → hints at leakage/overfit. https://ieeexplore.ieee.org/document/11503716
2. **Ji et al., "Forecasting the Price of Gold with Integrated Media Sentiment... CNN-QRLSTM" (Entropy 28(3):271, 2026)** — CNN-QRLSTM fusing daily media sentiment; nRMSE 4.68-15.88% across cross-year splits. Method: financial lexicon + semantic rules + context weighting, NOT event-study. ~1 citation, no replication. https://www.mdpi.com/1099-4300/28/3/271
3. **Daniati et al., "Gold Price Prediction With Integrated News Sentiment Analysis Using LSTM" (IEEE BTS-I2C 2025)** — IndoBERT daily sentiment, **t-2 lag best**; MAPE 1.51%, RMSE $60.92. https://ieeexplore.ieee.org/document/11399462
4. **Elkarnighi et al., "Does sentiment analysis bring more responsive and comprehensive commodity price forecasting?" (Research in International Business and Finance 88, 2026)** — sentiment improves forecasting **in selected markets, not uniformly** — conditional, not general. https://www.sciencedirect.com/science/article/pii/S0275531926001686

**Refuted at verification (do NOT cite these as true):**
- ❌ "LSTM/DNN benefit most from sentiment" — overreach beyond the source.
- ❌ "LLM scoring is the current method of choice" — LLM scoring exists (Mrad 2026) but is not settled standard.

## C. Methodological building blocks (highest confidence)

1. **t-2 (two-day) sentiment lag is optimal** for daily-frequency gold LSTM forecasting (IEEE BTS-I2C 2025). One study, Indonesian-language daily news — not consensus, but the strongest lag signal in the literature.
2. **Gold-specific directionality labels beat generic FinBERT polarity.** The Sinha & Khandait gold dataset uses a gold-specific annotation schema (price up/down, past/future info, asset comparison), and its directionality score **significantly predicts future gold price** (p=0.0318 / p=0.00218 across two windows; impact visible 24h after news).
3. **FinBERT is measurably weak on gold news.** arXiv:2512.00946 (Engineering Applications of AI, Dec 2025): FinBERT accuracy/macro-F1 up to **-30%** vs lightweight LLMs (Llama3 8B / Qwen3 8B) on the gold news dataset (FinBERT 0.56 acc / 0.45 F1 vs Llama3 0.95/0.88 at 100% data), attributed to overfitting/catastrophic interference.
4. **Event-study vs continuous sentiment**: the accuracy studies use *continuous daily-aggregated sentiment*, NOT event windows. But the intraday gold literature (below) says gold's news reaction is **fast and around scheduled releases** — the frequency-appropriate design for M15.
5. **The sentiment-inversion problem** (generic FinBERT labels "rising dollar" bullish, but it's bearish for gold): NOT directly established by a confirmed claim in this review — nearest support is (a) the gold-specific directionality finding, (b) FinBERT's measured weakness on gold, (c) Roache & Rossi's gold-safe-haven asymmetry. **Treat as a well-motivated hypothesis, not established fact.**

## D. Data sources (practical for backtesting)

1. **⭐ Sinha & Khandait, "Sentiment Analysis of Commodity News (Gold)"** — ~10,570 human-annotated gold headlines 2000-2021 from 6 outlets, 80/20 split (8,456 train / 2,114 test), labeled by 3 expert annotators (Cohen's Kappa > 0.85) on gold-specific dimensions. The paper (arXiv:2009.04202, Springer FICC 2021) reports the directionality score predicts future gold price.
   - **⚠️ License: CC BY-NC-ND 4.0 — research/backtest only, NOT for production/commercial use.**
   - Kaggle: https://www.kaggle.com/datasets/ankurzing/sentiment-analysis-in-commodity-market-gold
   - HF: https://huggingface.co/datasets/SaguaroCapital/sentiment-analysis-in-commodity-market-gold
   - Paper: https://arxiv.org/abs/2009.04202
2. **⭐ Kaggle "Gold & Silver" (Marketsignal)** — XAU/USD + silver, 2020-2025, 90+ cols incl. **AI sentiment (Google Gemini) on 100+ GDELT articles/day**, technical, macro, forward labels (1d/3d/5d). **Freely-downloadable Kaggle artifact is only a 30-row-per-metal sample**; the full feed appears to be a paid product. https://www.kaggle.com/datasets/marketsignal/gold-and-silver
3. **Kaggle "geopolitical news and financial markets"** — CC0, ~69 MB, daily news-derived numeric features (DailySentimentScore, NewsVolume, MaxImpact, event root codes) paired with daily Gold_Price 2020-2026. Free, directly usable. https://www.kaggle.com/datasets/kozasalih/geopolitical-news-and-financial-markets-numeric-data
4. **Hugging Face `olm/gdelt-news-headlines`** — large GDELT headline dumps, daily files. https://huggingface.co/datasets/olm/gdelt-news-headlines
5. **Production caveat**: a real AURUM-1 deployment needs a **commercially licensed** news feed; the CC BY-NC-ND dataset cannot be used in production.

## E. Foundational academic papers on gold news events

Verified:
- **Elder, Miao & Ramchander, "Impact of macroeconomic news on metal futures" (J. Banking & Finance 36(1):51-65, 2012)** — intraday (2002-2008) gold/silver/copper futures; **8:30am macro surprises** drive fast responses; gold/silver fall on positive economic surprises (inversion), copper rises.
- **Cai, Cheung & Wong, "What moves the gold market?" (J. Futures Markets 21(3):257-278, 2001)** — of 23 US macro announcements, only **employment reports, GDP, CPI, personal income** significantly move gold futures.
- **Ederington & Lee, "How markets process information: news releases and volatility" (1993)** — scheduled macro announcements drive most time-of-day volatility in futures.
- **Hautsch & Groß-Klußmann (2011)** — automated high-frequency text analytics for news-implied market reactions.
- Co-jumps in gold cluster around **US labour-market and inflation announcements, then FOMC** (verified).

## F. Open questions (from the review — honest gaps)

1. **The sentiment-inversion phenomenon is not directly verified** in the literature — yet it's the single biggest correctness risk for a gold-news system built on generic polarity. Would need our own test.
2. Exact lag/decay/magnitude of the foundational papers not fully verified in this run (Elder/Miao/Ramchander surfaced as corroboration).
3. **Would an event-study around CPI/FOMC/NFP show exploitable post-cost M15 gold edge?** This is the frequency-appropriate, unanswered experiment for AURUM-1.
4. Does the LLM scoring advantage over FinBERT translate to actual post-cost trading value, or is it classification-accuracy only (given JPM 2025 finds no text sentiment yields gold economic gains)?

## G. Implication for AURUM-1 (practical synthesis)

- **The evidence resolves to: forecast accuracy improves, but post-cost trading value for spot gold is not demonstrated.**
- **Disciplined recommendation: finish the 200-trade DSR gate on price-only D4 first.** We're mid-gate at 104 trades (DSR 0.274 underpowered, 2/3 automated criteria passed; criterion-4 additional-stream is manual review).
- **The highest-leverage first experiment** (if pursued): a **detached offline test** — does t-2 daily sentiment directionality add next-day OOS predictive power over the price-only baseline? Use the Sinha & Khandait human-labeled dataset (research-only, license-respected). Before ANY live integration.
- **The frequency-appropriate design for M15** is an **event-study around scheduled macro releases (CPI/FOMC/NFP)** — not a continuous daily sentiment stream (which sits at daily granularity and can't be mechanically transferred to M15).
- **Generic FinBERT should NOT be the scorer** — either lightweight LLMs (Llama3/Qwen3 8B) or gold-specific directionality labels.
- **Production deployment requires a commercially licensed feed.**

## H. Paper trail — what we reviewed and verified

| Paper / source | Verdict |
|---|---|
| Dong et al. 2025 (JPM) — sentiment economic value 4 assets | ✅ Real, strongest skeptical evidence |
| Novotny & Hájek 2026 (Fin. Innovation) — ETF sentiment SVR+PPO | ✅ Real, only after-costs positive (ETFs, caveats) |
| Roache & Rossi 2010 — gold safe-haven | ✅ Real, foundational inversion |
| Mrad et al. 2026 (IMCET) — GPT-4o sentiment | ✅ Real, weak OOS (overfit concern) |
| Ji et al. 2026 (Entropy) — CNN-QRLSTM | ✅ Real, ~1 citation, no replication |
| Daniati et al. 2025 (BTS-I2C) — LSTM t-2 | ✅ Real, t-2 lag finding |
| Elkarnighi et al. 2026 (RIBAF) — conditional value | ✅ Real, "selected markets not uniform" |
| Sinha & Khandait 2021 — gold news dataset | ✅ Real, human-annotated, CC BY-NC-ND |
| arXiv:2512.00946 — FinBERT vs LLMs on gold | ✅ Real, FinBERT -30% gap |
| Elder/Miao/Ramchander 2012 — macro news metal futures | ✅ Real, intraday 8:30am |
| Cai/Cheung/Wong 2001 — what moves gold | ✅ Real, employment/GDP/CPI/income |
| Ederington & Lee 1993 — news releases & volatility | ✅ Real, foundational |
| "LSTM/DNN benefit most from sentiment" | ❌ REFUTED (overreach) |
| "LLM scoring is the current method of choice" | ❌ REFUTED (not settled) |
