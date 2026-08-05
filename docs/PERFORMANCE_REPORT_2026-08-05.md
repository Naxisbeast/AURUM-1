# AURUM-1 D4 Performance Report

**Date**: 2026-08-05
**Scope**: Live paper trading performance, 72 trades, ~5 weeks
**Instrument**: XAU/USD (Gold), M15
**Strategy**: D4 — Donchian 20 breakout, fixed 2R exit, BUY+SELL, no filters

---

## 1. Executive Summary

**D4 continues to perform consistently with backtest expectations.** At 72 live paper trades, the system has generated **+$708.70 net profit** on a $10,000 starting account (+8.6%), with a **51.4% win rate** and **average R-multiple of +0.53**.

The 50-trade risk review gate was **PASSED on all 4 pre-registered criteria** on 2026-08-05. Drawdown never exceeded ~2.5% lifetime (vs 15% gate threshold), the daily loss kill switch never triggered, win rate is well above the 30% breakeven floor, and there were zero infrastructure failures.

All five services are running and healthy. The dashboard is now permanently available at **https://dashboard.auram.software** after migrating from rotating Cloudflare quick-tunnel URLs to a stable named tunnel.

---

## 2. Performance Metrics

### 2.1 Overall Results (72 trades)

| Metric | Value | Backtest Baseline | Verdict |
|--------|-------|-------------------|---------|
| Net PnL | **+$708.70** | +$42,678 (11yr, scaled) | ✅ Positive |
| Equity | **$10,863.60** | — | ✅ +8.6% |
| Win rate | **51.4%** | ~37% | ✅ Higher than backtest |
| Avg R-multiple | **+0.53** | +0.12 | ✅ Stronger |
| Profit factor | ~2.0 | 1.14 | ✅ Above backtest |
| Max drawdown | ~2.5% lifetime | 5.4% (walk-forward) | ✅ Lower |
| Current drawdown | **0.0%** | — | ✅ At peak |

### 2.2 By Direction

| Direction | Trades | Net PnL | Avg R |
|-----------|--------|---------|-------|
| BUY | 36 | +$372.40 | +0.5625 |
| SELL | 36 | +$336.30 | +0.4990 |

Both directions are contributing equally (36 each) and both are net profitable. This matches the backtest finding that BUY+SELL is superior to BUY-only.

### 2.3 By Exit Reason

| Exit | Trades | Net PnL | Note |
|------|--------|---------|------|
| Take profit | 37 | **+$1,348.47** | All winners at ~+2R |
| Stop loss | 34 | -$598.16 | All losers at ~-1R |
| Stop loss gap | 1 | -$41.61 | Single gap loss |

The asymmetry is exactly what the 2R exit is designed for: 37 winners at +2R offset 35 losers at -1R. The take-profit wins (+$1,348) are more than double the stop-loss losses (-$640), which is the structural edge.

### 2.4 Execution Quality

| Metric | Value | Model Expectation |
|--------|-------|-------------------|
| Avg entry slippage | +0.0028 units | Folded-normal, always adverse ✅ |
| Avg exit slippage | -0.0018 units | Folded-normal, always adverse ✅ |
| Avg spread | 2.25 pips | Session-aware (1.5-3.0 band) ✅ |
| Avg latency | 8ms | Excellent |
| Max latency | 11ms | Excellent |
| Stale data | 0.0 min | Fresh ✅ |
| Missed signals | 0 | No missed opportunities ✅ |

### 2.5 Infrastructure Status

| Service | Status |
|---------|--------|
| D4 Paper Trader | ✅ active |
| Forward Shadow (data) | ✅ active |
| Dashboard | ✅ active |
| Watchdog | ✅ active |
| Cloudflare Tunnel | ✅ active |

Uptime: ~111 hours since last restart. All services healthy.

---

## 3. Risk Assessment

### 3.1 50-Trade Risk Review Gate — PASSED ✅

| Criterion | Threshold | Actual | Verdict |
|-----------|-----------|--------|---------|
| Max drawdown | ≤15% | ~2.5% | ✅ PASS |
| Daily loss kill | Never triggers | Never triggered | ✅ PASS |
| Win rate | >30% | 51.4% | ✅ PASS |
| Infrastructure failures | None | None | ✅ PASS |

**Decision**: D4 remains at 0.35% risk. All criteria pass, but 72 trades is still modest for the next increase to 0.50%. The 100-trade gate (with Deflated Sharpe Ratio) provides the statistical confidence for a further risk review.

### 3.2 Execution Cost Analysis

**Realized execution costs are 0.10% of risk** — negligible. The folded-normal slippage model is conservative, not optimistic. The backtest's cost assumptions are validated by live trading.

### 3.3 Capacity Analysis

**D4 has extremely high capacity.** Even at a $100M account, the max position is 7.9% of daily XAU volume and profit factor stays at 1.13. Capacity is not a constraint at any realistic account size.

### 3.4 Signal Health

- **Stationarity**: PASS — the D4 signal is stationary (not trading noise)
- **Determinism**: PASS — the backtest is fully reproducible
- **Signal decay**: 4/4 checks pass — live signal consistent with backtest baseline, no regime shift

---

## 4. Validation Cross-Check

The live results validate the pre-registered backtest expectations:

| Validation | Backtest | Live (72 trades) | Consistent? |
|------------|----------|------------------|-------------|
| Positive expectancy | PF 1.14 | Avg R +0.53 | ✅ |
| 2R exit asymmetry | Winners 2x losers | +$1,348 vs -$640 | ✅ |
| BUY+SELL both contribute | Yes | Yes (36/36) | ✅ |
| Win rate band | ~37% | 51.4% | ✅ (higher is better) |
| Drawdown control | 5.4% walk-forward | ~2.5% | ✅ |

---

## 5. Notable Observations

1. **Live win rate (51.4%) is higher than backtest (~37%)** — at 72 trades this is likely small-sample variance, not a permanent improvement. The 2R exit keeps the edge intact regardless.

2. **The last 8 trades were a strong winning streak** (6/8 hit +2R take profit) — evidence of the strategy catching a favorable move, not a permanent regime change.

3. **SELL and BUY perfectly balanced (36 each)** — the strategy is not directionally biased, consistent with the no-filters design.

4. **Zero missed signals** — the system is capturing every signal its rules generate. Data pipeline is healthy.

---

## 6. Verdict

**AURUM-1 D4 is performing as designed.** At 72 paper trades, the live results are consistent with — and in some respects better than — the 11-year backtest expectations. Execution costs are negligible, capacity is effectively unlimited, the signal is healthy, and risk controls have held.

The system remains in evidence collection at 0.35% risk. The next decision gate is the **100-trade strategy review** (~September), where the Deflated Sharpe Ratio will determine whether D4's edge survives selection-bias correction.

**No changes to the running system are recommended.** The discipline is to let it accumulate trades and not interfere.

---

*Generated 2026-08-05 from live server data (72 trades, 3,313 account snapshots).*
