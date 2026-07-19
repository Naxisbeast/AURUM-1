# The AURUM-1 Journey

A student's raw, unfiltered log of building an algorithmic trading system from scratch — the wins, the losses, the 2am debugging sessions, and everything in between.

> *"AURUM-1 was never built to prove itself right. It was built to prove itself wrong."*

---

## Why This Exists

I'm a student who got obsessed with algorithmic trading. This folder is the real story — not the polished README, but the truth. The phases that went right, the bugs that went wrong, and everything I learned along the way.

If you're reading this and building something yourself: **this is what it actually looks like.** It's not clean. It's not linear. But it works.

---

## Phase 1 — The Data Layer (May 27)

I set out to build a complete algorithmic trading system for Gold (XAU/USD). I wanted it modular, testable, and production-grade — something that could eventually run 24/7 on a server without me babysitting it.

The first layer was the data pipeline. I needed real market data, not synthetic or proxy sources.

**What I built:**
- `aurum1/data/ingestion.py` — OANDA API with yfinance fallback, FRED macro data, CFTC COT parser, Alpha Vantage news, economic calendar blackout windows
- `aurum1/config/settings.yaml` — all tunable parameters
- `tests/test_phase1_ingestion.py` — 10 tests covering every component

**Result**: 9 passed, 1 skipped (live smoke test needs API keys).

Then I went back and hardened things — better OHLCV loading with datetime index enforcement, macro merge using `merge_asof` so intraday candles get the most recent macro observation, and COT parser hardening for the disaggregated CFTC format.

**Result**: 12 passed, 1 skipped.

---

## Phase 2 — Feature Engineering (May 27)

The feature engineering pipeline turns raw OHLCV into 50+ technical indicators, session flags, macro features, and multi-timeframe confluence.

**What I built:**
- `aurum1/features/engineer.py` — FeatureEngineer class
- EMAs (9, 20, 50, 100, 200) with slopes and alignment scores
- ATR(14), RSI(14), MACD, Bollinger Bands, ADX
- Volume features (rel_volume, VWAP deviation)
- Session flags (Asia/London/NY/overlap)
- Time encoding (hour/dow sin-cos)
- Macro and COT merging
- Multi-timeframe confluence (H1, H4)
- Target variable for ML training
- Lookahead bias enforcement (`assert_no_lookahead`)
- 10 tests

**Result**: 22 passed, 1 skipped.

---

## Phase 3 — ML Models (May 27)

The ML layer had four models, an ensemble combiner, and an automated retrainer:

- **RegimeClassifier** — LightGBM, 7 features, 3 classes (TRENDING_UP, TRENDING_DOWN, RANGING)
- **DirectionPredictor** — LSTM, sequence length 60, focal loss for class imbalance
- **SentimentScorer** — FinBERT from HuggingFace, lazy-loaded, thread-safe
- **EnsembleSignal** — weighted combination with ranging penalties
- **ModelRetrainer** — weekly retraining with promotion gate (0.05 Sharpe improvement threshold)

Plus ablation tests and LSTM promotion gates to prevent overfitting.

**Result**: 35 passed, 1 skipped.

I tested it myself:
```
Regime F1: {'0': 0.7424, '1': 0.7359, '2': 1.0}
Direction accuracy: 0.6098
```

At this point I had a complete pipeline: data → features → ML → signal. But it was all synthetic. I needed real data and real validation.

---

## Phase 11.1 — The Safety Sprint (May 28)

Before going further, I audited the system and found issues everywhere. The system was **not ready for paper trading** and I wouldn't let it run until things were fixed.

| Issue | Problem | Fix |
|-------|---------|-----|
| XAU/USD unit math | PnL off by 10x-1000x | Centralized InstrumentSpec |
| Backtest DB pollution | Fake trades in runtime DB | Isolated backtest databases |
| Same-candle SL/TP | Could open & close on same bar | Next-candle entry, stop-first |
| No safety interlocks | Accidental live trading possible | ALLOW_OANDA_ORDERS, ALLOW_LIVE_TRADING |
| Sharpe calculation | Used raw equity, not daily returns | Daily return Sharpe |
| Walk-forward overlap | Overlapping windows = inflated results | Non-overlapping default |
| ML ensemble fail-closed | Ran without real model artifacts | Blocked when artifacts missing |
| Dashboard security | Bound to 0.0.0.0 (exposed) | Locked to 127.0.0.1 |

**25 files changed, +645 lines.**

```
Engineering validation: passed
Quantitative readiness: passed
Paper readiness: failed
Live readiness: failed
```

---

## Phase 11.3 — Real Market History (May 28)

The next problem: I only had 5,000 candles of OANDA data. Not enough for meaningful validation.

