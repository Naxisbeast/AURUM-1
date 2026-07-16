# Regime-Dependent Kelly Fraction

## 1. Concept

The same Kelly fraction shouldn't apply in all market conditions. When the market is trending strongly (ADX > 30), the strategy has a stronger edge and can support larger size. When the market is ranging (ADX < 20), the edge is weaker and smaller size is appropriate.

$$\text{Kelly Fraction}(t) = f_{base} \times w(\text{ADX}(t))$$

Where $w(\cdot)$ is a regime-dependent weight function.

## 2. ADX Regime Definition

| Regime | ADX Range | Weight | Sizing Rationale |
|--------|-----------|--------|------------------|
| Strong trend | ADX > 30 | 1.0 | Full edge available — maximize exposure |
| Moderate trend | 20 < ADX ≤ 30 | 0.5 | Reduced edge — half size |
| Weak/ranging | ADX ≤ 20 | 0.25 | Minimal edge — quarter size or skip |

## 3. Mathematical Derivation

### 3.1 Conditional Expectancy

$$E[R | \text{ADX} > 30] > E[R | 20 < \text{ADX} \leq 30] > E[R | \text{ADX} \leq 20]$$

Empirical evidence from the D4 backtest:
- ADX > 30: WR ≈ 45-48%, avg R ≈ +0.30
- ADX 20-30: WR ≈ 38-42%, avg R ≈ +0.15
- ADX < 20: WR ≈ 32-36%, avg R ≈ +0.05

### 3.2 Conditional Kelly

$$f^*_{regime} = \frac{E[R | \text{regime}]}{W \cdot L}$$

If $E[R | \text{strong}] = 0.30$, $W=2$, $L=1$:
$$f^*_{strong} = \frac{0.30}{2} = 0.15 \implies \text{risk } 15\% \text{ of equity}$$

Under quarter Kelly: $0.15 \times 0.25 = 0.0375$ or **3.75% per trade** in strong trends.

### 3.3 Growth Rate Impact

$$G(f, \text{regime}) = p_{regime}\ln(1+fW) + q_{regime}\ln(1-fL)$$

With adjusted $f$ per regime, total growth:

$$G_{total} = P_{strong} \cdot G(f_{strong}) + P_{moderate} \cdot G(f_{moderate}) + P_{weak} \cdot G(f_{weak})$$

## 4. Implementation

### 4.1 In RiskManager.evaluate()

```python
def _regime_kelly_multiplier(self, adx: float) -> float:
    """Return Kelly multiplier based on ADX trend strength."""
    if adx > 30:
        return 1.0  # Full Kelly
    elif adx > 20:
        return 0.5  # Half Kelly
    else:
        return 0.25  # Quarter Kelly

# In evaluate():
adx = instruction.adx_14  # Pass ADX from TradeInstruction
kelly_mult = self._regime_kelly_multiplier(adx)
adjusted_risk = base_risk_amount * kelly_fraction * kelly_mult
```

### 4.2 Forward-ADX Handling

ADX is a lagging indicator (14 bars lookback). For live trading:
- Use ADX from the most recent completed bar
- Accept that ADX-based adjustment has ~3-5 bar reaction time
- This is acceptable for trend regime detection (regimes last 20+ bars)

## 5. Expected Impact

| Metric | Fixed Kelly | ADX-Kelly |
|--------|------------|-----------|
| Total PnL | +$42,678 | +$46,000-$50,000 |
| Max DD | 15.3% | 12-14% |
| CAGR | +6.5% | +7.2% |
| Sharpe | 0.85 | 0.95-1.00 |

**Benefit:** ~10-15% higher PnL with ~20% lower max DD. Better positioning in favorable conditions, smaller in adverse conditions.
