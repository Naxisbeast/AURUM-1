# AURUM-1: The Journey - A Trading System's Evolution

**Last updated**: 2026-07-20

This isn't a sanitized status report. This is the real story - the decisions, the
dead ends, the bugs, the fixes, and the lessons that didn't come from a textbook.
The final numbers never tell the whole story.

---

## Why AURUM Exists

AURUM wasn't started to create another trading bot.

The goal was always bigger than that: **can one person build a quantitative
research platform capable of discovering strategies, testing them honestly,
rejecting bad ideas, surviving production failures, and eventually becoming
something trustworthy enough for real capital?**

The strategy isn't the product. The research process is.

If D4 dies tomorrow and D8 replaces it, AURUM still succeeds if the process
that found D4 survives. AURUM is an experiment in building systems that make
decisions from evidence rather than intuition.

This journal exists because success isn't interesting without understanding
how we got there - and the failures along the way taught us more than the wins
ever did.

---

## How This Was Built

Let me be honest about something that matters.

A significant amount of AURUM's code was written with AI assistance - specifically
Claude. Large sections of the broker, the backtesting engine, the dashboard, the
test suite, and the hardening cleanup were built in collaboration with an AI.

If you're reading this hoping for a "pure solo-coded quant platform" story ...
that's not this journal.

**But here's what AI didn't do:**

- AI didn't decide to build AURUM
- AI didn't choose D4 as the strategy
- AI didn't design the architecture (decoupled data pipeline, PaperBroker pattern,
  single-instance lock - those were mine)
- AI didn't interpret the backtest results or decide when to bump risk
- AI didn't catch the bugs or decide which fixes mattered most
- AI didn't sit through 27 trades wondering if the whole thing was broken
- AI didn't learn the hard way that simplicity beats complexity

AI is a tool in my hands - like Python, like SQLite, like the OANDA API.
It makes me faster. It catches things I'd miss. It helps me iterate on ideas
I wouldn't have time to explore alone. But it doesn't replace the judgment,
the domain knowledge, or the patience that building a system like this requires.

If you're also building with AI assistance, stop feeling guilty about it.
The tool doesn't write the vision. You do.

I mention this because the project should tell the truth about itself -
and that includes how it was built.

---

## Timeline

```
Pre-2026
--------
Learning Python. Learning markets. Building experiments that went nowhere.
Jupyter notebooks full of half-finished ideas.

May 2026
--------
Main orchestrator built - full ML pipeline, state machine, ensemble voting.
Elegant. Overengineered. Produced 3 trades a week.

May 27
------
Orchestrator stopped. Too complex. Too few trades. Killed by its own design.

June 2026
---------
D1-D7 research begins. Systematic Donchian comparison. Seven variants running
the same data, same conditions. D4 wins by being the simplest.

July 2
------
D4 paper trader goes live. First trade: BUY @ $4,133. TP at $4,163. +$59.

July 7-8
--------
Major bugs discovered: trades not persisting to DB, Kelly double-cap sizing
positions to zero, favorable slippage overstating returns.

July 12
-------
Worst trade yet: SL gap loss at -1.74R (-$42). Doubt at its peak.

July 13-14
----------
5 straight SELL wins. Equity recovers. The backtest wasn't lying -
we just needed more trades.

July 18-20
----------
Hardening sprint. Truth map, stabilization, validation, analytics.
Risk bumped from 0.25% to 0.35%.

July 20
-------
API key scrub - .env purged from git history. Keys rotated. SSH hardened.
Pre-commit hook installed. Three audit gaps closed.

Current Phase
-------------
Evidence collection. D4 at 0.35%. Waiting for 50 trades.
```

---

## Before AURUM - The Research Phase (Pre-June 2026)

Before there was a live paper trader, there were months of research in Jupyter
notebooks and Python scripts scattered across directories. The core idea was
simple: **can a clean Donchian 20-bar breakout strategy be profitable on Gold
(XAU/USD) at M15?**

