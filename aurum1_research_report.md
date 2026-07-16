# AURUM-1 Research Report: Academic & Expert Improvements for D4 Strategy

**Date:** 2026-07-16
**Target:** XAU/USD M15 Donchian Breakout (D4 variant)
**Backtest:** 11 years, 8,175 trades, PF 1.14, ~40% win rate

---

## Executive Summary — Top 5 Most Impactful Changes

### 1. Replace Fixed 2R Exit with Adaptive Chandelier Exit (HIGH impact)
The Chandelier Exit (Chuck LeBeau/Alexander Elder) uses a trailing stop anchored to the highest high since entry, minus a multiple of ATR. Backtest evidence across 200 S&P 500 swing trades shows Chandelier Exit produced the highest expectancy (+$0.81/trade vs +$0.41 for fixed), the best win rate (51.3% vs 38.2%), and lowest max drawdown (10.2% vs 18.4%) compared to fixed stops. For your ~40% win-rate system, this alone could push win rate toward 48-50% while improving PF.

### 2. Volatility Compression/Expansion Entry Filter (HIGH impact)
Your #1 problem is false breakouts. Multiple independent sources converge: valid breakouts are preceded by volatility compression (ATR below its 20-bar SMA), followed by expansion (ATR spike on the breakout candle). For M15 gold specifically, the recommended ATR threshold is 1.7-2.0x the recent average. This is a simple check that could dramatically reduce fakeout entries without the draconian elimination that the volume-imbalance filter caused (83% trade loss).

### 3. Dual-Exit: Partial Take-Profit at 1R + Trail Remainder (HIGH impact)
Academic evidence (arXiv 1701.03960) shows combining take-profit targets with trailing stops is theoretically optimal. A simple implementation: close 50% at 1R, move stop to breakeven on the remainder, trail with Chandelier Exit. This addresses the fixed 2R problem while capturing extended trends. The partial close improves psychological win rate and reduces variance.

### 4. Multi-Timeframe Trend Filter Instead of Session Filter (MEDIUM-HIGH impact)
The session filter historically hurt performance, but the research strongly supports filtering by higher-timeframe trend direction instead. Multiple academic sources rank "higher timeframe direction" as the #1 filter for breakout quality. Use H1 Donchian or EMA alignment as a binary filter (only take longs when H1 trend is up). This is different from your existing H1/H4 EMA alignment — specifically, it should be a *hard gate* that blocks entries against the H1 trend, not just a soft feature. Test as a strict filter (block trades).

### 5. Regime-Dependent Sizing and Entry (MEDIUM impact)
Your LightGBM regime classifier has 0.85 validation Sharpe but barely changes strategy outcomes — this suggests the regime signal is not being *acted upon* strongly enough. A more impactful approach: use ADX(14) regime directly (ADX > 25 = trending, ADX < 20 = ranging) to switch between two modes. In trending mode: normal breakout entries with Chandelier exit. In ranging mode: either skip trades entirely, or use tighter exits (Keltner at 1.5x) and smaller size (half Kelly). Direct ADX thresholds are more interpretable and have less overfitting risk than an ML classifier.

---

## 1. Entry Improvements

### 1.1 Volatility Compression Pre-Breakout Filter

**Problem:** Donchian breakouts fire on every new 20-bar high/low, regardless of whether the market is in a compressed spring-loaded state or simply drifting to new highs in a low-volatility grind.

**Technique:** Only take a breakout signal if ATR(14) was below its 20-period SMA at the time of signal (compression), AND the breakout candle's ATR shows expansion relative to the prior bar.

```python
def compression_filter(df, atr_period=14, sma_period=20, expansion_mult=1.5):
    df['ATR'] = df['TR'].rolling(atr_period).mean()
    df['ATR_SMA'] = df['ATR'].rolling(sma_period).mean()
    df['ATR_Compressed'] = df['ATR'].shift(1) < df['ATR_SMA'].shift(1)  # compressed before signal
    df['ATR_Expanding'] = df['ATR'] > df['ATR'].shift(1) * expansion_mult  # expanding on signal
    return df['ATR_Compressed']  # require compression, expansion is optional bonus
```

**Academic basis:** KAS ZeroX Breakout Suite (TradingView) blocks signals when ATR Z-Score drops below -1.0. The Volatility Expansion Trigger Strategy (Everand) uses ATR % change + Donchian Middle Band to identify consolidation-to-expansion transitions.

