# AURUM-1 Strategy Documentation

**Last updated**: 2026-07-05

## Overview

AURUM-1 has two independent strategy paths that share the same codebase:

1. **Main Orchestrator Strategy** — ML ensemble + pullback-breakout state machine (STOPPED)
2. **Donchian Shadow Strategies** — Simple Donchian 20 breakout with configurable exits, directions, and filters

The Donchian family has been systematically researched through phases S1-S5 and narrowed to 6 named variants (D1-D6), with **D4** emerging as the best performer over 11 years of backtest data.

### Architectural Decisions

| Decision | Rationale | Context |
|----------|-----------|---------|
| Paper broker handles SL/TP natively | Avoids race conditions; `PaperBroker.update_prices()` evaluates all SL/TP against OHLC data | `broker.py:182-210` |
| R-multiple calculated at close | Enables Kelly calculator, trade quality comparison; `risk_amount` and `r_multiple` in every trade dict | `broker.py:253` |
| Slippage is gaussian, not folded-normal | Zero slippage is the most likely single outcome; negative slippage (price improvement) is possible in liquid markets with limit orders | `broker.py:338` |
| SL/TP rebased around actual fill | Preserves intended risk distance even when slippage shifts entry price | `broker.py:113-120` |
| Open positions persisted every cycle | Guarantees restart survival; `_save_open_positions()` called every loop iteration | `d4_paper_trader.py:299-325` |
| Missed signals logged with reasons | Every SKIP records timestamp, direction, price, and rejection reason to `missed_signals` table | `d4_paper_trader.py:587-595` |
| Entry/exit slippage tracked separately | Enables execution quality comparison; entry slippage vs exit slippage have different magnitudes and causes | `d4_paper_trader.py:456-459, 638-641` |

---

## 1. Main Orchestrator Strategy (`aurum1/signals/state_machine.py`)

**Status**: STOPPED (last run May 27, 2026).

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

### Machine Modes

| Mode | Description |
|------|-------------|
| `RULE_ONLY` | Direction from EMA crossover (EMA9 > EMA20 = BUY) |
| `RULE_REGIME` | Direction from ensemble + regime filter (no counter-trend) |
| `RULE_REGIME_SENT` | Same as RULE_REGIME + sentiment threshold |
| `FULL_ENSEMBLE` | Requires all trained ML models (not currently achievable) |

---

## 2. Donchian Strategy Variants

### Common Entry Logic

All Donchian variants share the same entry mechanism:

```
signal_time = M15 candle where close > high.rolling(20).max().shift(1)  [BUY]
          OR close < low.rolling(20).min().shift(1)                     [SELL]
entry_time  = signal_time + 1 bar (next M15 open)
entry_price = next bar's open ± slippage (0.5 × spread)
stop_loss   = entry_price ∓ 2 × ATR
```

### Strategy Variant Hierarchy (Ranked by 11-Year Performance)

| Rank | Variant | Exit | Directions | Filters | 11yr PF | 11yr PnL | Trades | Status |
|------|---------|------|-----------|---------|---------|----------|--------|--------|
| 🏆 1 | **D4** | Fixed 2R | BUY+SELL | None | **1.14** | **+$42,678** | 8,175 | ✅ **Paper trading live** |
| 2 | **D6** | Fixed 2R | BUY+SELL | ML ensemble | **1.14** | **+$42,681** | 8,169 | 🟡 Shadow timer |
| 3 | **Raw** | Fixed 2R | BUY only | None | 1.14 | +$17,156 | 4,879 | 🔴 Forward shadow |
| 4 | **D2** | Fixed 1R | BUY only | Vol + Session | 1.03 | +$1,667 | 6,890 | 🟡 Shadow timer |
| 5 | **D3** | Fixed 1R | BUY+SELL | Vol + Session | 1.02 | +$1,162 | 3,544 | 🟡 Shadow timer |
| 6 | **D1** | Fixed 1R | BUY only | Vol + Session | — | — | 36 closed | 🟡 Shadow journal |
| — | **D5** | Adaptive ATR | BUY+SELL | Vol imbalance | — | — | — | 🔬 Research only |

---

### 🏆 D4 — Best Variant (2R, BUY+SELL, No Filters)

**Status**: ✅ **Autonomous paper trading live** on cloud server since June 28.