The research tested:
- Different exits (1R, 2R, adaptive ATR, chandelier)
- Filters (ADX, volume, session, volatility)
- ML models (direction predictor, regime classifier, ensemble)
- Multi-asset experiments (forex pairs, indices)
- AI co-pilot concepts

Most of these ideas died in the research phase. Some because they didn't work,
others because they overfit, and a few because we realized simpler was better.

### Key Dead End: The ML Pipeline

We built a full ML pipeline - direction predictor, regime classifier, sentiment
scorer, ensemble voting, weekly retraining. It ran for weeks. The result? The
ML ensemble added virtually nothing over the simple rule-based system. The
`FULL_ENSEMBLE` mode never beat `RULE_ONLY` by a meaningful margin, and it
introduced complexity that made the system fragile.

**Lesson learned**: If a simple 20-bar channel competes with a gradient-boosted
ensemble, complexity is a liability, not a feature.

---

## The Main Orchestrator Era (May 2026)

The first live system was `aurum1.orchestrator` - a full pipeline that wired
data ingestion, feature engineering, ML models, the pullback-breakout state
machine, risk management, and execution into one service.

**It ran for about 3 weeks.** Then we stopped it on May 27.

Why? The state machine (`SCANNING → ARMED → WINDOW_OPEN → TRADE`) was elegant
but produced too few trades. High win rate, but low frequency. The pullback
requirement (1-4 bearish candles) meant most breakouts were missed. The system
was overthinking.

**Lesson learned**: An elegant state machine that produces 3 trades a week is
less useful than a brute-force breakout system that produces 50.

---

## The Donchian Shadow Project - Finding D4 (June 2026)

We pivoted to a systematic comparison of Donchian variants. Seven strategies
(D1-D7) shadowing the same market data simultaneously:

| Variant | Exit | Directions | Filters | 11-Year PF | Verdict |
|---------|------|-----------|---------|-----------|---------|
| D1 | 1R | BUY only | Vol + Session | - | Weak (36 trades) |
| D2 | 1R | BUY only | Vol + Session | 1.03 | Marginal |
| D3 | 1R | BUY+SELL | Vol + Session | 1.02 | Marginal |
| **D4** 🏆 | **2R** | **BUY+SELL** | **None** | **1.14** | **Winner** |
| D5 | Adaptive ATR | BUY+SELL | Vol imbalance | - | Research only |
| D6 | 2R | BUY+SELL | ML ensemble | 1.14 | Tied with D4 |
| D7 | Next-gen | BUY+SELL | - | - | Not tested |

### The D4 Insight

D4 won because it's the simplest configuration - and that's exactly why it
works. The 2R exit compensates for a ~37% win rate. Added SELL direction
added +$25,522 over 11 years compared to BUY-only. No filters meant it never
missed a good trade to avoid a bad one.

D6 matched D4's PF (1.14) but with ML dependencies that would break in
production. D4 needed nothing but price data.

**Lesson learned**: When the simplest strategy beats every filtered variant
over 11 years, stop adding filters.

---

## The D4 Paper Trader Goes Live (July 2, 2026)

First trade: BUY @ $4,133. TP hit at $4,163. +$59. The system worked.

### The First Week - Doubt

The first 10 trades were rocky:
- July 7: 4 trades, 3 wins, +$143 - feeling good
- July 8: 4 trades, 2 wins, +$36 - ok
- July 9-10: 4 trades, 1 win, -$96 - doubt creeping in
- July 12: SL gap loss (-1.74R, -$42) - worst trade yet

At this point we had 14 trades, net +$47. Was the 11-year backtest lying to us?

### The Recovery (July 13-14)

Then the market shifted. 5 straight SELL wins over 2 days:
- Jul 13: SELL @ $4,069 → TP @ $4,027 (+$42)
- Jul 13: SELL @ $4,036 → TP @ $3,991 (+$45)
- Jul 14: BUY @ $4,002 → TP @ $4,037 (+$35)
- Jul 14: BUY @ $4,030 → TP @ $4,077 (+$46)