**Expected impact:** HIGH — directly addresses the #1 problem (false breakouts).
**Implementation complexity:** LOW — ~5 lines of pandas.
**Conflict check:** None with existing findings. Does not eliminate trades like volume imbalance did; it filters based on volatility state.
**Suggested test:** A/B test with/without filter. Expected: fewer trades (maybe 30-40% reduction), higher win rate (target 45-48%), higher PF.

### 1.2 Pullback Entry After Breakout (Hybrid Approach)

**Problem:** Current entry is at next candle's open after the breakout signal. This catches the immediate momentum but exposes you to bull traps where price breaks out then immediately reverses.

**Technique:** After a Donchian breakout signal, do NOT enter immediately. Wait. Define a pullback as a 1-3 bar retracement that stays above the Donchian middle line (SMA of 20-bar high/low) for longs, or below it for shorts. Enter on the first bar that closes in the original breakout direction after the pullback.

**Academic evidence:** A 5-year XAU/USD backtest using a 4-phase state machine (scan -> pullback (1-3 counter-trend candles) -> breakout -> enter) achieved Sharpe 0.892, Profit Factor 1.64, Win Rate 55.43%, Max DD 5.81%. These are dramatically better than D4's current metrics.

**Expected impact:** HIGH — could significantly improve win rate (from ~40% to >50%).
**Implementation complexity:** MEDIUM — requires a state machine modification to add a "pullback watch" state between ARMED and WINDOW_OPEN. Your existing state machine already has this structure (SCANNING -> ARMED -> WINDOW_OPEN); the pullback logic fits naturally.
**Conflict check:** Will reduce trade count. The pullback may never come in a fast trend, causing missed trades. However, the XAU/USD backtest above shows net positive.
**Suggested test:** Add a counter that tracks retracement within the Donchian channel after a signal. Enter only if price pulls back to the middle 50% of the channel and then reverses back toward the breakout direction.

### 1.3 OBV Divergence Filter for Fakeout Detection

**Problem:** Breakouts often occur on low participation (institutional positioning) and reverse.

**Technique:** Add On-Balance Volume (OBV) as a confirmation filter. For a BUY signal: require that OBV is also making a new high (or at least not diverging). A valid breakout shows price AND OBV making new highs. A warning sign is price breaking out but OBV failing to confirm (lower high on OBV).

```python
def obv_divergence_filter(df, lookback=20):
    df['OBV'] = (df['Volume'] * (~df['Close'].diff().le(0) * 2 - 1)).cumsum()
    df['OBV_Higher_High'] = df['OBV'].rolling(lookback).max()
    df['Price_Higher_High'] = df['High'].rolling(lookback).max()
    # Divergence: price made new high but OBV didn't
    df['OBV_Confirmed'] = (df['High'] == df['Price_Higher_High']) == (df['OBV'] == df['OBV_Higher_High'])
    return df['OBV_Confirmed']
```

**Academic basis:** FTMO/OANDA VA Breakout Strategy identifies OBV divergence as a primary fakeout detection mechanism.
**Expected impact:** MEDIUM — reduces false signals, but may also filter out valid breakouts during low-volume periods.
**Implementation complexity:** LOW.
**Conflict check:** Your volume imbalance filter killed 83% of trades, which was too aggressive. OBV divergence is a softer filter that only blocks when there's a *divergence*, not when volume is merely low.
**Suggested test:** Add as an optional hard filter. Track how many trades it blocks and whether those blocked trades were losers.

### 1.4 Session-Refined Donchian (NOT the old session filter)

**Problem:** The old session filter (London/NY only) hurt performance. But research clearly shows gold has significant session-dependent behavior.

**Insight:** Instead of blocking trades outside certain sessions, use *different Donchian parameters* per session. The Asian session is lower volatility — use a shorter lookback (e.g., 10 bars) during Asian hours to capture their more compact ranges. Use the standard 20-bar during London/NY.

**Technique:**
```
if session == ASIAN:
    lookback = 10
elif session == LONDON:
    lookback = 20
elif session == NY:
    lookback = 15  # NY is faster-moving
```

**Expected impact:** LOW-MEDIUM — marginal improvement, low risk of harm.
**Implementation complexity:** LOW.
**Conflict check:** This is NOT the same as the old session filter (which blocked trades). This adapts the strategy parameters per session, which is fundamentally different.
**Suggested test:** Compare the standard D4 against the session-adaptive version. Measure PF separately per session to validate.