**Requirements:**
- At least 20,000 M15 candles
- At least 250 calendar days
- Real OANDA XAU/USD only — no proxies, no synthetics

I built `scripts/fetch_oanda_history.py` and `scripts/audit_market_cache.py`.

**Result:**

```
Rows fetched: 47,338
Date range: 2024-05-28 → 2026-05-28 (730 days)
Duplicates: 0
Synthetic/proxy used: no
```

The backtest on real data:
```
Best mode: rule_regime
Sharpe: 3.05 (daily returns)
PF: 1.95
Trades: 801
Walk-forward: 24 non-overlapping windows, promotion gate PASSED 6/6
```

---

## The Forward Shadow Goes Live (June 1)

With validation done, I deployed the forward shadow to a cloud server. This was a locked research run — **no live trading, no broker orders**:

- Raw Donchian breakout, BUY-only, lookback 20, fixed 2R
- 0.25% risk per trade
- ALLOW_OANDA_ORDERS=false, ALLOW_LIVE_TRADING=false

The deployment was full production:
- Ubuntu 24.04 server, Python 3.12 venv, GitHub deploy key
- systemd services with timers
- Log rotation, SQLite backups, health monitoring

**First weekly report:**
```
Gross P&L: -155.99
Win rate: 0.00%
Trades: 6
Average R: -1.001
```

Not great. But it was real data from a real system running 24/7.

---

## The Dashboard (June 9)

I built a read-only Streamlit dashboard to stop SSH'ing in for every check:

- Equity curve, drawdown chart, trade list, signal list
- Weekly report viewer, candle chart with trade markers
- **Skipped Signal What-If Analysis** — simulated what would have happened if skipped trades were taken. Yellow markers for hypothetical entries. This answered: "Is the skip logic helping or hurting?"

---

## Research Phases S1-S5 (June 11)

The forward shadow was losing. I needed to understand **why** — without changing the live execution. This kicked off five diagnostic research phases.

### S1: Failure Audit
Every trade and skipped signal was audited:
```
Closed trades: 13 | Wins: 2 | Losses: 11
Net P&L: -203.80 | PF: 0.36
Skipped: 20 (would have lost 15, won 2)
```
**Finding**: Skip logic was *helping*. The skipped signals would have lost even more.
**Decision**: `SAMPLE_TOO_SMALL_CONTINUE`

### S2: Context Filter Simulation
```
Baseline:              13 trades, avgR -0.540, PF 0.36
Exclude high vol + London: 5 trades, avgR +0.198, PF 1.33
```
Also discovered: **SELL signals were missing entirely** (34 BUY, 0 SELL).
**Decision**: `VOLATILITY_FILTER_PROMISING`

### S3: Candidate Filter Replay
```
Baseline:      avgR -0.540, PF 0.36
Best variant:  avgR +0.248, PF 1.66
```
**Decision**: `FILTER_REPLAY_PROMISING_SAMPLE_LIMITED`

### S4: Candidate Lock
Tested four candidates. **Winner**: D1 — volatility filter + session filter + 1R exit.

### S5: D1 Shadow Journal
A live journal running alongside the system, making hypothetical TAKE/HOLD decisions but never executing. The foundation for the D4 paper trader.

---

## The Silent DB Bug (July 2-7)

After all that research, I deployed the **D4 paper trader** — Donchian 20, 2R exit, BUY+SELL, no filters.

It executed trades correctly. But when I checked the database:

```sql
SELECT COUNT(*) FROM trades;  -- Returns: 0
```

For **5 days**, the system made real paper trades — entries, stops, take profits — all working perfectly. But **zero trades** showed up in the database. +$150 made, invisible.

**Root cause**: The trades table had a `timestamp TEXT NOT NULL` column from an older schema. The INSERT statement never provided a value. Every write silently failed with `NOT NULL constraint failed`. The `except` handler caught it and printed nothing useful.

---

## D4 > Everything Else

I had built 6 strategy variants by this point:

| Variant | Complexity | Profit Factor |
|---------|:----------:|:------------:|
| **D4** | **Minimal** | **1.14** |
| D6 | ML ensemble | 1.14 (identical) |
| Raw | BUY only | 1.14 |
| D2 | Filters + 1R | 1.03 |
| D3 | Filters + SELL + 1R | 1.02 |

The simplest strategy beat everything. Adding ML, adding filters, adding complexity — none of it helped. The Donchian 20 breakout with a fixed 2R exit, both directions, no filters. That's it.

---

## First Live Trade (July 7)

```
Jul 07 11:46  ENTRY BUY @ $4,133.30 | SL=$4,118.51 TP=$4,162.87
Jul 07 12:31  EXIT BUY R=+2.00 PnL=$+59.14 | take_profit
Jul 07 12:31  ENTRY BUY @ $4,157.19
Jul 07 16:16  EXIT BUY R=-1.00 PnL=$-17.36 | stop_loss
```

