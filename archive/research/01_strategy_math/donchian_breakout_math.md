# Donchian Breakout: Mathematical Foundations

## 1. Definition

The Donchian Channel (20-bar) is defined as:

$$\begin{aligned}
\text{Upper}(t) &= \max(P_{t-19}, P_{t-18}, \ldots, P_t) \\[2pt]
\text{Lower}(t) &= \min(P_{t-19}, P_{t-18}, \ldots, P_t) \\[2pt]
\text{Middle}(t) &= \frac{\text{Upper}(t) + \text{Lower}(t)}{2}
\end{aligned}$$

The **D4 strategy** uses a 1-bar lagged breakout:

$$\begin{aligned}
\text{BUY Signal}(t) &: P_t > \max(P_{t-20}, P_{t-19}, \ldots, P_{t-1}) \\[2pt]
\text{SELL Signal}(t) &: P_t < \min(P_{t-20}, P_{t-19}, \ldots, P_{t-1})
\end{aligned}$$

Entry is at $t+1$ open, with stop at $2\sigma_{ATR}$ and target at $+2R$ (where $R = \text{entry} - \text{stop}$).

## 2. Statistical Properties Under Random Walk

### 2.1 Probability of a New High

Under i.i.d. Gaussian returns $P_t \sim \mathcal{N}(0, \sigma^2)$:

$$P(\text{new 20-bar high at } t) = \frac{1}{20}$$

For a 20-minute-interval process on M15, this gives:
- Expected signals per day (96 bars): $96 / 20 \approx 4.8$
- Observed signals in D4: ~15% of bars → **3x random expectation**

This 3:1 ratio is the empirical evidence for serial correlation.

### 2.2 Expected Pseudo-Breakout Continuation (Random Walk)

Under random walk, after a new 20-bar high at time $t$:

$$E[P_{t+1} - P_t] = 0$$

The probability of price being higher $k$ bars later:

$$P(P_{t+k} > P_t) = 0.5 \quad \text{(for any } k > 0\text{)}$$

So the observed 40% win rate (above 50% theoretical)... wait. The 40% win rate is at a **fixed 2R target**. Under random walk with zero drift, if we enter at time $t+1$ open:

$$P(\text{hit TP before SL}) = \frac{\text{SL distance}}{\text{TP distance} + \text{SL distance}} = \frac{2\sigma}{4\sigma} = 50\%$$

But the observed WR is ~40% — lower than random expectation. This means:
- **The fixed 2R target is too tight relative to the underlying distribution**
- There IS serial correlation, but the exit structure doesn't capture it optimally
- **Alternative exits (trailing, partial) would better exploit the serial correlation**

## 3. Breakout Momentum Decay

Under time series momentum, the expected return after a breakout follows a power law:

$$E[r_{t+k} | \text{signal}] \propto k^{-\alpha} \quad \alpha > 0$$

The edge decays with horizon. For M15 gold:
- Peak edge: bars 1-5 after entry
- Significant edge: bars 1-20
- Edge decays to noise: bars 20+

This supports both the time-based exit (max 96-192 bars) and the multi-partial exit strategy.

## 4. The Breakout Distance Metric

Define the **breakout strength** as:

$$D_{BUY}(t) = \frac{P_t - \text{Upper}(t-1)}{\sigma_{ATR}(t-1)}$$

For a valid breakout: $D_{BUY} > 0$. For a *strong* breakout: $D_{BUY} > 1.0$.

**Hypothesis:** The conditional expected return is proportional to breakout strength:

$$E[R_{t+k} | D_t = d] = \beta d + \epsilon \quad \beta > 0$$

This can be tested empirically: bin signals by $D$ value and compute mean R for each bin. If $\beta > 0$, only trade breakouts with $D$ above a threshold.

## 5. Donchian Parameter Optimization

### 5.1 Channel Length

The optimal channel length $L$ balances:
- **Short $L$:** More signals, more noise, lower WR
- **Long $L$:** Fewer signals, less noise, higher WR, higher opportunity cost

$$\text{Expected net profit}(L) = N(L) \times E[R(L)] - \text{costs}$$

Where $N(L)$ is signal count and $E[R(L)]$ is expected R per trade, both functions of $L$.