Equity went from $10,350 → $10,449. The backtest wasn't lying - D4 needed
~15 trades before the distribution started matching expectations.

**Lesson learned**: 27 trades is nothing. The first 10-15 trades can be
misleading in either direction. Trust the process, not the short-term noise.

---

## The Bugs We Killed (July 7-8)

### Bug 1: Trades Not Persisting to DB

For the first week, trades were executing in-memory but **never saved to the
SQLite database**. The `_persist_trade()` method existed but wasn't being called
consistently. We only noticed when the dashboard showed 0 trades.

**Impact**: If the server restarted during those first 7 days, all trade
history and equity tracking would have been lost.

**Fix**: Ensured every trade close path calls `_persist_trade()`.

### Bug 2: Slippage Was Favorable (Overstating Returns)

The slippage model used a Gaussian distribution centered at zero. For market
orders at breakout levels, this allowed "favorable slippage" - price
improvement that doesn't happen in reality. A market buy at breakout always
hits the ask, never the bid.

**Impact**: Backtests and paper trading were likely overstating returns by
a small but systematic margin.

**Fix**: Changed to folded-normal (absolute of Gaussian) - slippage is always
adverse for market orders. Documented in `broker.py:370`.

### Bug 3: Kelly Double-Cap Sizing Positions to Zero

The Kelly calculator had `kelly_cap` AND `kelly_max_fraction` - two caps
applied in sequence. The result was near-zero position sizes despite the
strategy showing positive edge.

**Impact**: If we'd switched to Kelly-based sizing, positions would have been
microscopic regardless of edge.

**Fix**: Removed the double cap. Kelly now uses a single cap
(`kelly_max_fraction`).

### Bug 4: SL/TP Not Rebased Around Fill Price

When entry slippage shifted the fill price, the SL and TP distances were
calculated from the intended entry price, not the actual fill. This meant
actual R-multiple differed from intended 2R.

**Impact**: A BUY with 2-pip adverse slippage would have asymmetric SL/TP
distances.

**Fix**: SL/TP now rebased around actual fill so risk distance is preserved.

---

## The Mistakes We Made

Some mistakes were technical:

- **Trusting ML too early.** We spent weeks building a machine learning pipeline
  that added $2.91 in PnL over 11 years. The RegimeClassifier, DirectionPredictor,
  and SentimentScorer were elegant solutions to a problem that didn't exist.
- **Overengineering the orchestrator.** The state machine with its four-phase
  entry system (SCANNING → ARMED → WINDOW_OPEN → TRADE) was beautiful. It also
  missed most breakouts. We confused complexity with sophistication.
- **Ignoring test coverage.** For weeks the system ran with zero tests on
  critical paths. Every bug we found would have been caught by a single test.
- **Keeping dead code.** The repo was full of archived experiments, orphaned
  modules, and systemd templates pointing to deleted scripts. The repo wasn't
  telling the truth about what ran.
- **API keys in git history.** The .env file was committed in the initial push
  and propagated through 9+ commits. It took a full git-filter-repo scrub,
  key rotation, and a pre-commit hook to fix. This should never have happened.

Others were psychological:

- **Drawing conclusions from 10 trades.** I almost abandoned D4 during the
  first week because 14 trades showed +$47 and I panicked. The strategy wasn't
  broken - my patience was.
- **Wanting complexity to be valuable.** I wanted the ML to work. I wanted the
  orchestrator to be the right answer. Letting go of code I'd invested weeks in
  was harder than writing it.
- **Assuming more features meant more edge.** Every filter we tested removed
  good trades alongside bad ones. D4's edge isn't its complexity - it's the
  asymmetric 2R payout that turns a 37% win rate into profit.

**The biggest lesson wasn't about trading. It was learning how often I was wrong.**

---

## Learning to Trust the Data

One of the hardest parts wasn't building the system.

It was trusting it.

There were days where four losses made the strategy look broken. There were
days where five wins made it feel invincible. Both were equally dangerous.

