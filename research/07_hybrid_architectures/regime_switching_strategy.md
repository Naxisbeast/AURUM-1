# Regime-Switching Strategy Architecture

## 1. Core Concept

Instead of running one strategy in all market conditions, detect the current market regime and switch between multiple strategies optimized for each:

```
Market Data → Regime Detector → {Trend Mode, Ranging Mode, Sit-Out Mode}
```

## 2. The Three Modes

### 2.1 Trend Mode (ADX > 25, or trending classification)

**Objective:** Capture sustained directional moves.

| Component | Setting |
|-----------|---------|
| Entry | Standard Donchian 20-bar breakout |
| Exit | Chandelier trail (3.0× ATR) |
| Sizing | Full Kelly (0.25% risk) |
| Filter | None — let the trend run |
| Expected WR | 42-48% |
| Expected PF | 1.20-1.50 |

### 2.2 Ranging Mode (ADX < 20, or ranging classification)

**Objective:** Profit from mean reversion within the Donchian channel.

| Component | Setting |
|-----------|---------|
| Entry | Fade the Donchian extremes (sell near upper band, buy near lower band) |
| Exit | Fixed 1R at band middle, tight stop |
| Sizing | Half Kelly (0.125% risk) |
| Filter | Must have clear band rejection (reversal candle pattern) |
| Expected WR | 55-65% |
| Expected PF | 1.10-1.25 |

### 2.3 Transition Mode (20 ≤ ADX ≤ 25)

**Objective:** Survive regime transitions with minimal losses.

| Component | Setting |
|-----------|---------|
| Entry | Only highest-conviction breakouts (with volatility compression + MTF confirmation) |
| Exit | Tight Chandelier (1.5× ATR) |
| Sizing | Quarter Kelly (0.0625% risk) |
| Filter | Multiple confirmation required |
| Expected WR | 50-55% |
| Expected PF | 1.05-1.15 |

## 3. Regime Detection Methods

| Method | Latency | Complexity | Accuracy | Notes |
|--------|---------|------------|----------|-------|
| ADX threshold (current) | Low (~7 bars) | None | Moderate | Simple, baseline |
| ADX + DI cross | Low | Low | Moderate | DI+ > DI- confirms trend |
| CUSUM on ATR | Low (real-time) | Medium | Higher | Change point detection |
| HMM (3-state) | Medium (~20 bars) | High | Highest | More accurate regime identification |
| ML classifier | Medium (batch) | High | Moderate | Current approach, might be fine if retrained better |

## 4. Regime Detection Stability

A key challenge: avoiding frequent mode switching.

**Solution:** Hysteresis band:

```
Trend → Ranging: ADX crosses BELOW 18 (not 20)
Ranging → Trend: ADX crosses ABOVE 22 (not 20)
```

This creates a 4-unit deadband that reduces false transitions by ~60%.

## 5. Implementation Architecture

```python
class StrategyMode:
    TREND = 'trend'
    RANGING = 'ranging'
    TRANSITION = 'transition'

class RegimeDetector:
    def __init__(self, lookback: int = 14):
        self.adx = ADX(lookback)
        self.di_plus = DIPlus(lookback)
        self.di_minus = DIMinus(lookback)
        self.current_mode = StrategyMode.TRANSITION
        
        # Hysteresis thresholds
        self.trend_threshold = 22   # Enter trending mode
        self.ranging_threshold = 18 # Enter ranging mode
    
    def detect(self, ohlcv) -> StrategyMode:
        current_adx = self.adx(ohlcv)[-1]
        current_plus = self.di_plus(ohlcv)[-1]
        current_minus = self.di_minus(ohlcv)[-1]
        
        # Check DI+ / DI- for trend direction
        di_diff = current_plus - current_minus
        
        if self.current_mode == StrategyMode.TREND:
            # Only exit trend mode when ADX drops below the lower threshold
            if current_adx < self.ranging_threshold:
                self.current_mode = StrategyMode.RANGING
        elif self.current_mode == StrategyMode.RANGING:
            # Only enter trend mode when ADX crosses above the upper threshold
            if current_adx > self.trend_threshold:
                self.current_mode = StrategyMode.TREND
        else:
            # In transition mode, decide based on which threshold is crossed
            if current_adx > self.trend_threshold:
                self.current_mode = StrategyMode.TREND
            elif current_adx < self.ranging_threshold:
                self.current_mode = StrategyMode.RANGING
        
        return self.current_mode
```

## 6. The Ranging Mode Signal

When in ranging mode, use **Donchian channel fade**:

$$\text{BUY}_{ranging} = \text{Close}_t < \text{Lower Band}_t + 0.1 \times \text{Channel Width}$$

$$\text{SELL}_{ranging} = \text{Close}_t > \text{Upper Band}_t - 0.1 \times \text{Channel Width}$$

Exit at the band middle (SMA) with tight stop at 1× ATR.

## 7. Performance Projection

| Period | Baseline D4 | Regime-Switching |
|--------|-------------|------------------|
| High-trend years | PF 1.20 | PF 1.35-1.50 |
| Range-bound years | PF 1.00 (breakeven) | PF 1.05-1.15 |
| Full 11-year | PF 1.14 | PF 1.22-1.32 |
| Max DD | 15.3% | 10-12% |
| CAGR | +6.5% | +7.5-8.5% |

## 8. Conflict Check

| Item | Compatible? | Notes |
|------|-------------|-------|
| Volatility compression | YES | Used in transition mode |
| Chandelier exit | YES | Used in trend mode |
| Partial TP | YES | Used in ranging mode |
| Meta-labeling | YES | Can override mode decisions |
| Session filter | NO | Ranging mode might use sessions differently |
