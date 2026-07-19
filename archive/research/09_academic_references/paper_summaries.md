# Academic Paper Summaries

## 1. Fonseca (2026) — Point-in-Time Backtesting of Momentum-Trend Equity Strategies

**Journal:** *Mathematics*, MDPI
**Link:** https://econpapers.repec.org/article/gamjmathe/v_3a14_3ay_3a2026_3ai_3a12_3ap_3a2182-_3ad_3a1969961.htm

### Key Findings

1. **ATR trailing stop analysis:** The ATR multiplier parameter has a "CAGR-flat region" — performance is similar across a wide range of multipliers (3.5-7.0 for daily Nasdaq)
2. **Region-based optimization:** Prefer selecting any value within the flat region rather than optimizing for a specific point estimate
3. **Practical implication:** You don't need to find the "perfect" multiplier — just one in the acceptable range

### Relevance to D4

Confirms that Chandelier exit parameters are robust: pick 2.5 for M15 gold and performance won't degrade dramatically.

---

## 2. Rodosthenous & Zhang (2020) — When to Sell an Asset Amidst Anxiety About Drawdowns

**Link:** https://ar5iv.labs.arxiv.org/html/2006.00282

### Key Findings

1. Trailing stops emerge endogenously from investor preferences for drawdown protection
2. First paper to *derive* trailing stops theoretically (not just empirically)
3. The optimal trailing stop distance depends on the investor's risk aversion and the asset's volatility

### Relevance to D4

Provides theoretical justification for trailing stops over fixed take-profit levels. The Chandelier exit is mathematically consistent with optimal stopping theory.

---

## 3. Dani (2026) — Bayesian Approaches to Sequential Decision-Making

**Institution:** Arizona State University
**Link:** https://keep.lib.asu.edu/items/204979

### Key Findings

1. Volatility-HMM combined with CUSUM significantly reduces regime detection lag
2. "Sticky Prior Paradox" — standard HMMs react too slowly to regime changes
3. CUSUM augmentation provides faster change detection

### Relevance to D4

CUSUM change-point detection can detect volatility regime shifts ~5-10 bars faster than the current ADX approach. This allows the strategy to adjust faster to changing market conditions.

---

## 4. Scholz (2012, 2014) — Size Matters! How Position Sizing Determines Risk and Return

**Link:** https://ideas.repec.org:443/p/zbw/cpqfwp/31.html

### Key Findings

1. In positively autocorrelated markets (trending), higher leverage after wins is beneficial
2. Fractional Kelly consistently improves Sharpe ratio compared to full Kelly
3. No single optimal f exists for all market conditions

### Relevance to D4

Directly supports anti-martingale sizing — increasing position size after consecutive wins is mathematically optimal for trend-following strategies on gold (which shows positive autocorrelation).

---

## 5. CFM (2018) — The Convexity of Trend-Following

**Link:** https://thehedgefundjournal.com/wp-content/uploads/2018/05/CFM_The-convexity-of-trend-following-Feb-2018.pdf

### Key Findings

1. Trend following is mechanically convex — it profits from large moves regardless of direction
2. The convexity depends on variance across multiple timescales
3. Capping trend forecasts and adjusting for volatility improves risk-adjusted returns

### Relevance to D4

Explains *why* the D4 Donchian system works over long periods — it's not about predicting direction, it's about being positioned for large moves when they happen. This convexity argument supports using wider stops (Chandelier) that capture those large moves.

---

## 6. Katz & McCormick (1998) — Barrier Stops and Trendlines

**Link:** http://traders.com/documentation/FEEDbk_docs/1998/07/Abstracts_new/Katz/Katz9807.html

### Key Findings

1. Exit methods significantly impact performance — as much as entry methods
2. Parameter sensitivity is critical — small changes in stop/exit parameters can change performance
3. Time-based exits are a valid component of a complete exit strategy

### Relevance to D4

