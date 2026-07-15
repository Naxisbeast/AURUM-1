# The AURUM-1 Journey

A student's log of building an algorithmic trading system — the wins, the losses, and everything in between.

> *"If you're not failing, you're not learning."*

---

## Why This Exists

I'm a student who got obsessed with algorithmic trading. This folder is the raw, unfiltered story of building AURUM-1 — not the polished README version, but the real one. The bugs that made me want to throw my laptop out the window. The "aha" moments at 2am. The trades that worked and the ones that didn't.

If you're reading this and building something yourself: **this is what it actually looks like.** It's not clean. It's not linear. But it works.

---

## Chapter 1: The Great Orchestrator Crash (May 27)

**The dream**: A single Python script that does everything — fetch data, generate signals, manage risk, execute trades. The "main orchestrator."

**The reality**: It ran for a few hours then got murdered by signal_2 (SIGTERM from OOM killer). Over and over.

I spent weeks trying to fix it. Added retry logic. Added memory limits. Added health checks. Nothing worked reliably.

**What I learned**: Sometimes the simplest architecture wins. Instead of one monolith, I broke it into:
- `forward-shadow.service` — just fetches market data
- `d4-paper-trader.service` — just trades using that data
- `dashboard.service` — just shows you what's happening

Each one is tiny. Each one does one thing. And they all actually stay running.

---

## Chapter 2: The Silent DB Bug (Jul 2-7)

This one hurt. For **5 days**, the paper trader was executing trades correctly — entries, stop losses, take profits — all working perfectly. But **zero trades** showed up in the database.

I'd SSH into the server, run `SELECT COUNT(*) FROM trades`, and get back `0`.

The system made +$150 in those 5 days. I just couldn't prove it.

**The root cause**: The trades table had a `timestamp TEXT NOT NULL` column from an earlier schema version. The code that wrote new trades used `entry_time` and `exit_time` instead, never providing a value for the old `timestamp` column. Every write silently failed with `NOT NULL constraint failed`. The `except` handler caught it and printed nothing useful.

**What I learned**: 
- Never use `except: pass` — you're blind
- Check your database actually has data, not just that the code "works"
- Schema migrations are dangerous if you don't clean up old columns

---

## Chapter 3: D4 > Everything Else

I built 6 strategy variants:
- D1: Filtered 1R (complex)
- D2: Filtered 1R BUY-only (complex)
- D3: Filtered 1R BUY+SELL (complex)
- **D4: No filters, 2R, BUY+SELL (simple)**
- D5: Adaptive ATR + volume (over-engineered)
- D6: Same as D4 but with ML ensemble (over-engineered)

I ran the walk-forward validation across 11 years of data and the result was embarrassing:

| Variant | Complexity | Profit Factor |
|---------|:----------:|:------------:|
| D4 | **Minimal** | **1.14** 🏆 |
| D6 | ML ensemble | 1.14 (identical) |
| Raw | BUY only | 1.14 |
| D2 | Filters + 1R | 1.03 |
| D3 | Filters + SELL + 1R | 1.02 |

**The simplest possible strategy beat everything.** Adding ML, adding filters, adding complexity — none of it helped. The Donchian 20 breakout with a fixed 2R exit, both directions, no filters. That's it.

I spent weeks on ML models. The D4 took an afternoon. 

**What I learned**: Try the dumbest thing first. It's usually good enough.

---

## Chapter 4: The First Live Trade (Jul 7)

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

By end of day Jul 7: **+$215** (6 trades, 4 wins, 2 losses).

**What I learned**: Watching a live system trade with your strategy is terrifying and amazing. Every loss feels personal. But the math works if you trust it.

---

## Chapter 5: The SELL Streak (Jul 8)

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

## Chapter 6: The Dashboard That Didn't Work (Jul 14)

I had a beautiful Streamlit dashboard. Charts. Metrics. Auto-refresh. Everything.

I deployed it to the server. Nothing showed up. Empty charts everywhere.

**The problem**: The dashboard read from `aurum1.sqlite3` (the old database), but the D4 paper trader wrote to `paper_trading.sqlite3` (the new database). Two different databases, one with data and one without.

I added a fallback: read from `paper_trading.sqlite3` first, fall back to the old DB if empty. Fixed.

Then the dashboard worked but the **trade markers didn't correspond to the equity curve**. The markers showed entry prices ($4,000) on a chart showing equity ($10,000). They floated in space, disconnected from the line.

Fixed with dual y-axes. Then the dark theme made text invisible. Fixed with config. Then the tunnel stopped working because the IP got blocked. Fixed with Cloudflare.

**What I learned**: The last 10% of a project takes 90% of the time. Every "simple" fix reveals another issue.

---

## Chapter 7: The Account ID Isn't What You Think (Jul 15)

The forward shadow stopped fetching data. Logs showed a 401 error from OANDA.

I checked the API key — it was fine. Then I checked the account ID.

The `.env` file had:
```
OANDA_ACCOUNT_ID=29875785001
```

But the API returned:
```json
{"accounts":[{"id":"101-002-29875785-001"}]}
```

Somewhere along the way, OANDA changed their ID format. The old flat number stopped working. The new format with dashes was required. **4 hours of stale market data** while I figured this out.

**What I learned**: API credentials change without warning. Monitor your data pipeline separately from your trading pipeline. If the data stops flowing, you won't know until something breaks.

---

## Current State (Jul 15, 2026)

After all of this:
- **21 live trades closed** — 11 wins, 10 losses
- **+$228 net PnL** (-$20.70 on the latest loss)
- **$10,428.42 equity** (+4.28% from $10k)
- **55% win rate** (exactly at the expected range)
- **+0.57 avg R** (in line with backtest)

The system runs 24/7 on a cloud server. It doesn't need me. It just works.

---

## What's Next

- **100 trades** — not changing anything until we have real sample size
- **Breakeven stop** — a circuit breaker if things go bad
- **Maybe nothing** — this strategy might just work as-is

---

*This is a living document. Updated as things break and get fixed. Because they always break.*