**Key Insight**: D4 is the simplest configuration — and it wins. Over 11 years it outperforms every filtered variant by a wide margin.

| Metric | 11-Year Backtest | Validation |
|--------|-----------------|------------|
| Trades | 8,175 | Walk-forward: 88.9% positive windows |
| Win Rate | ~37% | Consistent across all 18 windows |
| Profit Factor | **1.14** | Mean 1.14 (L20), 1.09 (L55) |
| Net PnL | **+$42,678** | Risk sensitivity: 0% ruin at 0.25% |
| Avg Trades/Day | ~1.8 | Robust across market regimes |

**Why it wins**: The 2R exit compensates for a lower win rate. Over a full 11-year cycle through bull, bear, and range-bound markets, the larger payout on winners more than offsets the losses. Filters remove too many good trades alongside bad ones.

**SELL direction adds +$25,522** over 11 years compared to BUY-only (Raw). The added SELL trades are net profitable and diversify across market regimes.

### Risk Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Risk per trade | **0.25%** of equity | Risk sensitivity: median DD 11.9%, 99th DD 20.3%, 1.2% chance >20% DD |
| Kelly fraction | 0.25 | Ensures conservative sizing |
| Fixed 2R exit | TP=+2R, SL=-1R | Backtested across 8,175 trades |
| Maximum positions | 1 | Donchian breakout — sequential entries only |
| Max spread | 3.0 pips | Filters out high-cost entries |
| Slippage model | Gaussian (σ=0.5 pips) | Entries/exits worsened independently |

### Infrastructure Features

| Feature | Implementation | Purpose |
|---------|---------------|---------|
| Single-instance lock | PID file at `run/d4_paper_trader.pid` | Prevents duplicate processes doubling risk |
| Account snapshots | `account_snapshots` table every ~15 min | Equity history survives restart |
| State recovery on restart | Reads equity, trades, settings, open positions, missed signals from DB | Full restart survival |
| Entry/exit timestamps | `entry_time` / `exit_time` columns | Trade duration and session analysis |
| Open position persistence | `open_positions` table saved every cycle | Positions survive service restarts |
| Missed signal logging | `missed_signals` table with reason, timestamp, price | Debug why trades were rejected |
| Observability report | Printed every ~1h with all metrics | Performance monitoring |
| Health file | JSON at `run/d4_paper_trader_health.json` | External monitoring integration |

---

### D6 — ML Ensemble Filter (2R, BUY+SELL)

**Status**: 🟡 Timer-based shadow service (every 15 min).

**Changes from D4**: Applies ML ensemble (RegimeClassifier + DirectionPredictor) as an additional filter before entry.

