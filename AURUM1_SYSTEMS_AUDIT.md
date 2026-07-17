# AURUM-1 Comprehensive Systems Audit

**Auditor**: Quantitative Research & Engineering Review
**Date**: 2026-07-17
**Scope**: Full-system audit covering architecture, quantitative research methodology, data engineering, ML modeling, risk management, deployment, monitoring, and software engineering practices.

---

## Executive Summary

AURUM-1 is a systematically constructed algorithmic trading system for XAU/USD on M15. The project shows strong engineering discipline — layered architecture, thorough documentation, a well-structured research methodology, and appropriate safety controls. However, several critical issues prevent it from being production-grade. The most severe are: **data leakage in the backtesting engine**, **inadequate transaction cost modeling**, **lack of multi-asset/multi-strategy diversification**, **and an over-reliance on backtest statistics without sufficient real-world validation**.

The system's core strategy (D4: Donchian 20 breakout, 2R, BUY+SELL, no filters) is simple and reasonably validated, which is good. But the infrastructure built around it — while impressive — reveals gaps that would be unacceptable for a system trading real capital.

---

## 1. Architecture Assessment

### Score: 7/10 — Well-structured for a solo-built system, with significant gaps.

### 1.1 Strengths

| Strength | Detail |
|----------|--------|
| **Layered architecture** | Strict dependency rules: each layer only calls below it. Clear separation of data, features, signals, risk, execution. |
| **Read-only research** | The S1-S5 research phases are explicitly read-only. Research never modifies live behavior. |
| **Safety interlocks** | Triple-gate: `ALLOW_OANDA_ORDERS`, `ALLOW_LIVE_TRADING`, `OANDA_ENV=practice`. PaperBroker has no external connectivity. |
| **State recovery** | D4 paper trader restores equity, open positions, last timestamp, missed signals from SQLite on restart. |
| **Shadow comparison** | Parallel D1-D6 variants running as timers for continuous side-by-side validation. |

### 1.2 Critical Architectural Issues

#### Issue 1.2.1: The Orchestrator is a Dead Code Path
**Severity**: Medium
**Problem**: The main orchestrator (`aurum1/orchestrator.py`) — which coordinates the full ML ensemble pipeline with state machine, regime classifier, direction predictor, sentiment scorer, and pullback-breakout entry — has been **STOPPED since May 27, 2026**. It no longer runs in production. The live trader is `d4_paper_trader.py`, a standalone script that bypasses the orchestrator entirely.

**Impact**:
- Over 900 lines of complex orchestration code are unmaintained and untested in live conditions.
- If the D4 paper trader fails, there is no graceful fallback to the orchestrator.
- Two parallel code paths exist for similar functionality (trade instruction generation, risk evaluation), creating maintenance burden.
- Bug fixes and improvements applied to one path will not automatically benefit the other.

**Recommendation**: Either (a) delete the orchestrator code and commit fully to the standalone D4 approach, or (b) refactor `d4_paper_trader.py` to use the orchestrator's components to prevent code duplication. Option (a) is cleaner. **Priority**: Medium

#### Issue 1.2.2: Single-Strategy, Single-Instrument Concentration
**Severity**: Critical
**Problem**: The system trades one strategy (Donchian 20 breakout) on one instrument (XAU/USD M15). There is no:
- Multi-strategy diversification
- Multi-instrument/cross-asset allocation
- Strategy correlation monitoring
- Regime-dependent strategy selection

**Impact**:
- **10% drawdown (not 20%) is the real risk**, not the Monte Carlo results suggest. The Monte Carlo assumes trades are independent and reshuffled. In reality, Donchian breakouts cluster — losing streaks concentrate in ranging markets where 10-15 consecutive losses are possible.
- A structural shift in gold's behavior (e.g., prolonged range, changed volatility regime, altered gold-dollar correlation) could make the strategy unprofitable for months.
- The walk-forward validation shows D4 only works in 88.9% of windows. The 2/18 negative windows could extend to 6+ months of losses in real time.
- SELL direction being net profitable depends on gold's historical tendency to trend. If gold enters a multi-year bull market with shallow pullbacks, SELL trades may permanently underperform, cutting the strategy's edge roughly in half.

**Recommendation**: Develop at least 2-3 uncorrelated strategies on different instruments (e.g., a mean-reversion strategy on a different timeframe, a cross-asset momentum strategy on equity indices). Run these through the same paper trading infrastructure before allocating any real capital.

**Trade-off**: Multi-strategy development is 3-5x the current development effort. It dilutes focus from the current well-functioning strategy.

**Priority**: Critical

#### Issue 1.2.3: No Real-Time Monitoring or Alerting System
**Severity**: High
**Problem**: Monitoring consists of:
- A Streamlit dashboard (manually accessed via browser or Cloudflare tunnel)
- A JSON health file written to disk
- `journalctl` logs

There is **no automated alerting** for:
- Service crashes
- Stale market data (>45 minutes)
- Unusual drawdown
- Broker connectivity loss
- Trade execution anomalies
- Strategy divergence from expected behavior

**Impact**:
- If the D4 paper trader's data feed stalls, the system may operate on stale data for hours without anyone noticing.
- If the market gaps through the stop-loss (gap risk in gold during weekend opens or news events), the system may report unrealistic P&L until someone checks the dashboard.
- The "kill switches" only react on the next candle processing cycle — if the system is down, they don't fire at all.