A win. Then a loss. Right back. This is how the 2R structure works — you need less than 50% wins to be profitable.

### The SELL Streak (July 8)

XAUUSD trended down. The strategy hit SELL after SELL:

```
SELL @ $4,142 → TP @ $4,110  +$64
SELL @ $4,127 → TP @ $4,090  +$37
SELL @ $4,118 → TP @ $4,078  +$40
SELL @ $4,089 → TP @ $4,043  +$46
```

4 consecutive winners. Equity jumped from $10,214 to $10,384. Then the reversal came — 3 consecutive losses dropping back to $10,350.

---

## Dashboard & Infrastructure (July 14-15)

Deploying the dashboard was a comedy of errors:

1. **Wrong database** — Dashboard read from the old DB, paper trader wrote to the new one. Empty charts.
2. **Wrong chart axis** — $4,000 trade prices plotted on a $10,000 equity curve. Floating in space.
3. **ISP block** — The server IP got blacklisted by my provider. Fixed with Cloudflare Tunnel.
4. **OANDA account ID changed** — OANDA changed their ID format without warning. `.env` had `29875785001` but the API now expected `101-002-29875785-001`. 4 hours of stale data while I figured it out.

---

## Bugs I Killed Along the Way

1. **Trade recording** — `timestamp NOT NULL` column with no value provided. 5 days of invisible trades.
2. **Signal double-count** — `_signals_seen` incremented in both detection AND execution blocks. Double the real count.
3. **Risk distance** — Used slippage-adjusted price instead of raw signal price for sizing. Didn't match the backtest.
4. **Silent `except: pass`** — Database cleanup could fail silently. Nobody would know.
5. **OANDA account ID** — Format changed without notice.

---

## The Philosophy

Throughout this entire journey, one rule stayed constant:

> Nothing gets promoted unless forward evidence justifies it.

**Core principles:**

1. Never optimize for profitability. Optimize for **understanding**.
2. Every hypothesis gets measured. Never say "2R doesn't work." Ask: "Does 2R work?"
3. No curve fitting. No parameter optimization. No changing TP because of losses.
4. Research first. Always: strategy → research → diagnostics → forward validation → improvement.

> *"AURUM-1 was never built to prove itself right. It was built to prove itself wrong. Every phase of development has been an attempt to answer: Why did we lose? Why did we win? What destroyed expectancy? What haven't we measured yet?"*

---

## Current State (July 15, 2026)

| Metric | Value |
|--------|-------|
| Trades closed | 21 (11W / 10L) |
| Net PnL | +$273 |
| Equity | $10,428 (+4.28%) |
| Peak equity | $10,449 (+4.49%) |
| Avg R | +0.57R |

**Infrastructure:**
- Ubuntu 24.04 cloud server
- D4 paper trader (continuous service)
- Forward shadow (market data cache)
- Streamlit dashboard via Cloudflare Tunnel
- Daily SQLite backups (>1 GB total)

**Research:**
- Phases S1-S5 completed (failure audit → context filters → candidate lock)
- Walk-forward validation, TC stress test, ICIR analysis
- 47k+ real OANDA candles across 2 years

**Safety:**
- Research-only by design
- ALLOW_OANDA_ORDERS=false
- ALLOW_LIVE_TRADING=false
- 3 independent kill switches
- Full backup recovery

---

## Timeline

```
May 27 — Built Phase 1 (Data), Phase 2 (Features), Phase 3 (ML)
May 28 — Safety sprint + real history fetch (47k OANDA candles)
Jun 1  — Forward shadow deployed to cloud server
Jun 9  — Dashboard + What-If Analysis
Jun 11 — Research S1-S5: failure audit through D1 shadow journal
Jun 28 — D4 paper trader deployed
Jul 2  — First trade. Silent DB bug begins.
Jul 7  — DB bug fixed. First TP hit recorded.
Jul 8  — SELL streak: 4 wins in one day
Jul 14 — Dashboard deployed + documentation overhaul
Jul 15 — OANDA account ID fixed. Cloudflare tunnel.
          → 21 trades, +$273 net, $10,428 equity ←
```

---

## What's Next

- **100 trades** — Not changing anything until we have a real sample size
- **Breakeven stop** — A circuit breaker for when things go bad
- **Maybe nothing** — This strategy might just work as-is

> *"AURUM-1 is not a finished trading system. It is an evolving quantitative research platform whose greatest success so far has not been profitability, but the amount it has taught us about market behavior, strategy design, and the importance of disciplined experimentation."*

---

*This is a living document. Updated as things break and get fixed. Because they always break.*
