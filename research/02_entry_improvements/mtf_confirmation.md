# Multi-Timeframe Confirmation (Hard Gate)

## 1. The Problem with Soft Features

Current D4 architecture feeds H1/H4 features into the ML ensemble as *soft* inputs. The problem:

- The ML model can **override** the higher-timeframe signal if other features say otherwise
- In backtesting, this averaged out — but in specific market regimes (trending vs ranging), the override is often wrong
- A **hard gate** that makes the higher-timeframe trend mandatory is different from a soft feature

## 2. Hard Gate Design

### 2.1 The Rule

$$\text{Trade}_t = \begin{cases}
\text{BUY Signal}_t & \text{if M15 signal AND H1 in uptrend} \\
\text{SELL Signal}_t & \text{if M15 signal AND H1 in downtrend} \\
\text{Skip} & \text{otherwise}
\end{cases}$$

### 2.2 Defining H1 Trend

Multiple options (test each):

| Method | H1 Uptrend Condition | Sensitivity |
|--------|---------------------|-------------|
| EMA | H1 close > H1 EMA(50) | Medium |
| Donchian | H1 close > H1 Donchian(20) middle | Low (lagging) |
| Slope | H1 EMA(20) slope > 0 for 3 bars | High |
| ADX + DI | ADX > 22 AND DI+ > DI- | Medium |

**Recommended:** Start with **H1 EMA(50)** — clean, simple, and predictive.

### 2.3 Implementation

```python
def h1_trend_gate(ohlcv_m15) -> str:
    """Returns 'BULLISH', 'BEARISH', or 'NEUTRAL' for H1 trend."""
    # Resample M15 to H1
    h1 = ohlcv_m15['close'].resample('1H').last()
    
    # H1 EMA(50) — 50 hours ≈ 200 M15 bars
    h1_ema50 = h1.ewm(span=50, adjust=False).mean()
    
    if h1.iloc[-1] > h1_ema50.iloc[-1]:
        return 'BULLISH'
    elif h1.iloc[-1] < h1_ema50.iloc[-1]:
        return 'BEARISH'
    else:
        return 'NEUTRAL'

# In entry logic:
h1_trend = h1_trend_gate(ohlcv)
if signal.direction == 'BUY' and h1_trend == 'BEARISH':
    skip_trade('H1 trend bearish, skipping long')
elif signal.direction == 'SELL' and h1_trend == 'BULLISH':
    skip_trade('H1 trend bullish, skipping short')
```

## 3. Expected Impact

| Metric | Current | + H1 Gate |
|--------|---------|-----------|
| Trade count | 8,175 | 5,500-6,500 |
| Win rate | 40% | 43-46% |
| Profit factor | 1.14 | 1.18-1.22 |
| Total PnL | +$42,678 | +$38,000-$42,000 |

**Fewer trades, slightly higher quality.** The trade count reduction is acceptable if the quality improvement is material.

## 4. Conflict Check

| Item | Compatible? | Notes |
|------|-------------|-------|
| Volatility compression | YES | Both filters applied independently |
| Chandelier exit | YES | Filter is for entry only |
| ADX regime Kelly | YES | Both conditions must pass |
| Meta-labeling | YES | Meta-labeler can include H1 trend as feature |
| Regime switching | YES | H1 trend helps confirm regime |
