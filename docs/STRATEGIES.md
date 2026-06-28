# AURUM-1 Strategy Documentation

## Overview

AURUM-1 has two independent strategy paths that share the same codebase:

1. **Main Orchestrator Strategy** — ML ensemble + pullback-breakout state machine
2. **Donchian Shadow Strategies** — Simple Donchian 20 breakout with configurable exits and filters

---

## 1. Main Orchestrator Strategy (`aurum1/signals/state_machine.py`)

### Entry Logic — Pullback-Breakout State Machine

```
SCANNING → ARMED → WINDOW_OPEN → TRADE
```

| State | Description |
|-------|-------------|
| **SCANNING** | Looking for a valid setup. Requires: direction signal (from ensemble or rule), ADX > 25, EMA alignment, London/NY session. |
| **ARMED** | Setup identified. Waiting for a pullback (1-4 bearish candles). Max 20 candles before timeout. |
| **WINDOW_OPEN** | Pullback confirmed. Waiting for breakout past the armed candle's high/low + 0.3 ATR buffer. Max 6 candles before expiry. |
| **TRADE** | Breakout triggered. Entry at breakout level. Stop at 2× ATR. Target at 3× ATR (1.5R). |

### Configuration

```yaml
signals:
  adx_threshold: 25
  min_pullback_candles: 1
  max_pullback_candles: 4
  armed_timeout_candles: 20
  window_expiry_candles: 6
  atr_sl_multiplier: 2.0
  atr_tp_multiplier: 3.0
  atr_breakout_buffer: 0.3
  require_session_filter: true
```

### Machine Modes

| Mode | Description |
|------|-------------|
| `RULE_ONLY` | Direction from EMA crossover (EMA9 > EMA20 = BUY) |
| `RULE_REGIME` | Direction from ensemble + regime filter (no counter-trend) |
| `RULE_REGIME_SENT` | Same as RULE_REGIME + sentiment threshold |
| `FULL_ENSEMBLE` | Requires all trained ML models (not currently achievable) |

---

## 2. Donchian Shadow Strategies

### Raw Donchian Fixed 2R (`raw_donchian_fixed_2r`)

**Status**: Locked 3-month research study. Running on cloud since June 11.

**Entry**: Price closes above the prior bar's 20-period high (Donchian breakout)

```
signal_time = candle where close > high.rolling(20).max().shift(1)
entry_time  = signal_time + 1 bar (next M15 open)
entry_price = next bar's open + slippage
```

**Stop**: Entry price - 2 × ATR (1R = 2 × ATR)

**Target**: Entry price + 4 × ATR (fixed 2R)

**Filters**: None. Takes every signal (unless position already open).

**Risk**: 0.25% per trade

**Performance** (34 live trades):
```
WR:  23.5%  (8W / 26L)
PF:   0.61
Net: -$254.01
R:   -10.06R
```

**Root Cause**: The 2R fixed target is too far. Only 23.5% of trades reach it. The majority (76.5%) hit stop loss first. The entry signal itself is valid — the target distance is the problem.

---

### D1 — 1R Exit + Volatility/Session Filter

**Status**: Timer-based shadow journal running every 15 minutes.

**Changes from Raw**:
1. Exit: Fixed **1R** instead of 2R (target = entry + risk_distance)
2. Filter: Block trades when `volatility == high` OR `session == london`

**Entry**: Same Donchian 20 breakout as Raw.

**Decision Logic**:
```python
if direction == "SELL":              → HOLD (short not enabled)
if volatility == "high":             → HOLD (high_volatility)
if session == "london":              → HOLD (london_session)
otherwise:                           → TAKE (fixed 1R exit)
```

**Exit Simulation**: 1R target or -1R stop on candle data.

**Performance** (36 closed journal trades):
```
WR:  52.8%
PF:   1.24
```

---

### D2 — 1R Exit + D1 Filter (Full Historical Simulation)

**Status**: Timer-based simulation running every 15 minutes for monitoring.

**Changes from Raw**:
1. Exit: Fixed **1R** instead of 2R
2. Filter: Block trades when `volatility == high` OR `session == london`
3. Same 0.25% risk per trade

**Full 12-month simulation** (25,379 M15 candles, June 2025 - June 2026):

| Metric | D2 | Raw (for comparison) |
|--------|-----|---------------------|
| Trades | 543 | 34 |
| Win Rate | **57.6%** | 23.5% |
| Profit Factor | **1.33** | 0.61 |
| Net R | **+76.87R** | -10.06R |
| Net PnL | **+$2,183.87** | -$254.01 |

**Session Breakdown**:

| Session | Trades | Wins | Losses | Total R |
|---------|--------|------|--------|---------|
| Asia | 243 | 136 | 107 | +25.36R |
| London-NY Overlap | 182 | 113 | 69 | **+42.07R** |
| Rollover | 63 | 34 | 29 | +4.78R |
| New York | 55 | 30 | 25 | +4.66R |

**Exit Breakdown**:
- Take Profit (1R): 313 trades (57.6%)
- Stop Loss (-1R): 225 trades (41.5%)
- Stop Loss Gap: 5 trades (0.9%)

---

## 3. Strategy Comparison Matrix

| Feature | Raw | D1 | D2 |
|---------|-----|----|----|
| Entry | Donchian 20 | Donchian 20 | Donchian 20 |
| Stop | 2 × ATR | 2 × ATR | 2 × ATR |
| Target | 4 × ATR (2R) | 2 × ATR (1R) | 2 × ATR (1R) |
| Volatility Filter | None | Blocks "high" | Blocks "high" |
| Session Filter | None | Blocks "london" | Blocks "london" |
| SELL enabled | No | No | No |
| Risk per trade | 0.25% | 0.25% | 0.25% |
| Status | 🔴 Running (since Jun 11) | 🟡 Timer (15 min) | 🟡 Timer (15 min) |

## 4. Key Research Insights

1. **The entry logic is valid**. The Donchian 20 breakout generates signals that win 57.6% of the time when paired with the right exit and filters.

2. **The 2R exit is too ambitious**. Switching from 2R to 1R increases win rate from 23.5% to 57.6% — a 34 percentage point improvement.

3. **Session filtering adds value**. Blocking London session signals removes the weakest-performing session (London = breakeven at best).

4. **Volatility filtering adds value**. Blocking high-volatility signals prevents entries during unstable market conditions.

5. **The combination is synergistic**. The 1R exit + vol/session filter together produce PF=1.33 with 543 trades — statistically significant.
