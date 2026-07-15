# The AURUM-1 Journey

A student's raw, unfiltered log of building an algorithmic trading system from scratch — the wins, the losses, the 2am debugging sessions, and everything in between.

> *"If you're not failing, you're not learning."*
> *"The simplest strategy usually wins."*

---

## Why This Exists

I'm a student. I got obsessed with algorithmic trading. This folder is the real story of building AURUM-1 — not the polished README, but the truth. The bugs that made me want to throw my laptop. The research phases that went nowhere. The moments where it finally clicked.

If you're reading this and building something yourself: **this is what it actually looks like.** It's not clean. It's not linear. It took way longer than I expected. But in the end, it works.

---

## Chapter 1: The Beginning — Phase 11.1 Safety Sprint (May 28)

I started building. Then I realized how much I didn't know.

The first real milestone was **Phase 11.1** — a full safety audit. At this point the system existed but was basically untrustworthy. The audit found issues everywhere:

**The problems:**
- XAU/USD unit math was wrong (PnL could be off by a factor of 10, 100, or 1000)
- Backtests were polluting the runtime database with fake trades
- The same-candle SL/TP logic was too optimistic — you could open and close a trade on the same candle
- No interlocks preventing accidental live trading
- Sharpe was calculated from raw equity observations, not daily returns
- Walk-forward windows were overlapping, making results look better than they really were
- ML ensemble was allowed to run even when model artifacts were missing
- Dashboard was binding to 0.0.0.0 by default — exposed to the internet

**What I fixed:**
- Created a centralized `InstrumentSpec` for XAU/USD unit/lot/pip math
- Isolated backtest databases from runtime state
- Fixed execution realism: next-candle entry, stop-first on same-candle SL/TP
- Added hard interlocks: `ALLOW_OANDA_ORDERS`, `ALLOW_LIVE_TRADING`
- Fixed Sharpe to use daily returns
- Made walk-forward non-overlapping by default
- Blocked FULL_ENSEMBLE when model artifacts are missing
- Locked dashboard to localhost

**Result:**

```
Engineering validation: passed
Quantitative readiness: passed
Paper readiness: failed
Live readiness: failed
```

At this point, the system was safe enough to run as research-only. But it wasn't ready for real validation yet.

---

## Chapter 2: Real History — Phase 11.3 (May 28)

The next problem: I only had 5,000 candles of OANDA data. Not nearly enough to validate anything meaningful.

**The requirements:**
- At least 20,000 M15 candles
- At least 250 calendar days
- Real OANDA data only — no yfinance, no GC=F proxy, no synthetic fallback

I built:
- `scripts/fetch_oanda_history.py` — fetches real OANDA XAU/USD candles with pagination
- `scripts/audit_market_cache.py` — audits the cache for gaps, duplicates, freshness

**Results:**

```
Rows fetched: 47,338
Duplicates removed: 0
Date range: 2024-05-28 → 2026-05-28 (730 days)
Readiness eligible: yes
```

Now I had **2 years of real data**. No proxies. No synthetics. This was the foundation for everything that followed.

The backtest on real data:

```
Best mode: rule_regime
Sharpe: 3.05  (on daily returns)
PF: 1.95
Trades: 801
Walk-forward: 24 non-overlapping windows, promotion gate PASSED 6/6
```

For the first time, I had confidence the strategy wasn't just noise.

---

## Chapter 3: The Forward Shadow Goes Live (June 1)

With validation done, I deployed the forward shadow to the server. This was the locked 3-month research run:

- Raw Donchian breakout
- BUY-only (SELL was disabled pending research)
- Lookback 20, fixed 2R exit
- 0.25% risk per trade
- No OANDA orders, no live trading

**The deployment checklist was brutal:**
- Python 3.12 venv
- Server deploy key for GitHub
- 18-step deployment handover
- Systemd service, weekly report timer, backup timer
- Safety interlocks verification
- Log rotation, SQLite backups, smoke tests

