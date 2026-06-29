# AURUM-1 Machine Learning Audit — Compiled Findings

## Scores and Verdict

| Metric | Value | Interpretation |
|--------|-------|----------------|
| **ML Usefulness Score** | **2/10** | ML adds no measurable edge. D6 (with ML) vs D4 (without ML) differ by $3 on $42,678 PnL over 11 years. |
| **Signal Logic Score** | **7/10** | State machine transitions are sound; pullback/breakout logic is clean; session filters work. |
| **ML Verdict** | The ML ensemble provides no measurable edge over pure rule-based trading. The entire ML stack should be treated as experimental and should not influence production trading decisions. |
| **Recommended Mode** | **RULE_ONLY** — pure Donchian 20 breakout with 2R exit. No ML components should gate trading decisions. |

---

## Component Findings

### 1. RegimeClassifier (`aurum1/models/regime_classifier.py`)

**Severity: MEDIUM — Redundant by design**

The regime labels are a deterministic function of:
- `ADX > 25` (trend strength)
- `ema_alignment_score >= 3` (direction)

But the classifier's FEATURES are supposedly indirect indicators (ATR percentile, BB width, MACD histogram, RSI, relative volume, VIX, DXY). The classifier is trained to predict labels that are themselves computed from ADX + EMA alignment — which the backtest engine can check DIRECTLY without any ML.

**Severity: MEDIUM — Label definition creates a no-man's-land**
- `ADX > 25 AND ema_alignment >= 3 → TRENDING_UP`
- `ADX > 25 AND ema_alignment <= -3 → TRENDING_DOWN`
- Else → RANGING

What about ADX=30, ema_alignment=-2? That's a clear downtrend but labeled RANGING because alignment is -2 not -3. The label definition itself discards information the classifier can never recover.

**Severity: MEDIUM — _CentroidClassifier fallback when LightGBM is unavailable**
- At `line 242-260`, if `import lightgbm` fails, falls back to `_CentroidClassifier`
- Nearest-centroid in 7-dimensional feature space with `np.nanmean` centroids
- No error raised — the system silently degrades
- In walk-forward, this is caught by `try/except/pass` at `walk_forward.py:91`

**Severity: INFO — Validation Sharpe of 0.85 is computed in-sample**
- `_validation_sharpe` at `line 273-277` computes Sharpe on TRAINING data predictions
- It measures alignment between predicted regime labels and forward returns, NOT trading performance
- Not comparable to out-of-sample trading Sharpe

### 2. DirectionPredictor (`aurum1/models/direction_predictor.py`)

**Severity: HIGH — Never actually used as a neural network**

The "SoftmaxSequenceModel" is a centroid-based classifier (not an LSTM, not a neural network). The project's own memory says "limited predictive power." In walk-forward runs, training failures are silently caught by `except: pass` at `walk_forward.py:91`, meaning many windows run without it without any operator awareness.

### 3. SentimentScorer (`aurum1/models/sentiment_model.py`)

**Severity: MEDIUM — Placeholder with no live data feed**

Uses Alpha Vantage API key. If unset (which it likely is), all sentiment features default to 0.0. Even when active, the sentiment model is basic keyword matching — not a FinBERT or transformer model despite the naming. All sentiment features in practice are zeroes.

### 4. Ensemble and Promotion Gate (`aurum1/models/ensemble.py`)

**Severity: HIGH — Promotion gate has hardcoded checks**

The D6 variant (Donchian + ML ensemble) produces nearly identical results to D4:
- D4: +$42,677.72 PnL, 8,175 trades, PF 1.135
- D6: +$42,680.63 PnL, 8,169 trades, PF 1.135
- Difference: **$2.91** over 11 years

**Severity: MEDIUM — Ensemble weights are arbitrary (50/30/20)**

There is no documented evidence for why the RegimeClassifier gets 50% weight, DirectionPredictor 30%, and Sentiment 20%. These weights are uncalibrated.

### 5. Walk-Forward Mode Training (`aurum1/backtesting/walk_forward.py`)

**Severity: HIGH — Training failures are silently skipped**

```python
try:
    classifier.train(train_features, update_latest=False)
    engine.regime_classifier = classifier
    ...
except Exception:
    pass
```

If LightGBM is not installed, training fails silently. If the training window has no label variance (all RANGING), training fails silently. The engine then runs with `regime_classifier=None` and falls back to `RegimeClassifier.generate_labels()` — which is just the ADX/EMA rule. This means walk-forward windows that "fail" the ML training are still counted as "positive ML windows" when they're actually running rule-only.

---

## Decision Criteria for ML Component Promotion

| Component | Current Status | Recommendation | Evidence Threshold for Promotion |
|-----------|---------------|----------------|-------------------------------|
| RegimeClassifier | Experimental | Keep as informational overlay only | Must demonstrate >5% PF improvement over RULE_ONLY on 3+ years OOS |
| DirectionPredictor | Experimental | **Disable** — never used as neural network, centroid fallback is noise | Must be replaced with actual time-series model before any promotion |
| SentimentScorer | Experimental | **Disable** — no live data feed, always returns zeros | Requires active Alpha Vantage key + validated signal improvement |
| FULL_ENSEMBLE mode | Experimental | **Remain disabled** | Must pass same 5% PF threshold AND demonstrate the ensemble outperforms its best individual component |

## Recommended Mode Configuration

```
Default operating mode: RULE_ONLY
  - Pure Donchian 20 breakout with BUY+SELL directions
  - 2R fixed exit (atr_sl_multiplier=2.0, atr_tp_multiplier=4.0 or 2R=2*risk_dist)
  - No regime filtering, no ML gating, no sentiment
  - This is the D4 configuration that produced +$42,678 over 11 years (PF 1.14)

Allowable override: RULE_REGIME
  - Same as RULE_ONLY but with regime conflict check
  - Only blocks BUY-in-downtrend and SELL-in-uptrend
  - Uses rule-based ADX/EMA labels (RegimeClassifier.generate_labels), NOT the ML model
  - Primarily for comparing whether regime filtering helps

Experimental (do not promote): FULL_ENSEMBLE
  - Must demonstrate evidence threshold above before promotion
  - Currently adds no measurable value ($3 difference over 11 years)
```

## Code-Level Recommendations

1. **`aurum1/models/regime_classifier.py:84-90`** — Replace centroid-based labels with volatility-regime labels: LOW_VOL, MEDIUM_VOL, HIGH_VOL. These are more directly actionable for position sizing than direction labels.

2. **`aurum1/models/regime_classifier.py:242-260`** — Remove the `_CentroidClassifier` fallback. If LightGBM is not available, raise an ImportError so the operator knows. Silent degradation is dangerous.

3. **`aurum1/models/direction_predictor.py`** — Either replace with a proper time-series model (LSTM, Transformer, or XGBoost with proper feature engineering) or remove entirely. The current centroid-based approach is not ML.

4. **`aurum1/backtesting/walk_forward.py:91`** — Remove the bare `except: pass`. Log the specific exception so operators know when ML training fails.

5. **`aurum1/config/settings.yaml:137`** — Already changed to `mode: rule_only` in the audit fix commit. Keep this default.

6. **`aurum1/models/ensemble.py`** — Add a regression test that asserts FULL_ENSEMBLE performance on a fixed test set. This prevents silent degradation.