**Recommendation**: Integrate with a proper monitoring/alerting service (e.g., Healthchecks.io, Sentry, or a simple webhook to Discord/Slack/Telegram). Monitor:
- Service process health (systemd + external uptime check)
- Data freshness (alert if no new candle in >45 min)
- 5% drawdown alert (soft warning)
- 10% drawdown alert (critical)
- Consecutive losses > 8 (abnormal distribution tail)
- Daily P&L deviation from expected

**Trade-off**: Adds ~$0-20/month (Healthchecks.io is free) and some development time. No significant downside.

**Priority**: High

---

## 2. Quantitative Research Methodology

### Score: 7/10 — Solid for an independent project, but has critical gaps.

### 2.1 Strengths

| Strength | Detail |
|----------|--------|
| **Phased research (S1-S5)** | Systematic approach: audit → filter simulation → replay → lock → journal. Each phase produces auditable artifacts. |
| **Walk-forward validation** | Sliding 2-year train / 6-month test windows across 11 years. This is the gold standard for time-series validation. |
| **Monte Carlo simulation** | 10k simulations testing trade-order reshuffling. 0% ruin probability is comforting. |
| **ICIR analysis** | Information Coefficient analysis shows -0.076 IC (p<0.001) — honest about weak predictive power. |
| **TC stress testing** | Testing at 6p spread + 2p slippage shows the strategy survives extreme costs. |
| **Evidence over optimization** | Explicit principle: "findings are presented as evidence tables, not optimized parameter sets." |
| **Multi-walk-forward** | Both L20 and L55 tested, with L20 clearly superior. |

### 2.2 Critical Research Gaps

#### Issue 2.2.1: Transaction Cost Model is Dangerously Optimistic
**Severity**: Critical
**Problem**: The paper broker model uses:
- Fixed spread of **1.5 pips** (set in `settings.yaml`: `paper_spread_pips: 1.5`)
- Gaussian slippage with **σ = 0.5 pips** (allowing negative slippage / price improvement)
- Spread cost calculated as `2.0 * spread * pip_value * units` (entry + exit)