### 1.5 Multiple Timeframe Breakout Confirmation (Hard Gate)

**Problem:** Your current architecture already uses H1/H4 EMA alignment as a feature, but the ML model may be weighting it poorly. Multiple independent sources rank higher-timeframe trend as the #1 filter for breakout quality.

**Technique:** Add a hard gate that blocks trades against the H1 Donchian trend. Only take M15 BUY signals when H1 price > H1 Donchian middle line (or > H1 EMA 50). This should be a *hard rule*, not an ML feature, so it's non-negotiable.

**Academic basis:** The DkS Market Structure Breakout Strategy (TradingView) and multiple forum discussions rank "higher timeframe direction" as the most important breakout filter.
**Expected impact:** MEDIUM-HIGH — if the H1 trend filter aligns well with gold's behavior, this could significantly improve PF.
**Implementation complexity:** LOW — simple check.
**Conflict check:** Your existing H1/H4 features may already capture this, but as soft ML inputs they can be overridden. A hard gate is more aggressive.
**Suggested test:** Run unfiltered vs. H1-filtered. Measure PF, trade count, and win rate separately.

---

## 2. Exit Improvements

### 2.1 Chandelier Exit (Replace Fixed 2R SL and 2R TP)

**Problem:** Fixed 2R stop is naive. Stops that work in low volatility get hit in high volatility. Fixed profit targets leave money on the table in strong trends.

**Technique:** Use Chandelier Exit as the exclusive exit mechanism (no separate TP). For a long entry:
- Chandelier Stop = Highest High since entry - (ATR(14) * multiplier)
- Ratchet: the stop only ever moves UP (never down), locking in gains
- Default settings for M15 gold: period=14, multiplier=2.5-3.0

```python
def chandelier_exit(df, entry_price, entry_index, period=14, multiplier=2.5):
    """Returns stop price for each bar after entry."""
    stops = []
    highest_since_entry = entry_price
    for i in range(entry_index, len(df)):
        highest_since_entry = max(highest_since_entry, df['High'].iloc[i])
        atr = df['ATR'].iloc[i]
        stop = highest_since_entry - (atr * multiplier)
        stops.append(stop)
    return stops  # Take first bar where close < stop
```

**Backtest evidence (200 S&P 500 swing trades, 2020-2024):**

| Metric | Fixed $3 Stop | Chandelier (22/3x) |
|--------|:------------:|:-----------------:|
| Win Rate | 38.2% | 51.3% |
| Expectancy/Trade | +$0.41 | +$0.81 |
| Max DD | -18.4% | -10.2% |
| Premature Stop (VIX > 25) | 47% | 16% |

**For gold M15:** Start with ATR(14), multiplier 2.5 for shorter-term orientation. The theoretical Fonseca (2026) paper found a "CAGR-flat region" across multipliers 3.5-7.0 for NASDAQ, but gold on M15 would need a tighter range. Test multipliers from 1.5 to 4.0 in steps of 0.25.

**Expected impact:** HIGH — this is likely the single biggest improvement available.
**Implementation complexity:** MEDIUM — replaces existing exit logic; needs careful handling of the ratchet mechanism.
**Conflict check:** None. This replaces the fixed 2R system entirely.
**Suggested test:** A/B test current D4 (fixed 2R) vs. Chandelier-only (no TP). Measure PF, win rate, profit factor, max DD, and trade duration.

### 2.2 Partial Take-Profit at 1R + Breakeven + Chandelier Trail

**Problem:** Pure Chandelier exit can give back significant open profits during pullbacks within trends.

**Technique:** Hybrid approach — the single highest-impact exit improvement:
1. Enter at signal
2. At 1R profit: close 50% of position
3. Move stop to breakeven on remaining 50%
4. Trail remaining 50% with Chandelier Exit (tighter multiplier: 2.0x ATR since half is already banked)
5. Optional: close remainder if not hit TP/SL within 48 bars (time-based exit)

**Academic basis:** arXiv 1701.03960 proves combined take-profit + trailing stop is theoretically optimal. Katz & McCormick (1998) confirm exit strategies matter enormously but are parameter-sensitive.

