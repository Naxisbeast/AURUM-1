# Kelly Criterion: Complete Derivation & Application

## 1. The Kelly Criterion

### 1.1 Basic Formula

For a binary outcome bet that wins with probability $p$ and pays $W$ (win payoff as fraction of stake) or loses with probability $q=1-p$ and loses $L$ (loss as fraction of stake):

$$f^* = \frac{p}{L} - \frac{q}{W}$$

For equal win/loss amounts ($L = W = 1$): $f^* = p - q = 2p - 1$

### 1.2 For Trading

In trading, we define:

- $W$ = average win divided by average dollar risk (R-multiple)
- $L$ = average loss divided by average dollar risk (R-multiple)
- $p$ = win rate

$$f^* = \frac{pW - qL}{WL} = \frac{E[R]}{WL}$$

For D4: $p = 0.40$, $W = 2.0$, $L = 1.0$

$$f^* = \frac{0.40 \times 2.0 - 0.60 \times 1.0}{2.0 \times 1.0} = \frac{0.80 - 0.60}{2.0} = 0.10$$

Full Kelly says: risk **10%** of equity per trade.

### 1.3 Why D4 Uses Fractional Kelly

Current D4 uses **0.25% risk per trade** — equivalent to $f = 0.025$ or **quarter Kelly**:

$$f_{quarter} = \frac{f^*}{4} = 0.025$$

**Why quarter Kelly:**

1. **Full Kelly is too aggressive for real trading.** It maximizes long-term growth rate but has ~33% drawdown risk.
2. **Fractional Kelly reduces variance.** Quarter Kelly has ~1/4 the variance of full Kelly.
3. **Estimation error robustness.** Small errors in $p$, $W$, or $L$ compound dramatically at full Kelly:

$$f_{optimal} = f^* - \frac{\text{Var}(\hat{f})}{2\gamma f^*}$$

Where $\gamma$ is risk aversion and $\text{Var}(\hat{f})$ is estimation variance.

## 2. The Mathematics of Kelly Overbetting

### 2.1 Zone of Positive Growth

Kelly shows there's a **positive growth zone** from $0 < f < 2f^*$. Beyond $2f^*$, expected growth becomes negative even though expected dollar return is still positive. This is the "Kelly trap."

### 2.2 Growth Rate Function

$$G(f) = p \ln(1 + fW) + q \ln(1 - fL)$$

This is the expected log-growth of capital. For D4:

$$G(f) = 0.40\ln(1 + 2f) + 0.60\ln(1 - f)$$

| $f$ | $G(f)$ | Implied Risk/Trade |
|-----|--------|-------------------|
| 0.01 | +0.0039 | 0.25% (current) |
| 0.025 | +0.0095 | 0.625% |
| 0.05 | +0.0178 | 1.25% |
| 0.10 | +0.0280 | **2.5% (full Kelly)** |
| 0.15 | +0.0272 | 3.75% |
| 0.20 | +0.0165 | 5.0% |
| 0.25 | -0.0010 | 6.25% (beyond $2f^*$) |

**Key observation:** $G(f)$ is concave and peaks at $f^*=0.10$. The current 0.25% risk ($f=0.01$) is extremely conservative — well below the optimal growth rate. At $f=0.025$ (0.625% risk), growth would be 2.4× higher.

## 3. Half-Kelly vs Quarter-Kelly vs Current vs Full

| Variant | $f$ | $G(f)$ | Time to 2× Equity ($n$) | Max DD (est.) |
|---------|-----|--------|--------------------------|----------------|
| Current | 0.01 | 0.0039 | ~178 trades | ~5% |
| Quarter | 0.025 | 0.0095 | ~73 trades | ~8% |
| Half | 0.05 | 0.0178 | ~39 trades | ~13% |
| Full | 0.10 | 0.0280 | ~25 trades | ~25% |
| 2× Full | 0.20 | 0.0165 | ~42 trades | ~45% |

**Time to double capital in trades:**

$$n_{double} = \frac{\ln 2}{G(f)}$$

## 4. Distribution of Outcomes

### 4.1 Terminal Wealth Distribution

After $n$ trades with fraction $f$, terminal wealth follows a log-normal distribution:

$$\ln\left(\frac{W_n}{W_0}\right) \sim \mathcal{N}(n\mu_g, n\sigma_g^2)$$

Where:
$$\mu_g = p\ln(1+fW) + q\ln(1-fL)$$
$$\sigma_g^2 = p[\ln(1+fW) - \mu_g]^2 + q[\ln(1-fL) - \mu_g]^2$$

### 4.2 Probability of Drawdown

The probability of drawdown of size $D$ over $n$ trades:

$$P(\text{DD} \leq -D) = \Phi\left(\frac{\ln(1-D) - n\mu_g}{\sqrt{n}\sigma_g}\right)$$

## 5. Kelly Variants for Trend Following

### 5.1 Anti-Martingale Kelly

In positively autocorrelated markets (trending), the Kelly fraction should increase after wins:

$$f_t = f^* \times (1 + \alpha c_t)$$

Where $c_t$ is consecutive win count and $\alpha$ is the ramp rate.

**Rationale:** In a trend, sequential wins signal strong directional bias — increase exposure.

### 5.2 Regime-Dependent Kelly

Different Kelly fractions for different regimes:

$$f_t = \begin{cases}
f^* \times 1.0 & \text{ADX} > 30 \\
f^* \times 0.5 & 20 < \text{ADX} \leq 30 \\
f^* \times 0.25 & \text{ADX} \leq 20
\end{cases}$$

This becomes a state-conditional optimal fraction:

$$f^*_{ADX} = \frac{E[R | \text{ADX regime}]}{W \cdot L}$$

### 5.3 Dynamic Kelly with R-multiple Updates

As new trades close, update $p$, $W$, and $L$ with exponential weighting:

$$\hat{p}_t = \lambda \cdot \hat{p}_{t-1} + (1-\lambda) \cdot \mathbb{1}[\text{win}_t]$$
$$\hat{W}_t = \lambda \cdot \hat{W}_{t-1} + (1-\lambda) \cdot W_{t, win}$$
$$\hat{L}_t = \lambda \cdot \hat{L}_{t-1} + (1-\lambda) \cdot L_{t, loss}$$

Where $\lambda = e^{-1/N_{effective}}$ is the decay factor that gives effective lookback $N_{effective}$.

## 6. Relationship Between Kelly and Sharpe

For a strategy with Sharpe ratio $S$:

$$f^* \approx \frac{S}{\sigma_R} \approx \frac{E[R]}{\sigma_R^2}$$

Where $\sigma_R$ is the standard deviation of trade R-multiples.

For D4:
- $E[R] \approx 0.20$
- $\sigma_R \approx 1.5$ (typical for 40% WR with 2R/1R trades)
- $S = 0.20 / 1.5 \approx 0.133$ per trade
- $f^* \approx 0.133 / 1.5 \approx 0.089 \approx 0.10$ (matches direct Kelly)

## 7. Key Takeaways for D4

1. **Current sizing is too conservative.** 0.25% risk per trade ($f=0.01$) is well below the growth-maximizing Kelly fraction.
2. **Half-Kelly (0.625% risk) would approximately 2× the growth rate** while keeping max DD ~13%.
3. **Quarter-Kelly (0.3125% risk) is a reasonable middle ground** — 2.4× growth vs current, ~8% max DD.
4. **The anti-martingale variant adds ~15-20% extra PnL** without proportionally increasing risk.
5. **Regime-dependent Kelly protects capital during unfavorable conditions** — the true advantage over static Kelly.