But it worked. The service ran continuously. Every candle was processed. Every trade was logged. I could SSH in and check:

```bash
systemctl status aurum1-forward-shadow.service
journalctl -u aurum1-forward-shadow.service -f
```

For the first time, I had a system running 24/7 on a real server, processing real market data, without me having to babysit it.

**The first weekly report:**

```
Gross P&L: -155.99
Net P&L: -156.17
PF: 0.00
Win rate: 0.00%
Trades: 6
Average R: -1.001
```

Not great. But it was real data. And the whole point of the forward shadow was to learn.

---

## Chapter 4: The Dashboard (June 9)

I was tired of SSH'ing into the server to check trades. I wanted a visual dashboard.

I built `dashboard/forward_shadow_dashboard.py` — a read-only Streamlit dashboard with:
- Equity curve
- Drawdown chart
- Trade list
- Signal/skipped signal list
- Weekly report viewer
- Candle chart with trade markers

**The rule**: Read-only. No buttons to start/stop trades. No config mutation. No write operations. Just observation.

Then I added something called **"Skipped Signal What-If Analysis"** — for every signal that was skipped by the filter logic, I simulated what would have happened if the trade was taken. Yellow markers on the chart for hypothetical entries. A table showing hypothetical outcomes.

The goal was to answer: *"Is the skip logic helping or hurting?"*

---

## Chapter 5: The Research Phases — S1 Through S5 (June 11)

At this point, the forward shadow had been running with disappointing results. The first 13 trades were mostly losses. I needed to understand **why** without changing the live execution.

This kicked off a series of diagnostic research phases. Each phase was a Python script that read the shadow database and produced reports — **no execution code was ever modified**.

### Phase S1: Failure Audit

Audited every trade and skipped signal:

```
Closed trades: 13
Wins: 2
Losses: 11
Net P&L: -203.80
Avg R: -0.540
PF: 0.36
Win rate: 15.38%
```

**Key finding**: The skip logic was actually *helping* — skipped signals would have lost even more. The problem wasn't the filter, it was the raw entry logic.

**Research decision**: `SAMPLE_TOO_SMALL_CONTINUE`

### Phase S2: Context Filter Simulation

Tested filter variants without changing the runner:

```
Baseline:          13 trades, avgR -0.540, PF 0.36
Exclude high vol:   8 trades, avgR -0.252, PF 0.66
Exclude London:     8 trades, avgR -0.252, PF 0.66
Exclude high vol + London: 5 trades, avgR +0.198, PF 1.33
```

Also discovered: **SELL signals are missing**. The strategy was BUY-only, and SELL signals simply didn't exist in the data.

**Research decision**: `VOLATILITY_FILTER_PROMISING`

### Phase S3: Candidate Filter Replay

Replayed every raw signal with TAKE/HOLD decisions using context filters:

```
Baseline:                 avgR -0.540, PF 0.36, netR -7.024
Best variant (vol+London filter, 1R): avgR +0.248, PF 1.66, netR +3.965
```

**Warning**: SHORT_SIDE_MISSING — 34 BUY signals, 0 SELL signals.

**Research decision**: `FILTER_REPLAY_PROMISING_SAMPLE_LIMITED`

### Phase S4: Candidate Lock

Locked four candidate rules (D1-D4) for shadow comparison:

**Winner**: D1 — vol filter + session filter + fixed 1R exit. The best balance of PF, trade count, and drawdown.

### Phase S5: D1 Shadow Forward Journal

The final research phase. I deployed the D1 candidate as a **shadow journal** — running alongside the existing system, making hypothetical TAKE/HOLD decisions, but never executing real trades.

The journal tracked every raw signal, recorded the D1 decision, and simulated the outcome. This was the foundation for what would eventually become the D4 paper trader.

---

## Chapter 6: The Silent DB Bug (July 2-7)

