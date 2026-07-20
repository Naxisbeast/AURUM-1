# AURUM-1: The Journey — A Trading System's Evolution

**Last updated**: 2026-07-20

This isn't a sanitized status report. This is the real story — the decisions, the
dead ends, the fixes, and what we learned along the way. The final numbers never
tell the whole story.

---

## Before AURUM — The Research Phase (Pre-June 2026)

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

We built a full ML pipeline — direction predictor, regime classifier, sentiment
scorer, ensemble voting, weekly retraining. It ran for weeks. The result? The
ML ensemble added virtually nothing over the simple rule-based system. The
`FULL_ENSEMBLE` mode never beat `RULE_ONLY` by a meaningful margin, and it
introduced complexity that made the system fragile.

**Lesson learned**: If a simple 20-bar channel competes with a gradient-boosted
ensemble, complexity is a liability, not a feature.

---

## The Main Orchestrator Era (May 2026)

The first live system was `aurum1.orchestrator` — a full pipeline that wired
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

## The Donchian Shadow Project — Finding D4 (June 2026)

We pivoted to a systematic comparison of Donchian variants. Seven strategies
(D1-D7) shadowing the same market data simultaneously:

| Variant | Exit | Directions | Filters | 11-Year PF | Verdict |
|---------|------|-----------|---------|-----------|---------|
| D1 | 1R | BUY only | Vol + Session | — | Weak (36 trades) |
| D2 | 1R | BUY only | Vol + Session | 1.03 | Marginal |
| D3 | 1R | BUY+SELL | Vol + Session | 1.02 | Marginal |
| **D4** 🏆 | **2R** | **BUY+SELL** | **None** | **1.14** | **Winner** |
| D5 | Adaptive ATR | BUY+SELL | Vol imbalance | — | Research only |
| D6 | 2R | BUY+SELL | ML ensemble | 1.14 | Tied with D4 |
| D7 | Next-gen | BUY+SELL | — | — | Not tested |

### The D4 Insight

D4 won because it's the simplest configuration — and that's exactly why it
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

### The First Week — Doubt

The first 10 trades were rocky:
- July 7: 4 trades, 3 wins, +$143 — feeling good
- July 8: 4 trades, 2 wins, +$36 — ok
- July 9-10: 4 trades, 1 win, -$96 — doubt creeping in
- July 12: SL gap loss (-1.74R, -$42) — worst trade yet

At this point we had 14 trades, net +$47. Was the 11-year backtest lying to us?

### The Recovery (July 13-14)

Then the market shifted. 5 straight SELL wins over 2 days:
- Jul 13: SELL @ $4,069 → TP @ $4,027 (+$42)
- Jul 13: SELL @ $4,036 → TP @ $3,991 (+$45)
- Jul 14: BUY @ $4,002 → TP @ $4,037 (+$35)
- Jul 14: BUY @ $4,030 → TP @ $4,077 (+$46)

Equity went from $10,350 → $10,449. The backtest wasn't lying — D4 needed
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
orders at breakout levels, this allowed "favorable slippage" — price
improvement that doesn't happen in reality. A market buy at breakout always
hits the ask, never the bid.

**Impact**: Backtests and paper trading were likely overstating returns by
a small but systematic margin.

**Fix**: Changed to folded-normal (absolute of Gaussian) — slippage is always
adverse for market orders. Documented in `broker.py:370`.

### Bug 3: Kelly Double-Cap Sizing Positions to Zero

The Kelly calculator had `kelly_cap` AND `kelly_max_fraction` — two caps
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

## Hardening v1.0 — The Cleanup Sprint (July 18-20)

After D4 accumulated 27 trades with net +$317, we paused new development to
harden the system. The goal was to stabilize before bumping risk.

### Phase 0: The Truth Map

We did a forensic scan of the entire repository. What we found was ugly:
- **6 broken imports** from a script reorganization that left `from scripts.donchian_research_runner` pointing nowhere
- **No `__init__.py`** in any `scripts/` subdirectory — imports relied on fragile sys.path hacks
- **Dead code everywhere** — 15 experiment scripts, full ML orchestrator, 14 research directories, phase audit modules, all no longer in use
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
- **Walk-forward L20**: 88.9% positive windows (16/18) — same as before
- **Monte Carlo**: 0% ruin across 10,000 simulations — same as before
- **TC Stress Test**: Survives 6p spread + 2p slippage — same as before
- **Risk Sensitivity**: 0.25% = 11.9% median DD, 1.2% chance >20% DD — confirmed

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

If 0.35% holds up through 50 trades, we'll consider 0.50% — where median DD
jumps to 22.8% and P(DD>20%) hits 76.7%. That's a different conversation.

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
   The bugs, the wrong turns, the "obvious in hindsight" insights — those are
   what matter.

### About Ourselves

10. **We kill bugs when we find them.** We don't kick the can.
11. **We test before we trust.** Walk-forward, Monte Carlo, TC stress —
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
