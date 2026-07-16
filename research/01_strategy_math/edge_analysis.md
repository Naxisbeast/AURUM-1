# Edge Analysis: Where Does AURUM-1's Profit Come From?

## 1. Conceptual Framework

A trading strategy has a **positive expected edge** if:

$$E[R] = P(win) \times E[R|win] + P(loss) \times E[R|loss] > 0$$

Where $R$ is the risk-multiple (return relative to amount risked).

### Current D4 Edge Decomposition

| Metric | Value |
|--------|-------|
| Win Rate (WR) | ~40% |
| Average Win (R) | ~+2.0R (fixed 2R TP minus costs) |
| Average Loss (R) | ~-1.0R (fixed SL minus costs) |
| **Expectancy (E[R])** | **0.40 × 2.0 + 0.60 × (-1.0) = +0.20R** |
| Profit Factor | 1.14 (≈ (0.40×2.0) / (0.60×1.0) = 0.80/0.60 = 1.33 before costs) |

**Key insight:** The raw edge before costs is ~0.33R per trade. After spread (1.5 pips), slippage (0.5 pips), and commissions, the realized edge drops to ~0.20R. Costs consume ~40% of the gross edge — this is typical for a ~40% win-rate strategy on M15.

## 2. The Trend-Following Edge

### 2.1 Why Trend Following Works

Trend following exploits **positive serial correlation** in financial returns:

$$\text{Cov}(r_t, r_{t+1}) > 0 \quad \text{during trend regimes}$$

For XAU/USD gold, several structural factors produce this serial correlation:
- **Central bank intervention** — sustained policy actions over weeks/months
- **Institutional order flow** — large players execute over days, creating persistent directional pressure
- **Momentum herding** — breakout attracts followers, which extends the move
- **Stop-loss cascades** — once stops break, accelerated price movement follows

### 2.2 The Donchian Channel as a Serial Correlation Detector

The Donchian 20-bar breakout signal is:

$$\text{Signal}_{BUY}(t) = \mathbb{1}[P_t > \max(P_{t-20:t-1})]$$

This fires when price has risen faster than at any point in the last 20 bars. Under the null hypothesis of no serial correlation (random walk), the probability of making a new 20-bar high is approximately $1/20 = 5\%$ per bar. The observed signal frequency (~15%) suggests clustering — which is the edge being exploited.

### 2.3 Mathematical Structure of the Edge

The Donchian breakout captures the **momentum effect**:

$$E[ r_{t+k} \ | \ \text{signal}_t ] > E[r_{t+k}] \quad \text{for } k > 0$$

This has been documented extensively:
- **Jegadeesh & Titman (1993):** Momentum profitable over 3-12 month horizons
- **Moskowitz, Ooi & Pedersen (2012):** "Time series momentum" — a security's own past returns predict its future returns
- **Hurst, Ooi & Pedersen (2013):** Trend following across 58 futures markets over 125 years

For M15 gold specifically, the edge comes from **short-term time series momentum** at the 5-20 bar horizon.

## 3. Edge Degradation Sources

### 3.1 Costs

Current cost structure (per trade):
$$\text{Cost} = \text{Spread} + \text{Slippage} = 2 \times 1.5 \text{ pips} + 0.5 \text{ pips} = 3.5 \text{ pips}$$

For gold on M15:
- 1 pip = $0.10 per ounce (standard)
- At typical position size (~1 oz per $10k equity), cost ≈ $0.35/trade
- Average trade PnL ≈ $5.22 ($42,678 / 8,175 trades)
- Cost ratio: $0.35 / $5.22 ≈ **6.7% of gross PnL**

This seems low, but losses compound: costs turn gross PF of ~1.33 into net PF of 1.14.

### 3.2 Win Rate Asymmetry

At 40% win rate, the strategy has a **max 25 consecutive loss streak** under binomial probability:

$$P(\text{losing streak} \geq k) = (1-WR)^k \times WR$$
$$P(\text{losing streak} \geq 10) = 0.6^{10} \times 0.4 \approx 0.24\%$$

Expected longest losing streak over 8,175 trades:
$$\approx \frac{\ln(8175)}{\ln(1/(1-WR))} \approx \frac{9.01}{\ln(2.5)} \approx 9.8 \text{ trades}$$

Monte Carlo (10k sims) confirmed max DD of ~15-20%, with 0% ruin probability.

## 4. Where to Find More Edge

### 4.1 Improve Win Rate (Primary Lever)

| Improvement | Target WR | New Expectancy | PF Target |
|-------------|-----------|----------------|-----------|
| Current | 40% | +0.20R | 1.14 |
| Volatility filter | 45% | +0.35R | 1.31 |
| Pullback entry | 50% | +0.50R | 1.50 |
| Chandelier exit | 48% | +0.44R | 1.43 |
| Combined | 52% | +0.56R | 1.60 |

**Mathematical derivation:** For a strategy paying W on wins and losing L on losses:

$$E[R] = WR \times W - (1-WR) \times L$$

If $W=2$ and $L=1$, then:
$$E[R] = 2WR - (1-WR) = 3WR - 1$$

Solving for breakeven ($E[R]=0$): $WR = 1/3 = 33.3\%$
Solving for PF=1.50: $E[R] = 0.50 \times 2 - 0.50 \times 1 = 0.50$ → $WR = 50\%$

**Every percentage point improvement in WR adds 0.03R to expectancy.**

### 4.2 Improve Win Magnitude (Exit Optimization)

Current: Fixed 2R exits. Constrained: all winners capped at 2R.

If a trailing exit captures a 3.5R average winner (vs 2R fixed):
$$\text{New expectancy} = 0.40 \times 3.5 + 0.60 \times (-1.0) = +0.80R$$

This is more powerful than improving WR alone — which is why Chandelier exit is the #1 recommendation.

### 4.3 Reduce Loss Magnitude (Early Exit)

If winners and losers can be identified early:
- Close losers early at -0.5R (before they hit -1.0R)
- Keep winners to run

$$\text{New expectancy} = 0.40 \times 2.0 + 0.60 \times (-0.5) = +0.50R$$

The breakeven stop achieves a variant of this by eliminating losing trades after a favorable move.

## 5. Edge Over Multiple Horizons

The strategy's edge is not constant — it varies with:

1. **Volatility regime:** Edge increases in high-volatility trending periods
2. **Time of year:** Gold shows seasonal patterns (winter > summer)
3. **Macro environment:** DXY trend, real yields regime
4. **Market structure:** Institutional positioning (COT), open interest

A robust strategy exploits edge when it exists and reduces exposure when edge is absent. This is the foundation for regime-dependent sizing and switching.

## 6. Maximum Theoretical Capacity

Given:
- Average trade duration: ~48 bars (estimated)
- Annual trades: ~743 (8,175 / 11)
- Average market depth at M15 gold: ~500-1000 contracts at best bid/offer
- Current size: ~1 oz per trade

**Capacity estimate:** Strategy can scale to ~100x current size (100 oz) before market impact degrades edge at M15. This is not a near-term constraint.

## 7. Key Takeaways

1. **Current edge is real but modest** — $0.20 per $1 risked
2. **Costs consume 40% of gross edge** — any improvement must either increase gross edge or reduce cost impact
3. **Exit optimization is the highest-leverage change** — improving average win from 2R to 3R+ adds more expectancy than improving WR from 40% to 50%
4. **The strategy's edge is NOT constant** — regime-aware variations can significantly improve risk-adjusted returns
5. **Combined improvements are multiplicative, not additive** — improving WR AND win magnitude simultaneously creates a compound effect
