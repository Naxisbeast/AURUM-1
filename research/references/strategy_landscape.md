# Strategy & Math Landscape — What's Worth Building Next

**Date**: 2026-08-16
**Status**: Deep-research review (partial — synthesis step failed on API error, but 16 claims were adversarially verified; reconstructed here)
**Purpose**: Landscape map for the AURUM research lab — beyond D4 signal-discovery, what does the verified evidence say is worth building?

---

## Context

AURUM's lab has thoroughly mined **signal discovery**: D1-D7 (Donchian variants), ML ensemble (D6), regime classifiers, news/sentiment, pullback-breakout state machines, adaptive ATR exits, multi-asset shotgun — all rejected for adding no edge over simple price-only D4. The D-series is a graveyard of *complexity that didn't justify itself*.

This document maps the **unexplored territory** where the peer-reviewed evidence is strong, with adversarial verification. It's organized by the three axes the lab asked about.

---

## AXIS 1 — Math-grounded strategy improvements (signal-agnostic)

These improve ANY strategy regardless of signal. **Highest-leverage, lowest-risk builds.**

### 1.1 Volatility targeting — ✅ STRONG, with a critical nuance
**Verified findings (CFA Institute, Financial Analysts Journal 2020):**
- ❌ **Naive/conventional vol-targeting does NOT reliably improve risk-adjusted returns** — it generates high turnover and "has failed to consistently enhance risk-adjusted equity performance."
- ✅ **CONDITIONAL vol-targeting DOES**: adjust exposure only in extreme high/low-vol periods (reduce when vol high, increase when vol low). Consistently enhances Sharpe, reduces drawdowns and tail risk, with LOWER turnover and leverage than conventional.
- 🏆 **For momentum specifically** (closest analogue to D4's trend-following): conditional vol-targeting **more than doubled the Sharpe ratio and cut max drawdown from 54.1% → 20.1%.**
- ⚠️ **One paper (SSRN 4773781) argues the apparent vol-targeting alpha is really an implicit trend-loading** — controlling for trend makes the alpha accrue to trend. This is NOT a refutation for AURUM (D4 IS trend-following); it means vol-targeting on top of a trend strategy is arguably *reinforcing* the real edge.

**AURUM verdict**: HIGH VALUE. A conditional vol-targeting overlay on D4 (or any future strategy) is directly testable with the existing backtest engine. The momentum result is compelling and it's signal-agnostic. **This is my #1 recommendation.**

### 1.2 Stop-loss rules — ✅ STRONG, more powerful than expected
**Verified (Han & Zhou, "Taming Momentum Crashes: A Simple Stop-Loss", Semantics Scholar; and Journal of Futures Markets commodity study):**
- A **simple 10% stop-loss** on momentum portfolios cut worst monthly loss from −49.79% → −11.36% (EW) and **more than doubled Sharpe** (0.165 → 0.369) — it raises average return AND lowers volatility, not just caps downside.
- At the **individual-commodity level**, stop-losses improve long-short commodity factor premia by persistently reducing drawdown frequency/severity.
- **Dynamically calibrating the stop threshold to realized volatility materially enhances efficacy** (2-1 vote).
- ⚠️ Note for AURUM: D4 already uses a fixed 2R/ATR stop — the finding here is that *additional* stop/exit layers (and vol-calibrated stops) can add risk-adjusted value on top of a factor signal.

**AURUM verdict**: MEDIUM-HIGH. A **vol-calibrated stop** variant of D4 (test whether calibrating the exit threshold to realized vol beats fixed 2R) is a clean research variant.

### 1.3 Kelly / fractional Kelly — ✅ WEAK at AURUM's trade count
**Verified**: Full Kelly maximizes long-run log growth only over tens of thousands of trades. At realistic horizons (100-1,000 trades), fractional Kelly barely differs — Kelly adds little edge for a strategy with AURUM's sample size.
**AURUM verdict**: LOW VALUE now. The lab already does fractional-Kelly-style conservative sizing (0.35%). Not worth a research thread at ~100-200 trades.

### 1.4 Portfolio allocation across uncorrelated edges — ✅ STRONG (the strategic build)
[Verification of the specific "combining modest uncorrelated edges reliably improves risk-adjusted returns" claim failed on API error, but this is among the most-established results in quantitative finance — mean-variance / risk-parity combination of low-correlation strategies reliably improves Sharpe. It's the standard institutional practice.]

**AURUM verdict**: HIGH VALUE but DEPENDS on having a second strategy. This is the real answer to gate criterion 4 and the "build a portfolio" goal. The payoff is the compounding of uncorrelated edges.

---

## AXIS 2 — Other strategy families

### 2.1 Mean reversion
- Robust, well-documented family, but **needs its own instrument/timeframe** — can't ride on gold M15's trending nature.
- Overfit-prone if curve-fit; robust versions are simple (Bollinger/RSI extremes) and short-holding.
- **AURUM verdict**: MEDIUM. Would need a new instrument (or accept it trades *against* the trend signal on the same asset). Testable but a real build.

### 2.2 Carry strategies
- Strongest documented across FX and futures (currency carry is one of the most replicated anomalies).
- **Needs multi-asset/futures data** — not available on gold M15 alone.
- **AURUM verdict**: MEDIUM-HIGH conceptually, but **data-gated** — requires a futures/FX term-structure feed AURUM doesn't have.

### 2.3 Statistical arbitrage / pairs
- Well-documented but requires correlated assets + significant infra (cointegration machinery, execution).
- **AURUM verdict**: LOW near-term — high build cost, execution-sensitive, overfit-prone.

### 2.4 Seasonality
- Documented but weak, unstable, and often curve-fit in the literature.
- **AURUM verdict**: LOW — historically fragile.

### 2.5 Momentum beyond simple breakout
- Momentum is the most replicated anomaly in finance. D4 already captures a slice (breakout = momentum entry). 
- **The literature shows conditional vol-targeting + stop-losses are the momentum *improvements*** — which argues AGAINST a new momentum-signal search and FOR the Axis-1 overlay on the existing momentum edge.
- **AURUM verdict**: The edge is already captured; the improvements are Axis 1.

---

## AXIS 3 — Other edge sources (beyond news)

- **Cross-asset momentum** (equity→gold, USD→gold): documented; needs multi-asset data.
- **Term structure** (contango/backwardation): strong for futures, but gold futures contango is thin — limited gold signal; needs futures data.
- **Volatility/options signals**: documented but execution-heavy; AURUM is spot-only.
- **Order-flow/liquidity proxies**: high-effort, needs microstructure data.
- **Macro event effects**: the frequency-appropriate, defensible design (event-study around CPI/FOMC/NFP) — but the news review found this is weakest for gold and the verified literature is mixed.

**AURUM verdict on Axis 3**: All are **data-gated** (need new feeds) or **execution-heavy**. Lower leverage than Axis 1 given AURUM's infra.

---

## Practical synthesis — highest-leverage next builds

Given AURUM's infra (backtest engine, walk-forward + trial ledger + DSR gate, shadow services, gold M15 OANDA data):

### 🥇 Thread 1: Conditional volatility-targeting overlay on D4 (Axis 1)
- **Why**: Strongest verified evidence (momentum Sharpe ×2, max DD 54%→20%), signal-agnostic, testable now with existing engine.
- **First experiment**: Backtest D4 with a conditional vol-targeting exposure adjustment (reduce size in extreme-high-vol, increase in extreme-low-vol). Walk-forward it, log to trial ledger, compare Sharpe/DD vs D4 baseline.
- **Risk**: low. Doesn't change signal, only size.

### 🥈 Thread 2: Vol-calibrated stop-loss variant of D4 (Axis 1)
- **Why**: Verified that dynamic/vol-calibrated stops enhance factor premia; D4 uses fixed 2R.
- **First experiment**: D4 variant where the stop threshold scales with realized volatility (e.g., ATR multiplier varies with regime). Walk-forward, log, compare vs fixed 2R.
- **Risk**: low-medium. D5 tested adaptive *ATR exits* and failed, but this is a *stop* not an exit — and the literature specifically supports vol-calibrated stops. Worth distinguishing from D5's prior negative.

### 🥉 Thread 3: A second, uncorrelated strategy (Axis 1 payoff + gate criterion 4)
- **Why**: The compounding payoff; real answer to "build a portfolio."
- **First experiment**: Before committing to a new signal, use the existing infra to (a) pick a candidate with documented low correlation to D4 (e.g., a short-holding mean-reversion on gold, or a carry-style on futures if data is added), (b) backtest + walk-forward, (c) measure correlation to D4. Only if correlation is low is it worth running.
- **Risk**: medium. Needs a new signal + possibly new data.

### ⚠️ What I'd explicitly NOT prioritize (my pushback)
- **Another momentum-signal search** — D4 already captures it; the edge is the signal, the gains are Axis 1.
- **Full Kelly sizing** — weak at AURUM's trade count.
- **Any Axis-3 edge requiring new data** (carry, term-structure, cross-asset) before Axis-1 is mined — those are bigger builds with the same overfit risk as the rejected complexity.
- **The Sharpe-5 FX paper** — verified unreviewed/implausible.

---

## Honest caveats
- The synthesis step of the research workflow failed (API balance), so this is a *reconstructed* synthesis from 16 verified claims — the individual claims are verified, the ranking is mine.
- The vol-targeting evidence is on **equity indices/factors**, not gold M15 — it's transferable-by-mechanism, not proven on gold. The first experiment must verify on AURUM's own data.
- The "combining uncorrelated edges" claim's specific verification failed; it's treated as well-established-institutional-knowledge, not freshly verified here.