```python
class PartialExitManager:
    def __init__(self, entry_price, atr_at_entry):
        self.entry = entry_price
        self.breakeven = entry_price
        self.half_target = entry_price + (2 * atr_at_entry) if long else entry_price - (2 * atr_at_entry)
        self.position_half_closed = False
        self.chandelier_stop = None
    
    def update(self, bar, position):
        if not self.position_half_closed:
            if bar['high'] >= self.half_target:
                # Close half at 1R
                position.close_half()
                self.position_half_closed = True
                self.chandelier_stop = self.entry  # Start from breakeven
        else:
            # Update chandelier for remaining half
            highest = max(position.entry_price, self.highest_since_close)
            self.chandelier_stop = max(self.chandelier_stop, highest - (atr * 2.0))
            if bar['close'] < self.chandelier_stop:
                position.close_remaining()
```

**Expected impact:** HIGH — addresses the dual needs of banking profits and capturing trends.
**Implementation complexity:** MEDIUM — requires partial position tracking.
**Conflict check:** None. This is a pure improvement over the fixed 2R system.
**Suggested test:** Compare three variants: (A) fixed 2R, (B) Chandelier only, (C) 50% at 1R + Chandelier trail.

### 2.3 Time-Based Exit (Max Hold Bars)

**Problem:** Your trades can theoretically stay open indefinitely if price oscillates around breakeven, tying up capital in non-productive positions.

**Technique:** Add a forced exit if the trade hasn't hit TP/SL within a maximum number of bars. The optimal max hold for trend following varies by timeframe and asset.

**Research:** Katz & McCormick (1998) confirmed time-based exits as a valid component. For M15 gold, suggested values:
- Conservative: 192 bars (48 hours / 2 trading days)
- Aggressive: 96 bars (24 hours)
- Test range: 48 to 384 bars

**Rationale:** If gold hasn't moved to 2R within 2-3 trading days, the breakout momentum has likely dissipated. The trend followers' edge decays with time — holding longer doesn't increase the probability of resolution.

**Expected impact:** MEDIUM — reduces capital drag, may slightly reduce win rate but improve overall efficiency.
**Implementation complexity:** LOW.
**Conflict check:** None with existing exits. Can be layered on any exit method.
**Suggested test:** Add a `bars_since_entry` counter. Compare unlimited hold vs. 96-bar max vs. 192-bar max.

### 2.4 Breakeven Stop Timing

**Problem:** Currently no automatic breakeven mechanism. Trades that reach near-target then reverse to a loss are unnecessarily painful.

**Technique:** Move stop to breakeven when price reaches 0.5R to 1.0R (test this range). For a 2R system with stop at 2x ATR:
- Breakeven trigger: price reaches entry + 1x ATR (0.5R)
- Stop moves to entry price + spread buffer

**Expected impact:** MEDIUM — reduces losing trades and improves psychological comfort.
**Implementation complexity:** LOW.
**Conflict check:** None. Can be combined with partial exit or standalone.
**Suggested test:** A/B test with/without breakeven trigger. Track how many trades that hit breakeven would have eventually hit SL vs. TP.

---

## 3. Risk & Sizing Improvements

### 3.1 Anti-Martingale Sizing After Wins

**Problem:** Current Kelly sizing (fractional, capped at 0.25) is static per trade. But academic research shows trend followers should increase size after wins (trend is confirmed) and decrease after losses (trend may have ended).

**Technique:** Implement a "hot hand" multiplier that scales position size based on recent trade outcomes:
- After 2 consecutive wins: size x 1.25
- After 3+ consecutive wins: size x 1.5 (cap)
- After any loss: reset to base size

**Academic basis:** Scholz (2012, 2014) showed that in positively autocorrelated markets (trending), higher leverage benefits short-term strategies. Consecutive wins signal strong trending conditions where increasing size is rational.
**Expected impact:** MEDIUM — should increase total PnL without proportionally increasing risk.
**Implementation complexity:** LOW.
**Conflict check:** This is an extension of the existing Kelly system, not a replacement.
**Suggested test:** Track a separate equity curve with anti-martingale vs. fixed fractional Kelly. Compare max DD, CAGR, and PF.

### 3.2 Volatility Scaling (Constant Risk per Trade)

**Problem:** Current risk per trade is 0.25% of equity regardless of volatility. In low-volatility periods the stop is tighter (risk is actually smaller), and in high-volatility periods the stop is wider (risk is larger). This means unequal risk exposure across trades.

**Technique:** Instead of fixed 0.25% equity risk, use:

```
position_size = (equity * target_risk_pct) / (ATR_in_dollars * stop_multiple)
```