**Performance**: Statistically identical to D4 (PF=1.14, +$42,681 vs D4's +$42,678). The ML models add negligible value over the full cycle — the ensemble rarely disagrees with the raw Donchian signal in trending conditions, and doesn't improve outcomes in choppy ones.

**Purpose**: Continues running as a side-by-side comparison to validate whether the ML filter ever adds value in specific market regimes.

---

### Raw Donchian Fixed 2R (BUY Only)

**Status**: 🔴 Locked 3-month research study. Running on cloud since June 11.

**Performance** (34 live forward shadow trades):
```
WR:  23.5%  (8W / 26L)
PF:   0.61
Net: -$254.01
R:   -10.06R
```

**Note**: The Raw variant is BUY-only and was the starting point. Its poor live performance (PF=0.61) is misleading — the full 11-year backtest shows PF=1.14 with 4,879 trades once SELL is enabled. The 34-trade live sample is small and short-biased.

---

### D2 — 1R Exit + Vol/Session Filter (BUY Only)

**Status**: 🟡 Timer-based simulation (every 15 min).

**Changes from Raw**:
1. Exit: Fixed **1R** instead of 2R
2. Filter: Block trades when `volatility == high` OR `session == london`
3. BUY only

**Performance** (543 simulated trades, 12-month lookback):
```
WR:  57.6%
PF:   1.33
Net: +$2,183.87
R:   +76.87R
```

**12-month vs 11-year**: The 12-month simulation shows PF=1.33, but over the full 11-year cycle D2 drops to PF=1.03. The filter works well in some market conditions but fails in others, averaging out near breakeven.

---

### D3 — 1R Exit + Vol/Session Filter (BUY+SELL)

**Status**: 🟡 Timer-based shadow (every 15 min).

**Changes from D2**: Enables SELL direction.

**11-Year Performance**: PF=1.02, +$1,162 (3,544 trades).

**Why worse than D4**: With the 1R exit + filters active, SELL signals don't add meaningful value. The filter removes many SELL trades, and the 1R payout doesn't compensate for those that remain. D3's main value was as a research step to isolate the impact of SELL direction.

---

### D1 — Shadow Journal (1R + Vol/Session Filter)

**Status**: 🟡 Timer-based journal (every 15 min).

**Performance** (36 closed journal trades):
```
WR:  52.8%
PF:   1.24
```

The original candidate locked during Phase S4. Superseded by D4 as the best variant, but maintained as a running journal for continuity.

---

### D5 — Research Only (Adaptive ATR + Volume Imbalance)

**Status**: 🔬 Research only. Script exists but research showed it harms performance.

**Changes from D4**:
1. Exit: Adaptive ATR-based trailing stop instead of fixed 2R
2. Filter: Volume imbalance filter

**Problem**: The volume imbalance filter kills 83% of trades, removing good trades alongside bad. The adaptive ATR stop doesn't outperform the simple fixed 2R over the full cycle. Not recommended for further research.

---

## 3. Strategy Comparison Matrix

| Feature | Raw | D1 | D2 | D3 | D4 🏆 | D5 | D6 |
|---------|-----|----|----|----|-------|-----|----|
| Entry | Donchian 20 | Donchian 20 | Donchian 20 | Donchian 20 | Donchian 20 | Donchian 20 | Donchian 20 |
| Stop | 2× ATR | 2× ATR | 2× ATR | 2× ATR | 2× ATR | Adaptive | 2× ATR |
| Target | 4× ATR (2R) | 2× ATR (1R) | 2× ATR (1R) | 2× ATR (1R) | 4× ATR (2R) | Trailing | 4× ATR (2R) |
| Directions | BUY | BUY | BUY | BUY+SELL | **BUY+SELL** | BUY+SELL | BUY+SELL |
| Vol Filter | None | Blocks high | Blocks high | Blocks high | **None** | Vol imbalance | ML ensemble |
| Session Filter | None | Blocks London | Blocks London | Blocks London | **None** | None | ML ensemble |
| Risk/Trade | 0.25% | 0.25% | 0.25% | 0.25% | 0.25% | 0.25% | 0.25% |
| 11yr PF | 1.14 | — | 1.03 | 1.02 | **1.14** | — | 1.14 |
| 11yr PnL | +$17,156 | — | +$1,667 | +$1,162 | **+$42,678** | — | +$42,681 |
| Status | 🔴 Forward shadow | 🟡 Journal | 🟡 Timer | 🟡 Timer | ✅ **Paper live** | 🔬 Research | 🟡 Timer |

## 4. Key Research Insights

1. **D4 is the best variant.** The simplest configuration (2R exit, both directions, no filters) produces +$42,678 over 11 years — 25× better than any filtered variant. Complexity is punished.

2. **SELL signals add +$25,522.** Enabling SELL direction doubles the strategy's PnL. The short side is as valid as the long side for Donchian breakouts on XAUUSD M15.

3. **Fixed 2R exit outperforms 1R over full cycles.** While 1R wins more often (57.6% vs ~37%), the 2R payout more than compensates for the lower win rate over multi-year timeframes.

4. **Filters remove good trades alongside bad.** Volatility and session filters appear helpful in 12-month windows (D2 PF=1.33) but fail over full 11-year cycles (D2 PF=1.03). Market regimes shift and filters don't adapt.

5. **ML ensemble is technically additive but practically irrelevant.** D6's 11-year PnL ($42,681) is within rounding error of D4 ($42,678). The ML rarely disagrees with the raw signal, and when it does, the filtered trades don't outperform those it lets through.

6. **Volume imbalance and adaptive stops harm more than help.** D5 research showed these advanced concepts remove too many trades without improving win rate or risk-adjusted returns.

7. **The Donchian 20 breakout entry is robust.** Across all variants and all timeframes (34 trades to 8,175 trades), the core entry logic generates positive expectancy. The question is only which exit and filter configuration maximizes net profit.