The hardest lesson wasn't learning statistics. It was learning patience.
27 trades feels like forever when you're watching them happen live.
The market doesn't care about your timeline.

I'd check the dashboard multiple times a day. I'd refresh the equity curve
hoping it changed. I'd catch myself thinking "maybe if I add just one filter..."
- the exact impulse the whole hardening process was designed to resist.

Building the system was the easy part. Trusting it to do its job without
interference was harder. And I'm still learning.

---

## Hardening v1.0 - The Cleanup Sprint (July 18-20)

After D4 accumulated 27 trades with net +$317, we paused new development to
harden the system. The goal was to stabilize before bumping risk.

### Phase 0: The Truth Map

We did a forensic scan of the entire repository. What we found was ugly:
- **6 broken imports** from a script reorganization that left `from scripts.donchian_research_runner` pointing nowhere
- **No `__init__.py`** in any `scripts/` subdirectory - imports relied on fragile sys.path hacks
- **Dead code everywhere** - 15 experiment scripts, full ML orchestrator, 14 research directories, phase audit modules, all no longer in use
- **3 bare `except: pass`** in critical paths (health file, alert webhook, PID cleanup)
- **9 stale service templates** pointing to wrong script paths
- **8 backtesting scripts** with wrong ROOT path resolution
- **~25% test coverage** on the D4 paper trader (909 lines, 237 test lines)
- **Dashboard had 0 tests**

### Phase 1: Stabilization

We fixed everything:
- All 6 imports corrected
- 8 `__init__.py` files created
- Dead code archived (experiments, orchestrator, ML models, research notes)
- Silent errors now log instead of swallowing
- Service templates updated
- Path resolutions fixed across all scripts

### Phase 2: Validation

Re-ran every validation to confirm nothing broke:
- **Walk-forward L20**: 88.9% positive windows (16/18) - same as before
- **Monte Carlo**: 0% ruin across 10,000 simulations - same as before
- **TC Stress Test**: Survives 6p spread + 2p slippage - same as before
- **Risk Sensitivity**: 0.25% = 11.9% median DD, 1.2% chance >20% DD - confirmed

### Phase 3: Analytics

Built the analysis layer that should have existed from day one:
- Trade quality scoring (MAE/MFE, session analysis, composite scores)
- Prop firm challenge simulator (FTMO, The5ers, FundingPips)
- System health dashboard (latency, spread, slippage trends)
- Experiment framework template

### Phase 4: Evidence Collection

The current phase. D4 runs at 0.35% risk. We wait for 50 trades (risk review
gate) and 100 trades (strategy review gate). The analytics layer matures
alongside it.

---

## The Risk Decision: 0.25% → 0.35%

At 0.25%, the Monte Carlo simulation showed:
- 11.9% median drawdown (over 11 years)
- 1.2% chance of exceeding 20% drawdown
- 0% ruin probability

At 0.35%, the projections are:
- 16.4% median drawdown
- 24.9% chance of exceeding 20% drawdown
- Still 0% ruin

We bumped to 0.35% on July 19 after hardening passed. This was the first
deliberate risk increase, driven by data rather than gut feel. The 0.35%
decision gives us more trade frequency and better capital efficiency, but
requires closer drawdown monitoring.

If 0.35% holds up through 50 trades, we'll consider 0.50% - where median DD
jumps to 22.8% and P(DD>20%) hits 76.7%. That's a different conversation.

---

## The Rules of AURUM

Over the course of building this, some principles emerged that I never wrote
down. Here they are:

1. **Data decides.** Not intuition. Not gut feel. Not what we want to be true.
2. **Simpler beats clever.** If a 20-bar channel competes with a gradient-boosted
   ensemble, the channel wins.
3. **No strategy earns trust without evidence.** 11-year walk-forward, Monte Carlo,
   TC stress - every number is verified.
4. **Dead code gets archived.** The repo should tell the truth about what runs.
5. **Every bug becomes documentation.** If it broke once, it'll break again.
   Write it down.
6. **Production is the final backtest.** Paper trades reveal what backtests can't -
   stale data, restart failures, real slippage patterns.