This ensures each trade risks exactly the same dollar amount regardless of current ATR. When ATR doubles, position size halves.

**Academic basis:** Constant risk scaling is standard in professional CTA trend following. Turtle Traders used Unit = Risk / (N x Point Value). The CFM white paper confirms that capping trend forecasts and adjusting positions for volatility improves risk-adjusted returns.
**Expected impact:** MEDIUM — reduces equity curve volatility, improves Sharpe.
**Implementation complexity:** LOW.
**Conflict check:** Your current volume-imbalance filter era already tested some volatility-related concepts. This is about sizing, not entry filtering.
**Suggested test:** Compare current fixed-% sizing vs. ATR-volatility-scaled sizing. The total PnL may stay similar, but the Sharpe and max DD should improve.

### 3.3 Regime-Dependent Kelly Fraction

**Problem:** The same Kelly fraction (0.25) is used in all market conditions. But the optimal fraction differs by volatility regime.

**Technique:** Use ADX to set the Kelly multiplier:
- ADX > 30 (strong trend): use full fraction (0.25)
- ADX 20-30 (moderate trend): use half fraction (0.125)
- ADX < 20 (weak/ranging): use quarter fraction (0.0625) or skip

**Academic basis:** Hsieh, Barmish & Gubner show betting frequency should interact with variance estimates. Lower Kelly fractions in adverse conditions is mathematically optimal.
**Expected impact:** MEDIUM — reduces drawdowns during sideways markets when the strategy naturally performs poorly.
**Implementation complexity:** LOW.
**Conflict check:** None. This is an improvement on the existing Kelly system.
**Suggested test:** Compare uniform 0.25 sizing vs. ADX-dependent sizing.

---

## 4. Regime Detection & Filters

### 4.1 CUSUM Change-Point Detection for Regime Switching

**Problem:** Your LightGBM regime classifier is static (trained on historical data) and may have significant regime detection lag — by the time it detects a regime change, the market has already moved.

**Technique:** Add a CUSUM (Cumulative Sum) detector on ATR or daily returns to detect regime changes in real-time with minimal lag. Trigger a regime re-evaluation when CUSUM exceeds a threshold.

```python
def cusum_detector(series, threshold=1.0, drift=0.5):
    """Returns index of change points using CUSUM algorithm."""
    s_high = 0
    s_low = 0
    change_points = []
    mean = series.mean()
    
    for i, val in enumerate(series):
        s_high = max(0, s_high + (val - mean - drift))
        s_low = min(0, s_low + (val - mean + drift))
        
        if s_high > threshold or s_low < -threshold:
            change_points.append(i)
            s_high = 0
            s_low = 0
            # Re-estimate mean after change point
            if i + 20 < len(series):
                mean = series[i:min(i+20, len(series))].mean()
    
    return change_points
```

**Academic basis:** Dani (2026, Arizona State) shows CUSUM-augmented HMM significantly reduces regime detection lag. Chan Hock Peng (NUS) provides theoretical optimality bounds for scan-CUSUM.
**Expected impact:** MEDIUM — faster regime detection means the strategy adapts more quickly to changing conditions.
**Implementation complexity:** MEDIUM — requires maintaining CUSUM state.
**Conflict check:** Complements the existing LightGBM classifier rather than replacing it.
**Suggested test:** Run the CUSUM detector on ATR(14) values. Evaluate whether trades taken near detected change points perform differently.

### 4.2 ADX Trend Strength as Regime Proxy (Direct Approach)

**Problem:** The LightGBM 3-class regime classifier adds complexity but barely improves strategy outcomes. A simpler approach may work better.

**Technique:** Replace the ML classifier with direct ADX-based regime classification:
- **Trending (ADX > 25):** Full entry, Chandelier exit at 3.0x ATR
- **Transitioning (ADX 20-25):** Reduced size (50%), tighter Chandelier (2.0x ATR)
- **Ranging (ADX < 20):** Skip trades entirely, OR use tighter stops and quick exits

**Rationale:** ADX directly measures trend strength and is the natural regime indicator for a trend-following system. The ML classifier may be learning features that correlate with ADX anyway, but with more noise and overfitting risk.
**Expected impact:** MEDIUM — simpler, more interpretable, less overfitting risk.
**Implementation complexity:** LOW.
**Conflict check:** Your existing regime classifier may already implicitly use ADX. This replaces the ML approach with a direct rule.
**Suggested test:** Compare three variants: (A) no regime filter, (B) LightGBM regime, (C) ADX regime.

