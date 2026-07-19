# Derivation of Key Financial Metrics

## 1. Profit Factor (PF)

$$PF = \frac{\text{Gross Profit}}{\text{Gross Loss}} = \frac{\sum \text{Winning Trades}_i}{\sum |\text{Losing Trades}_i|}$$

**Expected value:** For a strategy with win rate $p$, avg win $W$, avg loss $L$:
$$PF \approx \frac{pW}{(1-p)L}$$
$$PF_{D4} \approx \frac{0.40 \times 2.0}{0.60 \times 1.0} = \frac{0.80}{0.60} \approx 1.33 \text{ (before costs)}$$
$$PF_{D4} = 1.14 \text{ (after costs)}$$

## 2. Sharpe Ratio

$$S = \frac{\bar{r} - r_f}{\sigma_r} \times \sqrt{N}$$

Where:
- $\bar{r}$ = mean return per period
- $r_f$ = risk-free rate (≈ 0 for short-term)
- $\sigma_r$ = standard deviation of returns
- $N$ = number of periods per year (96 × 365 for M15)

**Annualized Sharpe:** For the D4 backtest:
$$\text{Sharpe} = \frac{E[R_{daily}]}{\sigma_{R_{daily}}} \times \sqrt{252}$$

## 3. Sortino Ratio

$$Sortino = \frac{\bar{r} - r_f}{\sigma_{downside}} \times \sqrt{N}$$

$$\sigma_{downside} = \sqrt{\frac{1}{N} \sum_{r_i < 0} (r_i - \bar{r})^2}$$

Only penalizes negative volatility — more relevant for strategies with asymmetric return distributions.

## 4. Maximum Drawdown (MDD)

$$MDD = \min_{t} \frac{E_t - \max_{s \leq t} E_s}{\max_{s \leq t} E_s}$$

Where $E_t$ is equity at time $t$.

## 5. Calmar Ratio

$$Calmar = \frac{CAGR}{MDD}$$

Measures return per unit of drawdown risk. Higher is better.

## 6. R-Multiple Statistics

$$R_i = \frac{PnL_i}{Risk_i}$$

Where $Risk_i = |\text{Entry Price}_i - \text{SL Price}_i| \times \text{Units}_i \times \text{Unit Value}$

| R-Statistic | Formula | Interpretation |
|-------------|---------|----------------|
| Mean R | $\bar{R} = \frac{1}{n}\sum R_i$ | Expected return per risk unit |
| Median R | $\text{median}(R)$ | Robust central tendency |
| Capital-Weighted R | $\frac{\sum PnL_i}{\sum Risk_i}$ | Dollar-volume weighted edge |
| Maximum R | $\max(R_i)$ | Best single trade |
| Minimum R | $\min(R_i)$ | Worst single trade |

## 7. Kelly Criterion

$$f^* = \frac{pW - (1-p)L}{W \cdot L}$$

For D4: $f^* = \frac{0.40 \times 2.0 - 0.60 \times 1.0}{2.0 \times 1.0} = \frac{0.20}{2.0} = 0.10$

**Interpretation:** Risk 10% of capital per trade for maximum long-term growth.

## 8. Strategy Efficiency Ratio

$$Efficiency = \frac{Sharpe}{MDD \times \sqrt{TradeCount}}$$

Higher = better risk-adjusted returns. Useful for comparing strategies with different trade frequencies.
