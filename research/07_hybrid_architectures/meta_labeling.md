# Meta-Labeling: Machine Learning as a Gatekeeper

## 1. What is Meta-Labeling?

**Meta-labeling** (López de Prado, *Advances in Financial Machine Learning*, 2018) is a two-step ML approach:

1. **Primary model:** Predicts the *side* of the trade (BUY or SELL) — this is the existing Donchian breakout signal
2. **Secondary model (meta-labeler):** Predicts *whether* the primary signal will succeed or fail — this is a binary classifier

$$y_{meta} = \begin{cases}
1 & \text{if primary signal made money} \\
0 & \text{if primary signal lost money}
\end{cases}$$

The meta-labeler is trained on **only the bars where the primary model fired a signal**, not on all bars.

## 2. Mathematical Framework

### 2.1 Why Meta-Labeling?

Directly predicting a trade's outcome requires predicting future prices — a hard problem. Meta-labeling instead answers: *given the conditions when this breakout fired, did it work?* This is a simpler classification problem.

### 2.2 The Key Insight

For the D4 strategy, the Breakeven $p_{BE} = \frac{L}{W+L} = \frac{1}{2+1} = \frac{1}{3} \approx 33.3\%$.

A meta-labeler with accuracy $p_{meta} > 0.333$ adds value:
- If $p_{meta} > 0.5$, take all signals it approves
- If $0.333 < p_{meta} < 0.5$, it still adds information — boost size on approved signals, reduce on rejected signals

### 2.3 Bet Sizing with Meta-Labeling

$$\text{Final Size} = \text{Base Size} \times \begin{cases}
2 & \text{if meta-labeler predicts success with high confidence} \\
1 & \text{if meta-labeler predicts success} \\
0.5 & \text{if meta-labeler predicts failure} \\
0 & \text{if meta-labeler predicts failure with high confidence}
\end{cases}$$

This is NOT just a filter — it's a **sizing overlay** that weights bets by their predicted probability of success.

## 3. Feature Engineering for Meta-Labeler

The meta-labeler uses **context features** at signal time:

| Feature | Description | Rationale |
|---------|-------------|-----------|
| ATR percentile | Volatility regime | Breakouts work better in certain volatility states |
| ADX level | Trend strength | Stronger trends → better breakout success |
| Breakout distance | Distance from close to Donchian band as % of ATR | Strong penetration → stronger signal |
| Consecutive signals | How many breakouts in last N bars | A cluster of signals may indicate a regime |
| Prior 10 trade win rate | Recent strategy performance | Strategy may have hot/cold periods |
| Hour of day | Session context | Some sessions work better |
| Day of week | Day-of-week effect | Monday vs Friday differences |
| DXY regime | Dollar strength | Gold correlation |

## 4. Implementation

### 4.1 Labeling

```python
def create_meta_labels(df_signals):
    """
    Create labels for meta-learning.
    For each signal (row), label = 1 if trade had positive R, else 0.
    """
    labels = []
    for _, sig in df_signals.iterrows():
        # Get the trade result for this signal
        trade = trades_lookup[sig.timestamp]
        labels.append(1 if trade.r > 0 else 0)
    return np.array(labels)
```

### 4.2 Training

```python
from lightgbm import LGBMClassifier

# X = features at signal time
# y = 1 if winning trade, 0 if losing trade
meta_model = LGBMClassifier(
    n_estimators=100,
    max_depth=3,  # Shallow to avoid overfitting
    learning_rate=0.1,
    class_weight='balanced',
    subsample=0.8,
    colsample_bytree=0.8,
)
meta_model.fit(X_train, y_train)

# Only approve signals where P(success) > threshold
# Threshold should be calibrated to optimize PF
```

### 4.3 Integration

```python
def meta_labeled_entry(signal, features, meta_model):
    """Return (enter: bool, size_multiplier: float)."""
    if signal is None:
        return False, 0.0
    
    # Get features at signal time
    X = features.loc[[signal.timestamp]]
    
    # Predict probability of success
    p_success = meta_model.predict_proba(X)[0, 1]
    
    if p_success > 0.65:
        return True, 1.5  # Enter with 150% of base size
    elif p_success > 0.50:
        return True, 1.0  # Enter with base size
    elif p_success > 0.35:
        return True, 0.5  # Enter with half size
    else:
        return False, 0.0  # Skip trade
```

## 5. Expected Impact

| Metric | Base D4 | + Meta-Labeling |
|--------|---------|------------------|
| Trade count | 8,175 | 5,500-6,500 |
| Win rate | 40% | 48-52% |
| Profit factor | 1.14 | 1.30-1.45 |
| Max DD | 15% | 10-12% |
| Total PnL | +$42,678 | +$45,000-$50,000 |

**The key is that meta-labeling applies machine learning to the actual problem — classification of signal quality — rather than trying to predict prices.** This is fundamentally easier and more aligned with what ML models do well.

## 6. Conflict Check

| Component | Compatible? | Notes |
|-----------|-------------|-------|
| Volatility compression filter | YES | Can be used as feature or as pre-filter |
| Chandelier exit | YES | Meta-labeler works on entry signal only |
| ADX regime | YES | ADX can be a meta-labeler feature |
| Existing ML ensemble | YES | Replace ensemble with meta-labeler approach |