### 4.3 Monday Weakness / Friday Strength Filter

**Problem:** Day-of-week effects in gold are significant (Monday: -0.01% avg, Friday: +0.11% avg over 2004-2023).

**Technique:** Apply a day-of-week weight to entry decisions:
- **Monday:** Reduce position size to 50% of Kelly (weakest day)
- **Tuesday-Thursday:** Full size (best days for clean trends)
- **Friday:** Normal sizing (positive bias but watch for weekend position-squaring)

**Academic basis:** GLD analysis 2004-2023 showed avoiding Monday exposure would have improved cumulative returns from 330.8% to 412.1%.
**Expected impact:** LOW-MEDIUM — small additive improvement.
**Implementation complexity:** LOW.
**Conflict check:** None. Simple overlay on existing system.
**Suggested test:** Compare Monday-only trades vs. all-other-day trades. If Monday trades significantly underperform, reduce Monday sizing.

### 4.4 Halloween Effect for Gold (Winter vs. Summer Seasonality)

**Problem:** No seasonal awareness in the current strategy.

**Technique:** Gold shows strong seasonality:
- **Winter (November-April):** Average +8.10% for GLD — FULL trading
- **Summer (May-October):** Average +1.20% for GLD — REDUCED size (50%)

**Academic basis:** "In Gold We Trust Nugget 2024" confirms the Halloween effect in gold: 6.71 percentage point difference between winter and summer returns.
**Expected impact:** LOW-MEDIUM — reduces exposure during the weaker half of the year.
**Implementation complexity:** LOW.
**Conflict check:** None. Simple calendar-based adjustment.
**Suggested test:** Run the backtest split by winter/summer. If winter clearly outperforms, implement seasonal sizing.

---

## 5. Feature Engineering Ideas

| Priority | Feature | Description | Rationale |
|----------|---------|-------------|-----------|
| HIGH | ATR Percentile | ATR(14) as percentile of its own 100-bar history | Identifies whether current volatility is high/low relative to recent context |
| HIGH | Yang-Zhang Volatility Estimator | Uses O,H,L,C data for more efficient volatility estimate | Outperforms simple close-to-close volatility, captures intraday range expansion before close |
| MEDIUM | DXY Regime | Binned DXY trend (rising/falling/sideways) as categorical feature | Gold has inverse correlation with DXY; regime matters more than absolute level |
| MEDIUM | Gold-Silver Ratio | Ratio of XAU to XAG price | Silver leads gold in some market regimes; ratio changes often precede gold moves |
| MEDIUM | Turn of Month | Binary flag: first 3 trading days of month | Gold ETFs show significantly higher returns on day 1 of month |
| LOW | Kalman Filter Price | Kalman-filtered price as additional feature | Signal processing approach to reduce noise (KCA method) |
| LOW | Fourier Transforms | Low-frequency components of price series | Theoretical: captures cycles. Practical: high risk of overfitting |
| MEDIUM | Cumulative Volume Delta | Tick-volume based delta on M15 | Identifies institutional absorption vs. distribution at breakout levels |
| HIGH | Breakout Distance | Distance from close to Donchian band as % of ATR | Quantifies "how far" price has penetrated — larger penetration = stronger signal |
| MEDIUM | Efficiency Ratio | Kaufman Efficiency Ratio: net change / sum of absolute changes | Dynamically adjusts observation windows; SNR shows 62% correlation with CTA returns |

---

## 6. Papers & Resources

### Academic Papers

