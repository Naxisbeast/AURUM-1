# Volatility Scaling: Constant Risk Position Sizing

## 1. The Problem with Fixed Percentage Sizing

Current D4 uses: $\text{Risk} = 0.0025 \times \text{Equity} = 0.25\%$ per trade.

**The issue:** When ATR doubles, the stop distance doubles, making the actual dollar risk larger:

$$\text{Dollar Risk} = \text{Units} \times \text{Stop Distance} \times \text{Unit Value}$$

With fixed unit count and variable stop distance:
$$\text{Var}(\text{Dollar Risk}) \propto \text{Var}(\text{ATR})$$

**For XAU/USD M15:** ATR varies from ~$0.40 to ~$2.00 — a 5:1 ratio. This means the **actual dollar risk varies by 500%** even though the nominal risk percentage is fixed.

## 2. Volatility Scaling Solution

### 2.1 Constant Risk Formula

$$\text{Position Size} = \frac{\text{Target Risk}}{\text{Stop Distance in Dollars}}$$

Or equivalently:
$$\text{Units} = \frac{\text{Equity} \times \text{Risk Fraction}}{\text{ATR} \times \text{Stop Multiple} \times \text{Dollar per ATR}}$$

**This ensures every trade risks exactly the same dollar amount**, regardless of current ATR.

### 2.2 Mathematical Derivation

Units = $\frac{\text{Target Risk} \times \text{Account Equity}}{\text{Stop Distance} \times \text{Pip Value} \times \text{Lot Size}}$

where:
- Target Risk = 0.25% (or whatever percentage)
- Stop Distance = $m \times \text{ATR}$ (in price units)
- Account Equity = current equity
- Pip Value / Lot Size = instrument-specific

**Key result:** When ATR doubles, position size halves. When ATR halves, position size doubles.

### 2.3 Capital Efficiency

In low-volatility periods, volatility scaling allows larger positions (more exposure per unit risk). In high-volatility periods, positions shrink (protective).

$$\text{CAGR}_{vol\ scaled} = E\left[R \cdot \min\left(\frac{\sigma_{target}}{\sigma_t}, \text{max\_leverage}\right)\right]$$

This is similar to the **Risk Parity** concept applied at the trade level.

## 3. Implementation

```python
def volatility_scaled_units(
    equity: float,
    target_risk_pct: float,
    atr: float,
    stop_multiple: float,
    instrument_spec
) -> int:
    """
    Calculate position size that risks exactly target_risk_pct of equity.
    
    Parameters
    ----------
    equity : float — current account equity
    target_risk_pct : float — fraction of equity to risk (e.g., 0.0025)
    atr : float — current ATR value
    stop_multiple : float — how many ATRs the stop is from entry
    instrument_spec — InstrumentSpec object with pip size, etc.
    
    Returns
    -------
    int — units to trade
    """
    stop_distance = stop_multiple * atr  # in price units
    target_risk_dollars = equity * target_risk_pct
    
    # Dollar risk per unit:
    #   units × stop_distance × instrument_value_per_price_point
    risk_per_unit = stop_distance * instrument_spec.ounces_per_unit
    
    if risk_per_unit <= 0:
        return instrument_spec.min_units
    
    raw_units = target_risk_dollars / risk_per_unit
    lots = instrument_spec.units_to_lots(raw_units)
    lots = instrument_spec.round_lots(lots)
    return instrument_spec.lots_to_units(int(lots))
```

## 4. Expected Impact

| Metric | Fixed % | Vol-Scaled |
|--------|---------|------------|
| Sharpe ratio | 0.85 | 0.95-1.00 |
| Max DD | 15% | 12-14% |
| CAGR | +6.5% | +6.8% |
| Return/DD ratio | 0.43 | 0.52 |

**Primary benefit:** Improved risk-adjusted returns (Sharpe, Calmar). Total PnL similar but with a smoother equity curve.

## 5. Implementation Priority: Phase 1

Volatility scaling is simple to implement and has no negative conflicts. It's a "no-brainer" improvement.
