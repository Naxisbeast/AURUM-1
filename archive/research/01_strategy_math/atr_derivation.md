# Average True Range (ATR): Complete Mathematical Derivation

## 1. True Range Definition

The True Range (TR) for bar $t$ is the maximum of three price range measures:

$$TR_t = \max\left(\;
    H_t - L_t,\;
    |H_t - C_{t-1}|,\;
    |L_t - C_{t-1}|\;
\right)$$

Where $H_t$, $L_t$, $C_t$ are the high, low, and close of bar $t$ respectively.

### Intuition

The three components capture:
1. **H_t - L_t:** The bar's own range (normal movement)
2. **|H_t - C_{t-1}|:** Gap up from previous close (overnight/weekend gap)
3. **|L_t - C_{t-1}|:** Gap down from previous close

TR captures **total price volatility** including gaps, not just intra-bar movement.

## 2. ATR Calculation (Wilder's Method)

$$\text{ATR}_t = \begin{cases}
\frac{1}{14}\sum_{i=1}^{14} TR_i & t = 14 \\[6pt]
\text{ATR}_{t-1} + \frac{TR_t - \text{ATR}_{t-1}}{14} & t > 14
\end{cases}$$

### 2.1 Exponential Moving Average Form

Wilder's ATR is equivalent to:

$$\text{ATR}_t = \lambda \cdot TR_t + (1-\lambda) \cdot \text{ATR}_{t-1}$$

Where $\lambda = 1/14$ (the smoothing constant).

### 2.2 Effective Lookback

The effective lookback of an EMA with smoothing $\lambda$:

$$N_{effective} = \frac{2}{\lambda} - 1 = \frac{2}{1/14} - 1 = 28 - 1 = 27$$

So ATR(14) actually has an effective memory of ~27 bars. The most recent bars have the highest weight:

$$\text{Weight of bar } t-k = \lambda(1-\lambda)^{k-1}$$

| Lag | Weight |
|-----|--------|
| 0 (current) | 7.1% |
| 1 | 6.6% |
| 2 | 6.2% |
| 5 | 5.0% |
| 10 | 3.5% |
| 20 | 1.7% |

## 3. Statistical Properties

### 3.1 Distribution of TR

For XAU/USD M15 with typical spread of 1.5 pips ($\$0.15$):

$$TR_t \sim \text{LogNormal}(\mu, \sigma)$$

Typical values:
- Median: ~$0.80 (8 pips)
- Mean: ~$0.90 
- Std Dev: ~$0.40
- 95th percentile: ~$1.80

### 3.2 ATR as Volatility Estimate

$$\hat{\sigma}_{ATR} = \frac{\text{ATR}}{k}$$

Where $k$ is a scaling factor. For Gaussian returns, $k \approx \sqrt{2/\pi} \approx 0.798$. This gives:

$$\hat{\sigma}_{daily} = \frac{\text{ATR}_{M15}}{0.798} \times \sqrt{\frac{96\text{ bars}}{1\text{ day}}} \approx \frac{\text{ATR}_{M15}}{0.798} \times 9.80$$

This converts M15 ATR to a daily volatility estimate.

### 3.3 ATR vs Standard Deviation

Standard deviation: $\sigma = \sqrt{\frac{1}{n}\sum(r_i - \bar{r})^2}$ — measures dispersion around mean
ATR: measures average absolute range — captures total movement magnitude

For M15 gold:
- Daily return std dev: ~0.65%
- Daily ATR: ~0.80%
- Ratio ATR/$\sigma$: ~1.23

ATR is systematically higher because it captures intra-bar extremes, not just close-to-close returns.

## 4. ATR Percentile

$$\text{ATR Percentile}(t) = \frac{\text{rank}(\text{ATR}_t \text{ in last 100})}{100}$$

This identifies whether current volatility is high/low relative to recent history:
- $<0.20$: Extremely low volatility (potential breakout setup)
- $>0.80$: Extremely high volatility (potential exhaustion)
- $0.30-0.70$: Normal volatility

For the volatility compression filter: require $\text{ATR Percentile}(t-1) < 0.40$ before taking a breakout signal.

## 5. Stop Distance Using ATR

### 5.1 Fixed Multiple Stop

$$\text{Stop Distance} = m \times \text{ATR}_t$$

Current D4 uses $m=2$. The optimal $m$ depends on:
- Desired win rate
- Volatility regime
- Trading horizon

### 5.2 ATR Stop Hit Probability

Under the assumption that price moves are normally distributed with ATR-based standard deviation, the probability of a stop being hit within $n$ bars:

$$P(|P_{t+k} - P_t| > m \cdot \text{ATR}_t) = 2 \cdot \Phi\left(-\frac{m \cdot \text{ATR}_t}{\hat{\sigma}_t \sqrt{k}}\right)$$

Where $\hat{\sigma}_t = \text{ATR}_t \times \sqrt{1/\pi}$ (converting ATR to std dev).

For $m=2$ over 48 bars:
$$P = 2 \cdot \Phi\left(-\frac{2}{\sqrt{1/\pi} \cdot \sqrt{48}}\right) = 2 \cdot \Phi\left(-\frac{2}{0.564 \times 6.93}\right) = 2 \cdot \Phi(-0.512) \approx 60.8\%$$

This suggests a ~60% chance of hitting either stop or target within 48 bars under random walk — explaining the observed 40% win rate (since target is at +2R = +4 ATR, further away than the -2 ATR stop).

## 6. Chandelier Exit Using ATR

$$\text{Chandelier Stop}(t) = \max_{k \leq t}(\text{Entry}, H_{entry:k}) - m \cdot \text{ATR}_t$$

The maximum distance from the highest high since entry is proportional to current ATR, making the stop:
- Wider in high volatility (protects against noise)
- Tighter in low volatility (protects gains)

This is the **key advantage** over fixed-distance stops: the stop adapts to market conditions.

## 7. ATR for Position Sizing (Volatility Scaling)

$$\text{Position Size} = \frac{\text{Account Equity} \times \text{Risk Per Trade}}{\text{Entry} - \text{Stop}}$$

With $|\text{Entry} - \text{Stop}| = m \cdot \text{ATR}$:

$$\text{Position Size} \propto \frac{1}{\text{ATR}}$$

In high volatility: smaller position. In low volatility: larger position. This equalizes **dollar risk** per trade regardless of volatility regime.

## 8. Common ATR Multiplier Values

| Multiplier | Use Case | Stop Tightness |
|------------|----------|----------------|
| 1.0 | Very tight (scalping) | Extreme |
| 1.5 | Day trading stops | Very tight |
| 2.0 | **D4 current SL/TP** | **Medium** |
| 2.5 | Chandelier exit (medium) | Loose |
| 3.0 | Chandelier exit (standard) | Standard |
| 4.0 | Trend following (wide) | Wide |

## 9. Yang-Zhang Volatility vs ATR

Yang-Zhang uses open, high, low, and close for a more efficient volatility estimate:

$$\sigma_{YZ}^2 = \sigma_{OH}^2 + \sigma_{CO}^2 + (1-k)\sigma_{HL}^2$$

Where:
- $\sigma_{OH}^2$ = variance of open-to-close
- $\sigma_{CO}^2$ = variance of close-to-close
- $\sigma_{HL}^2$ = variance of high-low
- $k$ is chosen to minimize estimation error

This is 9.5x more efficient than the simple close-to-close estimator and captures the full range data like ATR, but with better statistical properties.