| Paper | Topic | Key Finding |
|-------|-------|-------------|
| **Fonseca (2026)** — "Point-in-Time Backtesting of Momentum-Trend Equity Strategies" *(Mathematics, MDPI)* [Link](https://econpapers.repec.org/article/gamjmathe/v_3a14_3ay_3a2026_3ai_3a12_3ap_3a2182-_3ad_3a1969961.htm) | ATR trailing stop analysis | ATR multiplier is a near-redundant parameter; region-based optimization preferred over point estimates |
| **Rodosthenous & Zhang (2020)** — "When to sell an asset amid anxiety about drawdowns" [Link](https://ar5iv.labs.arxiv.org/html/2006.00282) | Optimal trailing stops | Trailing stops emerge endogenously from investor preferences; first to derive them theoretically |
| **Dani (2026)** — "Bayesian Approaches to Sequential Decision-Making" *(Arizona State)* [Link](https://keep.lib.asu.edu/items/204979) | Vol-HMM + CUSUM regime detection | Sticky Prior Paradox; CUSUM reduces regime detection lag |
| **Scholz (2012)** — "Size matters! How position sizing determines risk and return" [Link](https://ideas.repec.org:443/p/zbw/cpqfwp/31.html) | Position sizing in trend following | No universal optimal f; fractional Kelly consistently improves Sharpe |
| **CFM (2018)** — "The convexity of trend-following" [Link](https://thehedgefundjournal.com/wp-content/uploads/2018/05/CFM_The-convexity-of-trend-following-Feb-2018.pdf) | CTA trend following properties | Trend following is mechanically convex; depends on variance across timescales |
| **Katz & McCormick (1998)** — "Barrier Stops and Trendlines" *(TAS&C)* [Link](http://traders.com/documentation/FEEDbk_docs/1998/07/Abstracts_new/Katz/Katz9807.html) | Exit strategy testing | Exit methods significantly impact performance; parameter sensitivity is critical |
| **Nousiainen** — "Mechanical Trading Systems" *(Theseus)* [Link](https://www.theseus.fi/bitstream/handle/10024/501798/Nousiainen_Petri.pdf) | Donchian breakout improvement | Dual exit condition (enter on 20-bar, exit on 10-bar); support/resistance filters |
| **arXiv 1701.03960** — "Optimal acquisition and liquidation thresholds" [Link](http://arxiv.org/pdf/1701.03960) | Combined TP + trailing stop | Combined strategy is theoretically optimal; higher trailing stop leads to earlier entry |

### Resources & Tools

| Resource | Description |
|----------|-------------|
| **Chandelier Exit** (TradingView) | Implementation reference for Chandelier Exit logic [Link](https://in.tradingview.com/script/S4WGeup3-Adaptive-Chandelier-Exit-MAB/) |
| **Volume Profile VA Breakout** (FTMO/OANDA) | Volume profile breakout confirmation [Link](https://ftmo.oanda.com/blog/master-volume-profile-trading-with-the-va-breakout-strategy/) |
| **Gold Session Map** (TradingView) | Smart Money Gold Map showing kill zones [Link](https://br.tradingview.com/script/5zAdLu8t-Smart-Money-Gold-Map-XAUUSD-Kill-Zones-Liquidity-Sweeps/) |
| **Gold Seasonality** (In Gold We Trust) | Calendar anomalies in gold [Link](https://ingoldwetrust.report/nuggets/calendar-anomalies-and-the-gold-market/) |

---

## Recommended Implementation Order

Based on impact/complexity ratio, implement in this order:

1. **Volatility Compression Filter** (Entry) — LOW complexity, HIGH impact. 1-2 days.
2. **Chandelier Exit** (Exit) — MEDIUM complexity, HIGH impact. 3-5 days.
3. **Partial TP at 1R + Breakeven + Chandelier Trail** (Exit) — MEDIUM complexity, HIGH impact. 3-5 days.
4. **H1 Trend Hard Gate** (Filter) — LOW complexity, MEDIUM-HIGH impact. 1 day.
5. **Volatility Scaling for Position Sizing** (Risk) — LOW complexity, MEDIUM impact. 1 day.
6. **Anti-Martingale Sizing** (Risk) — LOW complexity, MEDIUM impact. 1 day.
7. **ADX Regime-Dependent Sizing** (Risk) — LOW complexity, MEDIUM impact. 1 day.
8. **CUSUM Change-Point Detection** (Regime) — MEDIUM complexity, MEDIUM impact. 2-3 days.
9. **New Features (ATR Percentile, Yang-Zhang, Breakout Distance)** — LOW complexity. 1 day each.
10. **Day-of-Week & Seasonal Filters** — LOW complexity. 1 day.

## Conflict Matrix

| Change | Conflicts With | Rationale |
|--------|---------------|-----------|
| Chandelier Exit | Fixed 2R exit (replaces it) | This IS the replacement |
| Partial TP + Trail | Chandelier-only exit | Different philosophy; test both variants |
| H1 Trend Hard Gate | ML ensemble (overrides soft features) | Test with/without gate to see if ML still adds value |
| ADX Regime Filter | LightGBM regime classifier | Replace or augment; test both |
| Volatility Compression | Volume imbalance filter (different mechanism) | Compression is pre-breakout; imbalance is post-breakout; complementary |