7. **Losing experiments are valuable experiments.** D2, D3, D5, D6 - none were
   profitable enough. Learning why was the point.
8. **Complexity must justify its existence.** Every filter, model, and feature
   must prove it adds more edge than it removes.
9. **Nothing is promoted without validation.** The hardening phases weren't
   optional. They were the gate.
10. **We never optimize around short-term results.** 27 trades is noise.
    100 trades is a conversation. 500 trades is evidence.

---

## What We Learned

### About the System

1. **Simpler is better.** D4 (no filters, 2R, both directions) beat every
   filtered variant. The ML ensemble added nothing.

2. **Market orders at breakout always get adverse slippage.** Folded-normal
   is the correct model. Gaussian was overstating returns.

3. **D4's edge comes from the 2R exit, not the entry signal.** The ICIR
   analysis showed weak information coefficient (-0.079), but the 2R
   asymmetric payout turns slight directional accuracy into profit.

4. **SELL direction adds ~60% more PnL.** Over 11 years, BUY-only returned
   +$17,156. BUY+SELL returned +$42,678.

### About Building Trading Systems

5. **Dead code lies.** If it's not running, archive it. The repo should tell
   the truth about what the system is.

6. **Fix silent errors.** `except: pass` hides bugs that will surface at the
   worst possible time.

7. **Test the critical paths.** 27 trades is too few to discover edge cases in
   state recovery, spread costing, or exit logic. Tests catch those.

8. **27 trades teaches you nothing about strategy performance.** It teaches
   you about execution infrastructure. The first 50 trades are for finding
   bugs, not measuring edge.

9. **Document the journey.** The sanitized final report doesn't tell the story.
   The bugs, the wrong turns, the "obvious in hindsight" insights - those are
   what matter.

### About Ourselves

10. **We kill bugs when we find them.** We don't kick the can.
11. **We test before we trust.** Walk-forward, Monte Carlo, TC stress -
    every change earns its place.
12. **We let the data decide.** The strategy hierarchy came from 11 years of
    backtest data, not opinion.

---

## The Numbers (as of July 20, 2026)

| Metric | Value |
|--------|-------|
| Total trades | 27 |
| Win rate | 51.9% |
| Net PnL | +$317.19 |
| Net R | +14.22 |
| Best streak | 5 wins |
| Max DD (lifetime) | ~2.5% |
| Current DD | 0.49% |
| Avg quality score | 77.4/100 |
| Risk setting | 0.35% |
| Current position | BUY @ $4,001.26 (TP: $4,045.25, SL: $3,979.27) |

But the numbers don't tell the story. The story is in the bugs we killed,
the wrong turns we reversed, and the simplicity we fought to preserve.

---

## What Comes Next

D4 isn't finished. Over the coming months:

- Reach 50 trades for the first risk review gate
- Reach 100 trades for the strategy review gate
- Expand the analytics pipeline
- Improve execution monitoring
- Stress test larger risk profiles (0.50%?)
- Determine whether D4 deserves real capital

Eventually D4 may die. If it does, that's acceptable - it means we found
something better, or the data told us the edge wasn't real.

The purpose of AURUM isn't to prove D4 works. It's to discover what actually does.

---

## Final Thoughts

The most surprising discovery wasn't finding D4.

It was discovering how often my assumptions were wrong.

I built machine learning models that couldn't outperform price. I built an
elegant state machine that traded too little. I built filters that removed
more edge than they added. I committed API keys to git history. I let 7 days
of trades live only in memory. I almost abandoned a profitable strategy because
14 trades scared me.

Again and again, the data forced me to abandon ideas I was attached to.

That may be AURUM's greatest contribution. The goal was never to prove myself
right. The goal was to discover when I was wrong - and to build a system that
could do that honestly.

D4 may not be the strategy I trade five years from now. There may not even be
a D4. What I hope survives is the process that found it.

If AURUM succeeds, it won't be because I built a profitable strategy.
It will be because I built a system that can honestly discover one.