After all that research, I deployed the **D4 paper trader** — the improved strategy based on everything S1-S5 taught us. D4 was Donchian 20, 2R exit, BUY+SELL, no filters.

**It executed trades correctly.** But when I checked the database:

```sql
SELECT COUNT(*) FROM trades;
-- Returns: 0
```

For **5 days**, the paper trader was making real paper trades — entries, stop losses, take profits — all working perfectly. But **zero trades** showed up in the database. The system made +$150 in those 5 days. I just couldn't prove it.

**The root cause**: The trades table had a `timestamp TEXT NOT NULL` column from an earlier schema version. The code that wrote new trades used `entry_time` and `exit_time` instead, never providing a value for the old column. Every write silently failed with `NOT NULL constraint failed`. The `except` handler caught it and printed nothing useful.

**What I learned**: 
- Never use `except: pass` — you're blind
- Check your database actually has data, not just that the code "works"
- Schema migrations are dangerous if you don't clean up old columns

---

## Chapter 7: D4 > Everything Else

I built 6 strategy variants:
- D1: Filtered 1R (complex)
- D2: Filtered 1R BUY-only (complex)
- D3: Filtered 1R BUY+SELL (complex)
- **D4: No filters, 2R, BUY+SELL (simple)**
- D5: Adaptive ATR + volume (over-engineered)
- D6: Same as D4 but with ML ensemble (over-engineered)

I ran the walk-forward across 11 years of M15 data. The result was embarrassing:

| Variant | Complexity | Profit Factor |
|---------|:----------:|:------------:|
| **D4** | **Minimal** | **1.14** 🏆 |
| D6 | ML ensemble | 1.14 (identical) |
| Raw | BUY only | 1.14 |
| D2 | Filters + 1R | 1.03 |
| D3 | Filters + SELL + 1R | 1.02 |

**The simplest possible strategy beat everything.** Adding ML, adding filters, adding complexity — none of it helped. The Donchian 20 breakout with a fixed 2R exit, both directions, no filters. That's it.

I spent weeks on ML models. The D4 took an afternoon.

**What I learned**: Try the dumbest thing first. It's usually good enough.

---

## Chapter 8: The First Live Trade (July 7)

After the DB fix, I restarted the service and watched the logs. Nothing happened for 8 days. Then:

```
Jul 07 11:46:19 ENTRY BUY @ $4133.30 | SL=$4118.51 TP=$4162.87 | Units=2.0
```

45 minutes later:

```
Jul 07 12:31:11 EXIT BUY R=+1.997 PnL=$+59.14 | take_profit
```

The first trade that actually recorded to the database. A **+2R win**.

Then immediately:

```
Jul 07 12:31:11 ENTRY BUY @ $4157.19 | SL=$4139.83 TP=$4191.90
```

And later that day:

```
Jul 07 16:16:19 EXIT BUY R=-1.002 PnL=$-17.36 | stop_loss
```

A loss. -1R. Right back. This is how it works — the 2R structure means you need less than 50% wins to be profitable.

---

## Chapter 9: The SELL Streak (July 8)

XAUUSD trended down hard. The strategy switched to almost all SELL signals:

```
SELL @ $4,142 → TP @ $4,110  +$64
SELL @ $4,127 → TP @ $4,090  +$37
SELL @ $4,118 → TP @ $4,078  +$40
SELL @ $4,089 → TP @ $4,043  +$46
```

**4 consecutive SELL winners.** Equity jumped from $10,214 to $10,384 in one day.

Then:

```
SELL @ $4,048 → SL @ $4,072  -$23
SELL @ $4,060 → SL @ $4,087  -$27
BUY  @ $4,080 → SL @ $4,057  -$23
```

3 consecutive losses. The market reversed. Equity dropped back to $10,350.

**What I learned**: Streaks feel meaningful but they're just noise in a 37% win-rate system. Don't get cocky on a win streak, don't get depressed on a loss streak.

---