The current $L=20$ was chosen from backtesting. Session-adaptive $L$ (10 for Asia, 20 for London, 15 for NY) captures the different volatility regimes.

### 5.2 Dual Donchian Exit

Academic research (Nousiainen) suggests:
- Enter on 20-bar breakout
- Exit on 10-bar counter-breakout (exit long when price closes below 10-bar low)

This is mathematically equivalent to a trailing stop with variable distance proportional to the 10-bar range.

## 6. Expected Profit Decomposition

For the D4 strategy over 11 years:

$$\begin{aligned}
\text{Total PnL} &= \sum_{i=1}^{8175} \text{PnL}_i \\
&= \text{Trade Count} \times E[\text{PnL}] \\
&= 8175 \times \$5.22 \\
&= \$42,678
\end{aligned}$$

Decomposed by direction:
$$\begin{aligned}
\text{Long PnL} &: 4,079 \times \$5.20 \approx \$21,200 \\
\text{Short PnL} &: 4,096 \times \$5.25 \approx \$21,478
\end{aligned}$$

The symmetry suggests no directional bias in the signal — it exploits volatility more than directional trend.

## 7. The Math of Improving a Donchian System

### 7.1 Adding a Pullback Requirement

Instead of entering immediately, wait for a pullback within the channel:

$$\text{Entry} \iff \text{Breakout}(t-k) \ \text{AND} \ \text{Pullback}(t-k+1:t) \ \text{AND} \ \text{Resume}(t)$$

The pullback filter reduces signals by factor $\alpha$ but increases WR by factor $\beta$:

$$\text{Net expectancy} = \frac{N}{\alpha} \times \left( \beta \times WR_0 \times W - (1-\beta \times WR_0) \times L \right)$$

For $\alpha = 2$ (half the trades) and $\beta = 1.25$ (25% WR improvement):
$$\text{Net} = \frac{N}{2} \times (0.50 \times 2 - 0.50 \times 1) = \frac{N}{2} \times 0.50 = 0.25N$$

vs current: $N \times 0.20 = 0.20N$

**26% improvement in net expectancy**, even with half the trades.

### 7.2 Adding a Volatility Filter

If volatility-filtered signals have WR' and volatility-neutral signals have WR:

$$\text{Overall WR} = p \times WR' + (1-p) \times WR_0$$

Where $p$ is the proportion of compressed-volatility signals. If $p = 0.6$ (60% of eligible signals) and $WR' = 0.48$ vs $WR_0 = 0.28$:

$$\text{Overall WR} = 0.6 \times 0.48 + 0.4 \times 0.28 = 0.288 + 0.112 = 0.40$$

Same overall WR but from a different composition — the 0.48 WR trades run larger positions due to anti-martingale, improving the capital-weighted return.

### 7.3 Combined Expectancy Model

When multiple improvements are applied in parallel, the combined expectancy is:

$$E[R_{combined}] = WR_c \times W_c - (1-WR_c) \times L_c$$

Where subscript $c$ denotes combined parameters. Through the law of total probability:

$$WR_c = \int WR(\theta) \cdot f(\theta) \, d\theta$$

For discrete regimes (trending, ranging):

$$WR_c = p_{trend} \times WR_{trend} + p_{ranging} \times WR_{ranging}$$

If ADX regime detection identifies trending (65% accuracy) and ranging (45% accuracy) with prevalence 40%/60%:

$$WR_c = 0.40 \times 0.55 + 0.60 \times 0.35 = 0.22 + 0.21 = 0.43$$

**Modest improvement from regime awareness alone (40% → 43%).** The real gain comes from regime-dependent sizing (bet bigger in the 55% WR regime).

## 8. Key Formulas Summary

| Formula | Description |
|---------|-------------|
| $E[R] = WR \times W - (1-WR) \times L$ | Strategy expectancy |
| $PF = \frac{WR \times W}{(1-WR) \times L}$ | Profit factor |
| $N_{max\_streak} \approx \frac{\ln N}{\ln(1/(1-WR))}$ | Expected max losing streak |
| $D_{breakout} = \frac{P_t - \text{Upper}(t-1)}{\sigma_{ATR}}$ | Breakout strength |
| $E[R_{combined}] = \sum p_i E[R_i]$ | Multi-regime expectancy |
