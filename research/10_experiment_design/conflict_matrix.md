# Strategy Change Conflict Matrix

## 1. Quick Reference Matrix

| Change | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|--------|---|---|---|---|---|---|---|---|---|
| 1. Vol Compression | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 2. Pullback Entry | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 3. Chandelier Exit | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 4. Partial TP + Trail | ✅ | ✅ | ✅ | — | ✅ | ✅ | ⚠️ | ✅ | ✅ |
| 5. H1 Hard Gate | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ |
| 6. Vol Scaling | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| 7. ADX Regime Kelly | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ | — | ⚠️ | ✅ |
| 8. Meta-Labeling | ✅ | ⚠️ | ✅ | ✅ | ✅ | ✅ | ⚠️ | — | ✅ |
| 9. Regime Switching | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |

- ✅ = Compatible (can be combined)
- ⚠️ = Partial conflict (test carefully)
- 🚫 = Incompatible (mutually exclusive)

## 2. Detailed Conflict Descriptions

### Pullback Entry + Meta-Labeling (⚠️)

**Issue:** Both modify the entry decision. The pullback entry waits for a retracement before entering; meta-labeling predicts trade outcome based on features at signal time.

**Resolution:** Apply meta-labeling AFTER the pullback entry — use the features at the actual entry time (after pullback), not the initial breakout time. This is compatible but requires careful staging.

### ADX Regime Kelly + Meta-Labeling (⚠️)

**Issue:** Both adjust position sizing based on market conditions. ADX-Kelly uses a simple rule; meta-labeling uses ML.

**Resolution:** Use ADX-Kelly as the base sizing and meta-labeling as a multiplier on top:

$$\text{Final Size} = \text{Base Size} \times \text{ADX Multiplier} \times \text{Meta-Label Multiplier}$$

### ADX Regime Kelly + Partial TP + Trail (⚠️)

**Issue:** Regime-dependent sizing affects how much risk is on the table for the partial close.

**Resolution:** The partial close should be based on the position that was actually opened, not on the base risk. Since the position was sized based on the regime-dependent Kelly, the partial close at 1R naturally reflects this. No special handling needed.

### Pullback Entry + H1 Trend Gate (✅)

**Compatibility note:** If both filters are applied, extremely few signals may survive. Monitor trade count to ensure the strategy remains active.

## 3. Compatibility Scores

| Change | Compatible With | Partial Conflicts | Incompatible With |
|--------|----------------|-------------------|-------------------|
| Vol Compression | 8/8 | 0 | 0 |
| Pullback Entry | 7/8 | 1 | 0 |
| Chandelier Exit | 8/8 | 0 | 0 |
| Partial TP + Trail | 7/8 | 1 | 0 |
| H1 Hard Gate | 8/8 | 0 | 0 |
| Vol Scaling | 8/8 | 0 | 0 |
| ADX Regime Kelly | 6/8 | 2 | 0 |
| Meta-Labeling | 6/8 | 2 | 0 |
| Regime Switching | 8/8 | 0 | 0 |

## 4. Recommended Test Combinations

| Batch | Changes | Risk | Expected Benefit |
|-------|---------|------|------------------|
| A (Conservative) | Vol Compression + Chandelier Exit + Vol Scaling | Low | PF 1.30, DD 12% |
| B (Aggressive) | A + Partial TP + ADX Kelly + H1 Gate | Medium | PF 1.40, DD 10% |
| C (Full System) | B + Meta-Labeling + Pullback Entry | High | PF 1.50+, DD 10% |
| D (Regime Switch) | A + Regime Switching | High | PF 1.35+, multicurve |

**Test in order: A → B (if A works) → C/D (if B works). Never skip steps.**