## Chapter 10: The Dashboard That Didn't Work (July 14)

I had a beautiful Streamlit dashboard. Charts. Metrics. Auto-refresh. Everything.

I deployed it to the server. Nothing showed up. Empty charts everywhere.

**The problem**: The dashboard read from `aurum1.sqlite3` (the old database), but the D4 paper trader wrote to `paper_trading.sqlite3` (the new database). Two different databases, one with data and one without. Took me an embarrassing amount of time to realize.

Then the trade markers didn't correspond to the equity curve. The markers showed entry prices ($4,000) on a chart showing equity ($10,000). They floated in space. Fixed with dual y-axes.

Then the dark theme made text invisible. Fixed with config.

Then the ISP blocked the server IP. Fixed with Cloudflare Tunnel.

**What I learned**: The last 10% of a project takes 90% of the time. Every "simple" fix reveals another issue underneath.

---

## Chapter 11: The Account ID Isn't What You Think (July 15)

The forward shadow stopped fetching data. Logs showed a 401 error from OANDA.

I checked the API key — it was fine. Then I checked the account ID.

The `.env` file had: `OANDA_ACCOUNT_ID=29875785001`

The API returned: `{"accounts":[{"id":"101-002-29875785-001"}]}`

Somewhere along the way, OANDA changed their ID format. The old flat number stopped working. The new format with dashes was required. **4 hours of stale market data** while I figured this out.

**What I learned**: API credentials change without warning. Monitor your data pipeline separately from your trading pipeline.

---

## Chapter 12: Bugs I Killed Along the Way

These are the bugs I found and fixed that weren't in any research phase — just things I discovered by reading the code carefully:

1. **Trade recording** — `timestamp NOT NULL` column with no value provided. All trades silently lost for 5 days.
2. **Signal double-count** — `_signals_seen` was incremented in both the detection block AND the execution block. Reported double the real count.
3. **Risk distance** — Used the slippage-adjusted entry price instead of the raw signal price. Position sizing didn't match the walk-forward.
4. **Silent exception handling** — `except: pass` on database cleanup operations. Never knew when they failed.
5. **OANDA account ID format** — Changed without notice by OANDA's API. 4 hours of stale data.

---

## Current State (July 15, 2026)

After all of this:

- **21 live trades closed** — 11 wins, 10 losses
- **+$273 net PnL** (-$20.70 on the latest loss)
- **$10,428 equity** (+4.28% from $10k)
- **52% win rate** (in line with backtest expectations)
- **+0.57 avg R** (exactly where the backtest predicted)
- **$449 peak equity** (+4.49%)

The system runs 24/7 on a cloud server. It doesn't need me. It just works.

---

## What's Next

- **100 trades** — not changing anything until we have a real sample size
- **Breakeven stop** — a circuit breaker for when things go bad
- **Maybe nothing** — this strategy might just work as-is

---

## Timeline

```
May 27   — Orchestrator crash. D4 becomes the new direction.
May 28   — Phase 11.1 safety sprint. Phase 11.3 real history fetch.
Jun 1    — Forward shadow deployed to cloud server.
Jun 9    — Dashboard built. What-if analysis added.
Jun 11   — Phases S1-S5: Failure audit, context filters, candidate lock.
Jun 28   — D4 paper trader deployed.
Jun 29   — Fixed data source (yfinance → local OANDA cache).
Jul 2    — First trade. Silent DB bug begins.
Jul 7    — DB bug found and fixed. First TP hit recorded.
Jul 8    — SELL streak: 4 wins in one day.
Jul 10   — Drawdown: 3 consecutive stop losses.
Jul 13   — Recovery: 3 SELL wins.
Jul 14   — Dashboard deployed. Documentation overhaul.
Jul 15   — OANDA account ID fixed. Cloudflare tunnel set up.
          → 21 trades, +$273 net, $10,428 equity ←
```

---

*This is a living document. Updated as things break and get fixed. Because they always break.*
