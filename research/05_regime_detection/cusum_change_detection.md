# CUSUM Change-Point Detection for Regime Identification

## 1. What is CUSUM?

The **Cumulative Sum** (CUSUM) algorithm detects change points in time series by monitoring cumulative deviations from a target mean. When the cumulative sum exceeds a threshold, a change point is declared.

## 2. Mathematical Formulation

### 2.1 Standard CUSUM

For a sequence $X_1, X_2, \ldots, X_t$:

$$S_t = \max(0, S_{t-1} + X_t - \mu - \delta)$$
$$T_t = \min(0, T_{t-1} + X_t - \mu + \delta)$$

Where:
- $\mu$ = target mean
- $\delta$ = drift term (allowable deviation before triggering)
- $S_t$ = cumulative positive deviation
- $T_t$ = cumulative negative deviation

A change point is detected when $S_t > h$ or $T_t < -h$, where $h$ is the threshold.

### 2.2 For ATR-Based Regime Detection

Apply CUSUM to the ATR(14) series. When ATR compressed below its mean, issue a signal that a volatility breakout is approaching.

### 2.3 For Return-Based Regime Detection

Apply CUSUM to 5-bar returns (roughly 75 minutes). When returns persistently deviate from zero, a trend may be developing.

## 3. Advantages Over ML

| Aspect | ML Regime Classifier | CUSUM |
|--------|---------------------|-------|
| Latency | High (batch training) | Low (online) |
| Overfitting risk | Moderate-High | None |
| Interpretability | Low | High |
| Parameter tuning | Many hyperparameters | 2 parameters ($\delta$, $h$) |
| Real-time adaptation | Retraining required | Continuous |

## 4. Implementation

```python
class CUSUMDetector:
    """Real-time CUSUM change point detection."""
    
    def __init__(self, threshold: float = 1.0, drift: float = 0.5, 
                 warmup: int = 20):
        self.threshold = threshold
        self.drift = drift
        self.warmup = warmup
        self.values = []
        self.mean = 0.0
        self.S_high = 0.0
        self.S_low = 0.0
        self.change_points = []
        self.counter = 0
    
    def update(self, value: float) -> dict:
        """
        Process a new value. Returns detection info if a change point is found.
        """
        self.values.append(value)
        self.counter += 1
        
        # Warmup: compute initial mean
        if self.counter <= self.warmup:
            if self.counter == self.warmup:
                self.mean = np.mean(self.values)
            return {'change_detected': False}
        
        # Update mean with exponential decay (adaptive)
        self.mean = 0.99 * self.mean + 0.01 * value
        
        # Update CUSUM statistics
        self.S_high = max(0, self.S_high + value - self.mean - self.drift)
        self.S_low = min(0, self.S_low + value - self.mean + self.drift)
        
        # Check for change point
        if self.S_high > self.threshold:
            self.change_points.append(self.counter)
            result = {'change_detected': True, 'direction': 'up', 
                      'cumulative': self.S_high, 'index': self.counter}
            self.S_high = 0
            self.S_low = 0
            return result
        
        if self.S_low < -self.threshold:
            self.change_points.append(self.counter)
            result = {'change_detected': True, 'direction': 'down',
                      'cumulative': self.S_low, 'index': self.counter}
            self.S_high = 0
            self.S_low = 0
            return result
        
        return {'change_detected': False}
```

## 5. Integration into D4

Use CUSUM on two series:

1. **ATR CUSUM:** Detect volatility regime shifts
2. **Return CUSUM:** Detect trend initiation

```python
# Before taking a breakout signal
atr_cusum_result = atr_cusum.update(current_atr)
return_cusum_result = return_cusum.update(bar_return)

# If CUSUM detected a volatility expansion AND a trend return, high conviction
high_conviction = (atr_cusum_result['change_detected'] and 
                   return_cusum_result['change_detected'])
```

## 6. Parameter Selection

| Parameter | Effect | Recommended |
|-----------|--------|-------------|
| Threshold ($h$) | Higher = fewer false alarms, longer detection delay | 0.5-2.0 |
| Drift ($\delta$) | Higher = less sensitive | 0.25-0.50 |
| Warmup | Stabilize initial mean estimate | 20-50 bars |
| Series | What to monitor | ATR(14) or 5-bar returns |
