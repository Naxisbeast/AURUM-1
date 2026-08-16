# D4 Model Documentation (SR 26-2 Lite)

**Model**: D4 — Donchian 20-bar breakout, fixed 2R exit, BUY+SELL, no filters
**Documented**: 2026-08-05
**Status**: Paper trading live at 0.35% risk

This document formally describes the D4 strategy as a model, following the
spirit of the SR 26-2 / SR 11-7 Model Risk Management framework. It is a
lite version — the full framework assumes institutional infrastructure that
doesn't apply to a solo research project — but it captures the same
essential elements: purpose, inputs, methodology, validation, limitations,
and change control.

---

## 1. Model Purpose and Scope

**Purpose**: Generate profitable trade signals on XAU/USD (gold) at M15
timeframe by identifying trend breakouts.

**Scope**: Single instrument (XAU/USD), single timeframe (M15), paper trading
only. Not used for live capital deployment.

**Intended use**: As a research-validated candidate for potential future
capital deployment, subject to further evidence collection.

---

## 2. Input Data

| Input | Source | Description |
|-------|--------|-------------|
| M15 OHLCV candles | OANDA API → local cache | 15-minute open/high/low/close/volume |
| Historical backtest data | OANDA (2016-2026) | 236,303 M15 candles, 11 years |
| Live market data | Forward shadow service | Continuously updated cache |

**Data quality considerations**:
- XAU/USD is a highly liquid instrument — low survivorship bias risk
- Single-asset scope avoids multi-asset data issues
- Candle timestamps are UTC-aligned
- Weekend/market-close gaps are handled

---

## 3. Methodology

### Entry Signal

The model computes a Donchian channel from the last 20 M15 candles:

```
high_20 = max(high of last 20 candles, shifted 1 bar)
low_20  = min(low of last 20 candles, shifted 1 bar)

BUY signal: close > high_20
SELL signal: close < low_20
```

Entry occurs on the NEXT bar open (to avoid look-ahead bias).

### Exit Rules

Fixed 2R exit:
```
Stop loss: entry - 2 × ATR (BUY)  or entry + 2 × ATR (SELL)
Take profit: entry + 2 × risk (BUY)  or entry - 2 × risk (SELL)
where risk = |entry - stop_loss|
```

The 2R asymmetric payout turns a ~37% win rate into a profitable expectancy.

### Risk Controls

- Risk per trade: 0.35% of equity (Kelly-capped)
- Daily loss kill: stops trading after -3% daily
- Drawdown kill: stops trading after -8% from peak
- Spread filter: rejects trades when spread > 3 pips
- Recovery mode: halves size after -5% from peak
- Price collar: rejects orders >5% from market price
- Independent watchdog: kills process at 15% DD / 10% daily loss / 6h stale data

---

## 4. Validation

| Validation | Method | Result |
|------------|--------|--------|
| Walk-forward | 18 windows, 2yr train / 6mo test, 11 years | 88.9% positive windows |
| Monte Carlo | 10,000 simulations of trade shuffles | 0% ruin probability |
| TC stress | Spread/slippage at 4x normal | PF stays > 1.05 |
| Stationarity | ADF test on signal components | Signal is stationary (not noise) |
| Determinism | Backtest run twice, identical inputs | Fully deterministic |
| Deflated Sharpe | Corrects for selection bias (D1-D7) | Computed at 100-trade gate (2026-08-16) — 0.274, underpowered with 4 trials; reassess ~200 trades |
| Live signal decay | Live vs backtest ICIR profile | Healthy, no regime shift |

---

## 5. Parameter Sensitivity

The model has few parameters, which is a strength:

| Parameter | Value | Sensitivity |
|-----------|-------|-------------|
| Lookback | 20 bars | L15 and L55 both tested; L20 is best |
| Exit multiplier | 2R | 1R tested and performs worse (D1-D3) |
| Directions | BUY+SELL | BUY-only loses ~60% of edge |
| Filters | None | All filters tested and removed edge |

The strategy's simplicity means it has minimal parameter sensitivity — the
parameters were chosen from systematic comparison (D1-D7), not curve-fitting.

---

## 6. Known Limitations

1. **Trend-following on a trending asset**: Gold trended up ~11.9%/yr over the
   backtest period. Some of D4's edge reflects the secular uptrend, not pure
   market timing. Buy-and-hold comparison documented in research/promoted/d4.

2. **Live evidence is still limited**: The 100-trade gate was run at 104 trades
   (2026-08-16) — 2/3 automated criteria passed (Sharpe, PF). The DSR criterion
   could not be meaningfully assessed with only 4 same-family trials in the pool;
   it becomes statistically meaningful near 200 trades. See `docs/STATUS.md`.

3. **Paper trading ≠ live capital**: Real slippage, requotes, broker latency,
   and psychological pressure are absent in paper mode.

4. **Single instrument**: No diversification. If gold enters a multi-year
   regime D4 can't capture, the model has no hedge.

5. **Solo validation**: All validation was designed and graded by the same
   person who built the strategy. No independent review yet.

---

## 7. Change Control

Any change to D4 must:
1. Be logged in the trial ledger (`aurum1/research/trial_ledger.py`)
2. Be validated via walk-forward + Monte Carlo + TC stress
3. Update the Deflated Sharpe Ratio if relevant
4. Pass the four-question gate:
   - Does it improve performance?
   - Does it improve reliability?
   - Does it improve explainability?
   - Does it reduce uncertainty?
5. Be documented here and in `docs/system/AUDIT_DECISIONS.md`

If no criterion in the four-question gate is satisfied, the change is rejected.

---

## 8. Approval Status

| Gate | Status |
|------|--------|
| 50-trade risk review | ✅ PASSED (4/4 criteria, 2026-08-05) |
| 100-trade strategy review | ✅ RUN (2026-08-16) — 2/3 automated criteria passed (Sharpe, PF); DSR underpowered, reassess ~200 trades |
| Real capital deployment | 🔲 Not considered until DSR clears |

The model remains in paper trading. The 100-trade gate confirmed the live edge
(Sharpe, PF) but DSR needs ~200 trades for statistical confidence before any
real-capital consideration.