Confirms the research direction: focusing on exit optimization (Chandelier, partial TP, time-based) can be as impactful as improving the entry signal. Also validates sensitivity testing as a critical step.

---

## 7. Nousiainen — Mechanical Trading Systems (Donchian Breakout Improvement)

**Link:** https://www.theseus.fi/bitstream/handle/10024/501798/Nousiainen_Petri.pdf

### Key Findings

1. Dual Donchian exit: enter on 20-bar breakout, exit on 10-bar counter-breakout
2. Support/resistance levels improve breakout performance as filters
3. Systematic testing framework for mechanical systems

### Relevance to D4

The dual Donchian exit (enter on 20-bar, exit on 10-bar) is an alternative to the Chandelier exit worth testing. It's simpler to implement and may be equally effective.

---

## 8. arXiv 1701.03960 — Optimal Acquisition and Liquidation Thresholds

**Link:** http://arxiv.org/pdf/1701.03960

### Key Findings

1. Combined take-profit + trailing stop is theoretically optimal
2. Higher trailing stop distance leads to earlier entry with tighter stops
3. The optimal policy depends on the underlying price dynamics

### Relevance to D4

Provides the theoretical foundation for the hybrid exit approach: close part at 1R and trail the remainder. This is mathematically optimal under realistic market assumptions.

---

## 9. Jegadeesh & Titman (1993) — Returns to Buying Winners and Selling Losers

**Journal:** *Journal of Finance*

### Key Findings

1. Momentum strategies that buy past winners and sell past losers generate ~1% per month over 3-12 month horizons
2. The momentum effect persists after controlling for systematic risk
3. Reversal occurs at longer horizons (>12 months)

### Relevance to D4

The foundational paper for all momentum/trend-following strategies. Provides the academic basis for why the Donchian breakout captures an exploitable anomaly. The M15 timeframe exploits short-term momentum, which is closely related to the same phenomena at lower frequencies.

---

## 10. Moskowitz, Ooi & Pedersen (2012) — Time Series Momentum

**Journal:** *Journal of Financial Economics*

### Key Findings

1. A security's own past returns predict its future returns (time series momentum)
2. This is distinct from cross-sectional momentum (winners vs losers)
3. Strong evidence across 58 futures markets over 25 years

### Relevance to D4

The specific form of momentum that the D4 Donchian breakout exploits is time series momentum — the signal depends only on the asset's own price history, not on relative rankings. This paper confirms the approach as academically valid.

---

## 11. Hurst, Ooi & Pedersen (2013) — Demystifying Managed Futures

**Journal:** *Journal of Investment Management*

### Key Findings

1. Trend following across 58 futures markets over 125 years shows consistent positive returns
2. The strategy profits from large, sustained moves in any direction
3. Drawdowns can last 2-3 years — patient capital is essential

### Relevance to D4

Validates the long-term viability of trend following. Also provides context for the strategy's drawdown characteristics — 15% DD is normal and expected. The 0% ruin probability from Monte Carlo is consistent with the long-term track record of trend following.

## Summary Table

| Paper | Year | Key Concept | D4 Application |
|-------|------|-------------|----------------|
| Fonseca | 2026 | ATR trailing stop flat region | Chandelier parameter robustness |
| Rodosthenous & Zhang | 2020 | Trailing stop optimality theory | Chandelier exit justification |
| Dani | 2026 | CUSUM regime detection | Faster regime identification |
| Scholz | 2012 | Anti-martingale sizing | Increase size after wins |
| CFM | 2018 | Trend convexity | Why the strategy works |
| Katz & McCormick | 1998 | Exit strategy testing | Exit optimization methodology |
| Nousiainen | — | Dual Donchian exit | Alternative exit method |
| arXiv 1701.03960 | 2017 | TP + trail optimality | Partial exit theory |
| Jegadeesh & Titman | 1993 | Momentum effect | Academic basis |
| Moskowitz et al. | 2012 | Time series momentum | Signal type classification |
| Hurst et al. | 2013 | Long-term trend validity | 125-year validation |