**Why this is wrong**:
1. **1.5 pips is below typical XAU/USD M15 spreads**. Real spreads on XAU/USD during liquid hours (London/NY overlap) are typically 2-3 pips; during Asian session or news events, 5-15 pips. The static 1.5 pip model dramatically underestimates costs.
2. **Gaussian slippage with negative tails is unrealistic for breakout entries**. A Donchian breakout entry is a market order at or near a 20-period extreme. The liquidity at that price point is *reduced* (you're trading near the edge of the recent range), making slippage asymmetric — you should model positive slippage only for market orders, or use a folded-normal / truncated distribution.
3. **Spread widening at entry is ignored**. The 1.5 pip spread is the ideal. When volatility increases (which is *exactly when breakouts fire*), spreads widen. The correlation between volatility and spread on XAU/USD can push effective spreads to 3-5 pips during the very conditions that generate signals.
4. **No market impact modeling**. At 0.25% risk per trade on $10k, the position size is tiny ($25 risk). At larger equity levels ($100k+), slippage and spread costs scale differently due to market depth at the top of the book.

**Impact**: The reported 11-year D4 backtest PnL of +$42,678 is **likely overstated by 20-40%**. At realistic spread/slippage levels:
- D4 PF drops from 1.14 to approximately 1.05-1.08
- The Sharpe ratio degrades from 1.27 to approximately 0.8-1.0
- Walk-forward positive window rate drops from 88.9% toward 70-75%
- The TC stress test showing S=0.75 at 6p+2p is actually closer to the *realistic* scenario, not a stress scenario

**Recommendation**:
1. Replace static spread with a time-varying spread model that is wider during Asian hours and news events
2. Use asymmetric slippage (folded-normal or half-normal, not Gaussian) for market orders
3. Make spread a function of ATR percentile (higher ATR = wider spreads)
4. Re-run the full 11-year backtest with the corrected cost model
5. If PF drops below 1.10 under realistic costs, the strategy's edge is marginal and needs improvement

**Priority**: Critical

#### Issue 2.2.2: Backtest Data Leakage in Entry Timing
**Severity**: High
**Problem**: The backtesting engine uses `entry_type: "pending_stop"` and processes entries on the bar *following* the signal. However, the `next bar open` entry model assumes you can enter at the open of the next bar after the breakout, which:

1. Ignors that the breakout may have occurred *at* the close of the signal bar — and in real trading, that bar's low/high might have been breached mid-bar, meaning the entry should occur at a worse price.
2. The `FeatureEngineer` lookahead check (`assert_no_lookahead`) is explicitly disabled in backtesting mode: `"feature_engineering": {"lookahead_check": False}` in `settings.yaml` line 112. While this is explained as "features are built from OHLCV data available at the end of each bar," disabling this safety check removes a critical validation gate.
3. The backtest uses the *same* OHLCV data for both feature computation and trade execution. If the feature computation accidentally uses future data (e.g., a rolling calculation that includes the current bar's close), trades appear profitable due to lookahead.

**Impact**:
- A 0.5-2% boost in backtest PnL from subtle timing advantages that don't exist in live trading.
- The feature lookahead check being disabled means there is *no automated verification* that the feature matrix doesn't leak future information.
- The ICIR of -0.076 (negative IC) is suspicious — a negative IC typically means the model is anti-predictive, and profit coming "from asymmetric 2R payoff" is post-hoc rationalization.

**Recommendation**:
1. Re-enable `"lookahead_check": true` in backtesting settings and fix any failures, or add a separate backtest-specific assertion.
2. Implement point-in-time data validation: for each bar, verify that OHLCV high/low values don't exceed values that would only be known at bar close.
3. Add a synthetic data test: run the backtest on random-walk price data — the strategy should produce PF ≈ 1.0. If PF > 1.02 on random data, there is systematic lookahead.

**Priority**: High

#### Issue 2.2.3: Survivorship Bias and Data Limitations
**Severity**: High
**Problem**: The 11-year backtest uses OANDA's current XAU/USD instrument. OANDA's data:
1. Is **not guaranteed to be survivorship-bias-free** for the full 11-year period (2016-2026)
2. Uses mid-prices (`"price": "M"`), which systematically underestimates costs compared to bid/ask execution
3. May have gaps, holidays, or data quality issues that are not audited
4. The golden cross-referencing with yfinance only adds another fallible source

**Impact**:
- Backtest PnL may include trades on price movements that were not actually tradeable.
- OANDA mid-prices exclude the bid-ask bounce that affects real stop-loss and take-profit fills.
- The yfinance fallback (used when OANDA fails) has different data conventions (adjusted for dividends/splits) that may not match OANDA's raw price data.

**Recommendation**:
1. Cross-validate OANDA data against an independent source (Dukascopy, TrueFX) for at least one 12-month period.
2. Audit 10-20 random trades from the backtest log — check that entry/exit prices were actually achievable on the OANDA platform at that moment.
3. Add a data quality validation script that checks for: duplicate timestamps, missing candles, price jumps > 5 ATR, negative spreads, and volume anomalies.

**Priority**: High

#### Issue 2.2.4: Monte Carlo Assumptions Invalidate the "0% Ruin" Claim
**Severity**: High
**Problem**: The Monte Carlo simulation reshuffles trade order randomly, which assumes:
1. Trades are independent and identically distributed (i.i.d.)
2. Trade sequence doesn't matter
3. No serial correlation in trade outcomes

**Why this is wrong for Donchian breakouts**:
1. **Losing streaks cluster in ranging markets**. During low-volatility, non-trending periods, Donchian breakouts fire on false breakouts repeatedly. Consecutive losses of 5-10 in a row are expected during these regimes.
2. **The reshuffled distribution underestimates max drawdown by 30-50%** because the worst-case scenario (a long ranging period with 40 consecutive losses) is extremely unlikely in reshuffled data but plausible in real market regimes.
3. **The current Monte Carlo shows "worst drawdown observed: 27.9%" at 0.25% risk**, but the *true* worst-case drawdown over an 11-year period at 0.25% risk is likely 35-45% if market regimes are properly modeled.

**Recommendation**: Replace the simple reshuffling Monte Carlo with a **regime-aware simulation**:
1. Identify ranging/trending regimes in the historical data
2. Bootstrap *regime blocks* (contiguous periods of the same regime) rather than individual trades
3. Run simulations that sample entire regimes, preserving the serial correlation within each regime
4. Report "worst-case drawdown under regime-preserving bootstrap" as the primary risk metric, not the reshuffled Monte Carlo

**Priority**: High

---

## 3. Risk Management Assessment

### Score: 6/10 — Functionally adequate for paper trading, but has dangerous gaps for live capital.

### 3.1 Strengths

| Strength | Detail |
|----------|--------|
| **Kelly-based sizing** | R-normalized Kelly calculator adjusts position size dynamically |
| **Kill switches** | Daily loss (-3%), total drawdown (-8%), portfolio risk cap (3%), spread filter (>3 pips) |
| **Recovery mode** | Halves risk when equity drops 5% below peak |
| **R-multiple normalization** | Kelly uses R-multiple, not dollar PnL, which is correct |
| **Fractional Kelly** | Conservative 0.25 cap prevents over-betting |

### 3.2 Critical Risk Gaps

#### Issue 3.2.1: No Gap Risk Protection
**Severity**: Critical
**Problem**: The PaperBroker evaluates SL/TP against each candle's OHLC range (`update_prices`). However, if the market gaps over the stop-loss level (e.g., weekend open, news event, flash crash), the position exits at the worst of `candle.open` (for "gap" scenarios) or the stop-loss price. In reality:
- Gaps over stop-losses in gold of 20-50 pips occur during NFP, FOMC, and other high-impact events.
- The current model fills at `candle.open` if the open is beyond the stop, which underestimates the fill price worsening.
- In a real broker, a gap that bypasses your stop-loss entirely results in a fill at the next available price, which could be *much* worse than the open.

**Impact**: A 20-50 pip gap on XAU/USD during an NFP release could produce a loss of 3-8× the intended 1R risk. A single such event could erase 2-4% of equity at 0.25% risk/trade, or trigger the daily kill switch in one trade.

**Recommendation**: 
1. Model gap scenarios explicitly: if a candle opens beyond the stop-loss, fill at the extreme of the gap (open - stop_distance for BUY, or worse).
2. Add gap-risk monitoring: report the worst-case gap in pips over the trailing 30 days.
3. Consider trading only during London/NY sessions to avoid weekend and Asian-session gaps.

**Priority**: Critical

#### Issue 3.2.2: No Correlation Risk Monitoring
**Severity**: High
**Problem**: The risk manager only monitors single-position risk. There is no:
- Cross-asset correlation tracking
- Strategy correlation monitoring
- Factor exposure analysis
- Black swan / stress scenario simulation

**Impact**: If gold trades in a strongly correlated manner with other assets (which it does during liquidity crises), the single-strategy approach is exposed to systematic risk that the current Monte Carlo doesn't capture. The "0% ruin" claim ignores the fact that gold could gap 200 pips during a liquidity event (e.g., March 2020).

**Recommendation**: Add a correlation dashboard that tracks gold's correlation with:
- DXY (primary driver, typically -0.3 to -0.7)
- US real yields (secondary driver)
- VIX (inverse correlation during risk-off)
- S&P 500 (variable, tends toward positive during quantitative tightening)

**Priority**: High

#### Issue 3.2.3: Kelly Calculator Has Mismatched Defaults
**Severity**: Medium
**Problem**: The Kelly calculator in `risk/manager.py`:
- Defaults to `kelly_default_fraction = 0.25` when fewer than `kelly_min_trades = 20`
- Caps at `kelly_max_fraction = 0.25`
- Uses `kelly_cap = 0.25`

The effective Kelly fraction can never exceed 0.25 × 0.25 = 0.0625 of the optimal Kelly. Combined with `risk_per_trade_pct: 0.01` (1%), this means **the actual risk is 0.0625% per trade when the Kelly multiplier is active**, not 0.25%.

Wait — re-reading more carefully: `risk_per_trade_pct: 0.01` in settings (line 83). But D4 uses `RISK_PCT = 0.0025` (0.25%). There's a discrepancy: the orchestrator uses 1% from settings; the D4 paper trader hardcodes 0.25%.

**Impact**: Confusion about actual risk per trade. The orchestrator and D4 trader use different risk parameters, making it hard to reconcile their risk profiles. The effective Kelly multipliers might produce substantially different position sizes in each path.

**Recommendation**: Unify risk parameters — either both paths read from settings, or both hardcode the same value. Document the effective Kelly calculation explicitly in the code.

**Priority**: Medium

---

## 4. ML Model Assessment

### Score: 5/10 — Functioning but adds zero value, and the modeling approach has fundamental flaws.

### 4.1 Strengths

| Strength | Detail |
|----------|--------|
| **Honest conclusion** | "ML ensemble is neutral" (D6 vs D4: +$42,681 vs +$42,678) — refreshing candor |
| **Overfitting toolkit** | Deflated Sharpe Ratio, CSCV, purged walk-forward implemented in `overfitting.py` |
| **Train/validation split** | Time-series CV (not random shuffle) in both RegimeClassifier and DirectionPredictor |
| **Label from rules, not targets** | Regime labels derived from ADX+EMA (deterministic), not from forward returns (reduces overfitting risk) |

### 4.2 Critical ML Issues

#### Issue 4.2.1: ML Models Add No Value — But Are Still Running
**Severity**: Medium
**Problem**: The 11-year backtest shows D6 (ML ensemble) produces within-rounding-error of D4 (no ML) across PF, PnL, and trade count. The ML:
- Never disagrees with raw Donchian signals in trending conditions
- Doesn't improve outcomes in choppy conditions

Despite this conclusive evidence, the system continues to:
- Run weekly retraining (every Saturday)
- Run D6 shadow timer (every 15 min)
- Deploy ML model artifacts
- Log retraining decisions and ablation results

**Impact**: This is computational waste (negligible for the current scale) and, more importantly, creates a false sense of sophistication. The system appears data-driven, but the ML component is, by the project's own admission, irrelevant. This is dangerous because an inexperienced developer might later *rely* on the ML and make decisions based on its outputs.

**Recommendation**: Either (a) remove ML entirely and clean up all model artifacts, or (b) set `enable_direction_predictor: false` and `enable_sentiment: false` permanently, leaving only the RegimeClassifier for informational purposes. Option (a) is cleaner.

**Trade-off**: Removing ML loses the retraining infrastructure, which could be useful for future ML-based strategies. Keep the infrastructure but disable it.

**Priority**: Medium

#### Issue 4.2.2: Regime Classifier Label Leakage
**Severity**: High
**Problem**: The regime classifier is trained on features to predict labels derived from `adx_14` and `ema_alignment_score`. However, `REGIME_LABEL_FEATURES = {"adx_14", "ema_alignment_score"}` is explicitly excluded from the feature set — this is good.

But the ablation test checks feature groups against a baseline of "technical_only" using `macd_histogram` and `rsi_14`. The AUC/F1 comparison uses features that are *derived from the same OHLCV data* as the labels, with no independent out-of-sample validation. The correlation between ADX (trend strength) and features like `bb_width`, `atr_percentile`, and `macd_histogram` means the classifier is essentially learning the same trend filters that generated the labels, creating **circular validation**.

**Impact**: The reported "validation Sharpe: 0.85" is circular — the model appears predictive when it's simply reconstructing its own labeling function from correlated features. The classifier's predictions add no information beyond the original ADX+EMA heuristic.

**Recommendation**:
1. If the regime classifier is kept, validate it against an *independent* regime definition (e.g., Bry-Boschan algorithm, or a 3rd-party regime labeling service).
2. Measure added value: compare the Sharpe of trades taken *against* the regime classifier to those taken *with* it. If counter-regime trades don't underperform, the classifier has no value.
3. Alternatively, remove the regime classifier entirely and use the ADX+EMA rule directly.

**Priority**: High

#### Issue 4.2.3: Direction Predictor Train/Test Contamination
**Severity**: High
**Problem**: The `DirectionPredictor._make_sequences` method creates overlapping sequences from a sliding window:
```python
for row in range(start + self.sequence_length, end):
    sequence_list.append(x[row - self.sequence_length : row])
```

This means:
1. Consecutive sequences overlap by `sequence_length - 1` time steps.
2. When split into train/validation folds, information leaks between adjacent sequences.
3. The validation accuracy is inflated because the model is tested on sequences that share 59/60 of their time steps with training sequences.

**Impact**: The reported "validation accuracy" is likely significantly overestimated. The true directional accuracy on independent (non-overlapping) test samples may be barely above random (50%).

**Recommendation**:
1. Add a gap between training and validation sequences: do not allow train sequences to overlap with validation sequences in time.
2. Report accuracy on *non-overlapping* sequences only (e.g., use every 60th sequence).
3. The current ICIR of -0.076 suggests the direction predictor is *anti-predictive* — it would be better to invert its signal.

**Priority**: High

---

## 5. Data Pipeline Assessment

### Score: 7/10 — Well-engineered but has specific quality gaps.

### 5.1 Strengths

| Strength | Detail |
|----------|--------|
| **Multiple providers** | OANDA primary, yfinance fallback, FRED for macro, Alpha Vantage for news, CFTC for COT |
| **Concurrent fetching** | `ThreadPoolExecutor` for multi-timeframe data, reducing latency |
| **Retry with backoff** | Exponential backoff for all provider calls, configurable |
| **WAL mode SQLite** | Write-Ahead Logging enabled for all databases |
| **Warmup buffer** | 300-candle OHLCV buffer, 200-bar feature warmup |
| **Feature causality check** | `assert_no_lookahead` verifies feature data availability vs source index |

### 5.2 Critical Data Issues

#### Issue 5.2.1: News Sentiment is Non-Functional
**Severity**: Medium
**Problem**: The news sentiment pipeline:
1. Fetches from Alpha Vantage (free tier: 5 calls/min, 500 calls/day)
2. Filters for gold terms
3. Scores sentiment using the `SentimentScorer`

Alpha Vantage's news sentiment for gold is notoriously poor — it provides generic financial news with unreliable sentiment scoring. The system already has `enable_sentiment: false` in settings, which is the correct decision.

**Impact**: The sentiment code path is dead code. It's 50+ lines in the orchestrator, adds complexity, and doesn't contribute to trading decisions.

**Recommendation**: Delete the news sentiment infrastructure entirely, or leave it as a disabled reference implementation for future work. The economic calendar / blackout filter is more valuable and should be kept.

**Priority**: Low

#### Issue 5.2.2: Investing.com Web Scraping is Fragile
**Severity**: Medium
**Problem**: The economic calendar is scraped from Investing.com using regex-based HTML parsing:
```python
re.finditer(r"<tr\b(?P<attrs>[^>]*)>(?P<body>.*?)</tr>", html, ...)
```

This is extremely fragile:
1. Investing.com may change their HTML at any time, breaking the parser silently.
2. The scraping may violate Investing.com's Terms of Service.
3. No caching of parsed events leads to repeated HTTP requests.
4. No fallback for when the parser fails to find events.

**Impact**: If the blackout filter fails silently, the system may trade through high-impact news events, incurring gap losses. If it throws an error, the orchestrator catches it in `_process_candle` and continues, but the blackout state becomes unknown.

**Recommendation**: Replace with a proper economic calendar API (e.g., ForexFactory free tier via an `ff-econ` wrapper, or a paid service like Econodaily). Add explicit logging when blackout status changes.

**Trade-off**: A paid API is $10-50/month. Free alternatives exist but require more development.

**Priority**: Medium

---

## 6. Execution and Broker Assessment

### Score: 7/10 — PaperBroker is well-designed; OandaBroker is incomplete.

### 6.1 Strengths

| Strength | Detail |
|----------|--------|
| **SL/TP rebasing around fill price** | Preserves intended risk distance even when slippage shifts entry price |
| **R-multiple normalization** | Every trade records R-multiple for cross-strategy comparison |
| **Entry/exit slippage tracking** | Separately tracked, enabling execution quality analysis |
| **Gaussian slippage with negative tail** | While debatable (see Issue 2.2.1), the implementation is clean |
| **Open position persistence** | Saved to SQLite every cycle for restart recovery |

### 6.2 Critical Broker Issues

#### Issue 6.2.1: OandaBroker is Not Battle-Tested
**Severity**: High
**Problem**: The `OandaBroker` class:
1. Uses LIMIT orders for entry (`"type": "LIMIT"`) — but Donchian breakout entries should be MARKET orders (you want to enter at the breakout, not wait for a pullback to a limit price).
2. Has no polling for order fill status — after submitting, it assumes immediate fill.
3. Does not handle partial fills.
4. Has no reconnection logic for network failures.
5. The `get_account_state()` returns `daily_pnl=0.0` as a placeholder — this means the daily kill switch won't work with live broker.

**Impact**: If the OANDA broker is ever enabled, the system will:
- Submit limit orders that don't fill during fast breakouts
- Fail to detect unfilled orders
- Report incorrect daily P&L (missing the kill switch entirely)
- Lose state on network interruptions

**Recommendation**:
1. Change entry order type from LIMIT to MARKET for Donchian breakout entries.
2. Add order status polling with timeout (e.g., poll every 5 seconds for 30 seconds).
3. Handle partial fills by reducing the intended position size.
4. Add automatic reconnection with exponential backoff.
5. Compute daily_pnl from realized trade history, not from broker response.

**Priority**: High

#### Issue 6.2.2: Spread Cost Calculation is Wrong
**Severity**: High
**Problem**: `PaperBroker._spread_cost`:
```python
def _spread_cost(self, units: float) -> float:
    return 2.0 * self.get_current_spread_pips(self.instrument) * self.instrument_spec.pip_value_per_unit * float(units)
```

This multiplies spread by 2, presumably for entry + exit. But:
1. Spread is already paid on entry (included in fill price) and on exit (included in close price) — the OANDA fill price includes the spread cost.
2. The spread cost is double-counted in the model: once in the fill price (via `_worsen_entry_price` + slippage), and again as a separate `spread_cost` line item.
3. The apparent net PnL subtracts spread cost from gross PnL, but the gross PnL already reflects the widened entry/exit prices.

**Impact**: Trades appear 10-30% worse than they actually are (or better, depending on slippage direction). This distorts the Kelly calculation (which uses net PnL) and makes it harder to reconcile backtest results with live trading.

**Recommendation**: Remove the explicit `spread_cost` calculation from `_close_position_at_price`. The spread should be embedded in the fill prices themselves. If a separate cost line is needed for reporting, compute it from the difference between mid-price fills and the actual bid/ask fills.

**Priority**: High

---

## 7. Monitoring and Analytics Assessment

### Score: 6/10 — Functional dashboard, but missing critical analytics.

### 7.1 Strengths

| Strength | Detail |
|----------|--------|
| **Streamlit dashboard** | Live equity curve, trade log, rolling metrics, open positions, signal monitor |
| **Health file** | JSON metrics written to disk for external monitoring |
| **Observability report** | Printed hourly with all key metrics |
| **Missed signal logging** | Every rejected signal is logged with reason |

### 7.2 Critical Monitoring Gaps

#### Issue 7.2.1: No Trade Quality Analytics
**Severity**: High
**Problem**: The dashboard shows trade count, PnL, and win rate — but not:
- **R-multiple distribution**: Are wins larger than losses? The strategy claims 2R wins and 1R losses, but is that actually true?
- **MAE/MFE analysis**: Maximum Adverse/Favorable Excursion — the gold standard for evaluating exit quality.
- **Equity curve autocorrelation**: Detecting when the strategy enters a "bad regime" before drawdown becomes severe.
- **Time-based performance breakdown**: Does the strategy trade differently on Mondays vs Fridays? London vs NY vs Asia? Up-trend vs down-trend?
- **Consecutive loss tracking**: The Monte Carlo says losing streaks can reach 40 trades. Is the system tracking this in real time?

**Recommendation**: Add to the dashboard:
1. R-multiple distribution histogram
2. MAE/MFE scatter plot by trade duration
3. Weekly performance breakdown (by session, day, regime)
4. Consecutive loss/wins counter
5. A "health score" that combines Sharpe, win rate, R-multiple, and drawdown into a single metric

**Priority**: High

#### Issue 7.2.2: No Live vs Backtest Comparison
**Severity**: High
**Problem**: The "Live vs Backtest Comparator" script exists (`scripts/paper_trading/run_live_vs_backtest_comparator.py`) but is NOT integrated into the dashboard. The README says it "needs 100+ trades" (currently at 21 trades, ~8 days of trading). At ~1.8 trades/day, it will take ~55 more days to reach 100 trades.

**Impact**: Without live vs backtest comparison, there is no way to know if the strategy is performing as expected. The live win rate (55%) differs significantly from the backtest win rate (~37%), which is either:
- Random variation due to small sample (most likely)
- A structural difference in execution or pricing
- A sign that the backtest overestimates performance

**Recommendation**: Implement a **sequential live vs backtest monitor** that updates after each trade:
1. After each trade, find the N most similar backtest trades (matching direction, volatility regime, session)
2. Compare the live outcome to the distribution of backtest outcomes
3. If the cumulative P&L diverges beyond the 95th percentile of expected variation, flag it
4. Show this comparison as a chart on the dashboard

**Priority**: High

---

## 8. Software Engineering Assessment

### Score: 7/10 — Above average for a solo project, with real professional touches.

### 8.1 Strengths

| Strength | Detail |
|----------|--------|
| **Type hints** | Extensive use of `from __future__ import annotations` and type hints throughout |
| **Dataclass usage** | `AccountState`, `RiskOrder`, `OrderResult`, `TradeInstruction`, etc. all properly structured |
| **Clean schema design** | ACID-compliant SQLite with WAL mode, proper indexes, upsert semantics |
| **Logging discipline** | Structured logging with consistent format, rotating file handler, fallback when loguru unavailable |
| **Exception handling** | Graceful degradation at every layer (retries, fallbacks, clear warnings) |
| **Settings management** | Centralized YAML/JSON config, env vars for secrets, single source of truth |
| **Systemd integration** | Proper service files, PID locks, watchdogs, timers |

### 8.2 Critical Engineering Issues

#### Issue 8.1: No Test Suite Coverage
**Severity**: Critical
**Problem**: The repository has a `pytest.ini` and test files under `exports/obsidian_phase0_template/tests/`, but:
- No tests for any `aurum1/` core module
- No tests for `d4_paper_trader.py`
- No tests for `forward_shadow_donchian.py`
- No CI pipeline (GitHub Actions or similar)
- No coverage measurement

**Impact**:
- A code change that breaks critical functionality (e.g., Kelly calculation, slippage model, trade persistence) will go undetected until discovered in production.
- The "DB persist bug" that took until Jul 7 to fix (all trades now save to DB) would have been caught immediately with a single test.
- Refactoring is extremely risky — there's no safety net.

**Recommendation**: Build a test suite, starting with the highest-value targets:
1. **Unit tests**: InstrumentSpec, FeatureEngineer (with known OHLCV data), PaperBroker slippage/SL-TP logic, RiskManager Kelly calculation, EnsembleSignal combine()
2. **Integration tests**: D4 paper trader with fixed input data (check trades match expected), forward shadow processing
3. **Regression tests**: For every bug fixed, add a test that would have caught it

Even 10-20 well-chosen tests provide enormous confidence gains.

**Priority**: Critical

#### Issue 8.2: No Versioning for Research Artifacts
**Severity**: Medium
**Problem**: Research outputs (CSVs, JSONs, SQLite databases) are timestamped but there's no systematic versioning:
- Backtest databases (`backtest_execution_*.sqlite3`) are generated per run
- Shadow databases (`donchian_shadow.sqlite3`) are overwritten
- Phase reports (S1-S5) are timestamped but not linked to specific code versions

**Impact**: If someone runs a backtest today and another next week, they can't easily tell which code version produced each result. This makes regression hunting extremely difficult.

**Recommendation**: 
1. Record git hash in all generated artifacts (backtest results, phase reports)
2. Use the hash to create reproducible backtest directories
3. Add a `AURUM1_COMMIT` field to health file and observability reports

**Priority**: Low

#### Issue 8.3: Profit Factor Criteria in Walk-Forward is Wrong
**Severity**: Medium
**Problem**: In `walk_forward.py:124`:
```python
"mean_profit_factor": mean_profit_factor > 1.30,
```

A mean profit factor of 1.30 is an unrealistic threshold. The D4 strategy's walk-forward shows mean PF of **1.14** (L20). Setting the promotion gate at 1.30 means this criteria **never passes**. This creates a false sense of diagnostic utility — the metric shows "failed" when it's actually meeting expectations.

**Recommendation**: Set criteria based on the strategy's actual distribution:
- `mean_profit_factor > 1.05` (slightly profitable after costs)
- Or better, use a statistical test: is the 90% confidence interval of PF above 1.0?

**Priority**: Medium

---

## 9. Documentation Assessment

### Score: 8/10 — Exceptionally well-documented for a solo project.

### 9.1 Strengths

| Strength | Detail |
|----------|--------|
| **Architecture documentation** | README.md with Mermaid diagram, ARCHITECTURE.md with layered design |
| **Data flow documentation** | DATA_FLOW.md with end-to-end pipeline visualization |
| **Research methodology** | RESEARCH.md documents every phase with results and confidence levels |
| **Strategy documentation** | STRATEGIES.md with comparison matrix, all variants documented |
| **Status tracking** | STATUS.md updated with current metrics, trade log, action items |
| **Deployment guide** | DEPLOYMENT.md with step-by-step instructions for every service |
| **Research notes** | `research/` directory with 30+ documents covering math, experiments, academic references |

### 9.2 Documentation Gaps

#### Issue 9.1: No Risk Parameter Documentation
**Severity**: Medium
**Problem**: There's no single document explaining why specific risk parameters were chosen:
- Why 0.25% risk per trade? (The Monte Carlo section is in README, but not centrally referenced)
- Why -3% daily kill switch?
- Why -8% total drawdown kill?
- Why 0.25 Kelly cap?
- Why 3 pip spread filter?

**Recommendation**: Create a `docs/RISK_PARAMETERS.md` documenting the rationale for each risk parameter, the analysis that supported it, and the conditions under which it should be reviewed/recalibrated.

**Priority**: Low

#### Issue 9.2: No Incident/Decision Log
**Severity**: Low
**Problem**: Multiple design decisions and bugs are only documented in git commit messages. There's no incident log or decision register that captures:
- Why was the orchestrator stopped?
- What was the DB persist bug that was fixed on Jul 7?
- Why was D4 chosen over the original state machine strategy?
- What was the signal generator issue (signal_2) that caused the shutdown?

**Recommendation**: Create a `docs/DECISIONS.md` that records major decisions with:
1. Date
2. Decision made
3. Context and alternatives considered
4. Expected outcome
5. Actual outcome (updated retroactively)

**Priority**: Low

---

## 10. Deployment and Operations Assessment

### Score: 7/10 — Solid for personal use, but has production-readiness gaps.

### 10.1 Strengths

| Strength | Detail |
|----------|--------|
| **Systemd services** | Proper service files, timers, PID locks |
| **Automated backups** | Daily SQLite backups with 28-day retention |
| **Logrotate** | Configured via `deploy/logrotate/aurum1` |
| **Health endpoint** | Flask HTTP health check on localhost:8080 |
| **Single-instance lock** | PID file prevents duplicate processes |

### 10.2 Operations Issues

#### Issue 10.1: No Failover or High Availability
**Severity**: High
**Problem**: The system runs on a single Ubuntu VPS (3.7GB RAM, 38GB disk). If the server goes down:
- No automatic failover
- No secondary instance
- No status page to check if the system is running
- The Cloudflare tunnel depends on the local `cloudflared` service

**Impact**: System downtime of hours to days if the VPS provider has an outage or the instance needs a manual restart. During downtime, market opportunities are missed and open positions are not managed.

**Recommendation**: For paper trading, this is acceptable. For live trading, at minimum:
1. Set up a secondary monitoring instance on a different provider (e.g., a $5/month DigitalOcean droplet or a Raspberry Pi at home)
2. Have the secondary instance ping the primary health endpoint every 5 minutes
3. If the primary is down, send an alert (email/SMS) within 5 minutes

**Priority**: Medium (Low for paper trading; High for live capital)

#### Issue 10.2: No Database Migration Strategy
**Severity**: Medium
**Problem**: Schema changes are handled by ad-hoc `ALTER TABLE` statements:
```python
for col in ("entry_time", "exit_time", "risk_amount", "spread_cost", "slippage_cost"):
    try:
        conn.execute(f"ALTER TABLE trades ADD COLUMN {col} TEXT")
    except sqlite3.OperationalError:
        pass
```

This works for adding columns but fails for:
- Renaming columns
- Changing column types
- Merging schemas
- Rolling back migrations

**Impact**: Schema evolution becomes increasingly risky as the database grows. A bad migration could corrupt the trade history database.

**Recommendation**: Implement a simple migration system:
1. Add a `schema_version` table with a single integer row
2. Number each schema change
3. On startup, apply any pending migrations in order
4. Fail loudly if a migration fails

**Priority**: Medium

---

## 11. Issue Priority Summary

| Priority | Count | Key Items |
|----------|-------|-----------|
| **Critical** | 6 | Transaction cost model optimism, data leakage in backtesting, no test suite, single-strategy concentration, no gap risk protection, survivorship bias in data |
| **High** | 10 | Feature lookahead disabled, regime classifier circular validation, direction predictor train/test contamination, OandaBroker not tested, spread cost double-count, no trade quality analytics, no real-time alerting, no live vs backtest comparison, gap risk in Monte Carlo, correlation monitoring missing |
| **Medium** | 7 | Orchestrator dead code, ML adds no value, Investing.com scraping fragility, Kelly defaults mismatch, profit factor criteria wrong, no database migration, stale data detection |
| **Low** | 4 | News sentiment dead code, no risk parameter doc, no decision log, no research versioning |

---

## 12. Overall Assessment

### Maturity Score: 6.5/10

| Dimension | Score | Assessment |
|-----------|-------|------------|
| Architecture | 7/10 | Well-layered but has two parallel code paths |
| Quantitative Research | 7/10 | Solid methodology marred by cost model optimism |
| Risk Management | 6/10 | Adequate for paper, dangerous gaps for live |
| ML Modeling | 5/10 | Well-built but adds zero value |
| Data Pipeline | 7/10 | Well-engineered with specific quality gaps |
| Execution Engine | 7/10 | PaperBroker good; OandaBroker incomplete |
| Monitoring | 6/10 | Dashboard exists but lacks critical analytics |
| Software Engineering | 7/10 | Professional-quality code, no tests |
| Documentation | 8/10 | Exceptionally well-documented |
| Deployment | 7/10 | Solid for personal use, no HA |

### What Prevents Production-Grade Quality

1. **No test suite** — This is the single biggest blocker. Without tests, no change can be made confidently, and every bug is discovered in production.

2. **Unrealistic transaction cost model** — The backtest PnL is likely 20-40% overstated. The real strategy edge may be marginal (PF ≈ 1.05-1.08 under realistic costs).

3. **No multi-strategy diversification** — Trading a single strategy on a single instrument is not a production-grade approach. One structural market change could destroy profitability for months.

4. **Data leakage risk** — The disabled lookahead check and overlapping validation sequences in the DirectionPredictor mean the ML validation metrics are unreliable.

5. **No alerting** — The system can fail silently for hours without anyone knowing.

6. **OandaBroker is incomplete** — Switching from paper to live would fail immediately due to order type, fill monitoring, and daily P&L tracking issues.

### Recommended Roadmap

**Phase 1 — Immediate (1-2 weeks)**
1. ✅ Fix transaction cost model (realistic spreads, asymmetric slippage)
2. ✅ Add tests for: Kelly calculation, PaperBroker SL/TP logic, Donchian signal generation
3. ✅ Re-enable lookahead check and fix failures
4. ✅ Add stale data alerting (simple webhook)

**Phase 2 — Short-term (2-4 weeks)**
5. ✅ Fix OandaBroker: MARKET orders, fill polling, daily P&L tracking
6. ✅ Add MAE/MFE analysis to dashboard
7. ✅ Implement live vs backtest comparator in dashboard
8. ✅ Add regime-aware bootstrap Monte Carlo (not just reshuffling)

**Phase 3 — Medium-term (1-2 months)**
9. ✅ Develop 1-2 uncorrelated strategies on different instruments
10. ✅ Add strategy-level risk allocation (Kelly across multiple systems)
11. ✅ Implement proper database migrations
12. ✅ Remove or deprecate the orchestrator code path

**Phase 4 — Pre-Live Capital (3+ months)**
13. ✅ 500+ paper trades accumulated across all strategies
14. ✅ Live vs backtest gap < 20% in key metrics
15. ✅ Full test suite with >70% coverage
16. ✅ Alerting and failover tested in simulation
17. ✅ Third-party security audit of OANDA credentials and server access

---

*This audit was conducted as a constructive review. The system shows exceptional engineering discipline for a solo project and has real potential. The critical issues identified are fixable — none require fundamental architectural changes. The path to production-grade quality is clear and achievable.*
