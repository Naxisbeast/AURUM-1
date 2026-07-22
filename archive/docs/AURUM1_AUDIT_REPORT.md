# AURUM-1 Full Quantitative Systems Audit

**Date:** 2026-06-29  
**Audit type:** Multi-agent structured codebase review  
**Repository:** AURUM-1 (main branch, commit 28cf594)  
**Scope:** All production Python code, config, deployment files, and test suite  

---

## Executive Verdict

| Dimension | Score | Interpretation |
|-----------|-------|----------------|
| Engineering quality | **5/10** | Good architecture, but D4 bypasses its own abstractions |
| Quant research quality | **5/10** | Thoughtful feature design, but backtest credibility unproven |
| Backtest credibility | **5/10** | 29/29 positive windows is suspicious; cost accounting unresolved |
| Risk management quality | **4/10** | Kelly doubly capped, open_risk_pct dead, no portfolio aggregation |
| ML usefulness | **2/10** | LightGBM RegimeClassifier adds ~nothing; DirectionPredictor noise |
| Paper-trading readiness | **4/10** | Ops issues moderate, but the paper trader bypasses execution engine |
| Real-money readiness | **1/10** | Not close — security, execution, and risk issues block |

### Final Status: **PAPER TRADING CANDIDATE WITH CONDITIONS**
*(NOT SAFE FOR LIVE CAPITAL — DO NOT APPROVE REAL-MONEY DEPLOYMENT)*

The system is not ready for unsupervised paper trading either. The D4 paper trader duplicates exit logic instead of using PaperBroker, has a Kelly bug that cuts risk to near-zero, and security findings (API keys in git history) need immediate remediation.

---

## Section 1: Architecture Audit

**Engineering quality: 5/10**

### Strengths
- Clean layered separation: Data → Features → Models → Signals → Risk → Execution → Backtest
- FeatureEngineer enforces column contracts and timezone-aware indices
- PaperBroker properly rebases SL/TP distances around actual fill price
- _assert_oanda_interlocks provides two-factor protection against accidental live trading

### Weaknesses

**HIGH: D4 paper trader bypasses its own execution layer.**  
`d4_paper_trader.py` implements SL/TP exit logic directly (`_check_exits`) instead of calling `PaperBroker.update_prices()` (which has its own identical SL/TP loop at `broker.py:182-210`). The paper trader calls `broker.submit_order()` directly instead of `execution_engine.execute()`, bypassing the execution engine's SQLite trade logging. This means:
- Two copies of exit logic that can diverge
- Different slippage models (fixed `self.slip_dist` in D4, Gaussian `_sample_slippage_distance` in PaperBroker)
- PaperBroker's native `update_prices` method is dead code for D4
- Trades not logged to `trades_log` table in the execution DB

*Files:* `aurum1/execution/broker.py:182-210`, `scripts/d4_paper_trader.py:164-195`

**MEDIUM: Forward shadow runner duplicates signal logic from research_runner.**  
`forward_shadow_donchian.py` has its own signal generation inline rather than importing from `donchian_research_runner.py`. This creates a maintenance burden and potential divergence.

**MEDIUM: No systemd unit files in the repository.**  
Deployment requires checking the server's `/etc/systemd/system/` directly. No `deploy/` directory, no Docker config.

---

## Section 2: Data and Feature Audit

**Data quality: 6/10**

### Strengths
- `assert_no_lookahead()` catches features that appear before their minimum lookback
- Timestamps enforced to UTC with tz-aware checks (`_validate_ohlcv_contract`)
- NaN/Inf handling: `replace([np.inf, -np.inf], np.nan)` then `raise ValueError` if NaN remains
- WARMUP_BARS=200 correctly exceeds all lookback periods (max is 200 bars for EMA_200)

### Issues

**HIGH: OANDA → yfinance silent fallback is dangerous for backtest integrity.**  
`fetch_ohlcv()` in `ingestion.py:364` silently falls back from OANDA to yfinance on any exception, with no logging of which data source was used per bar. The 11-year backtest could mix institutional-grade OANDA M15 data with consumer-grade yfinance data (which may have different timestamp precision, price accuracy, and volume data). `BacktestResult` has no `data_sources` field.

*File:* `aurum1/data/ingestion.py:364-374`, `aurum1/backtesting/engine.py` (BacktestResult dataclass)

**MEDIUM: COT and macro features are hardcoded to 0.0 when unavailable.**  
`_merge_cot` sets `cot_net_long_pct = 0.0` when COT data is empty. Similarly, macro features use `ffill().fillna(0.0)`. Feeding zeroes instead of NaN for unavailable data means models learn from artificial values during training.

*File:* `aurum1/features/engineer.py:236-247`, `aurum1/features/engineer.py:217-218`

**LOW: `assert_no_lookahead()` only checks first non-NaN appearance, not index alignment.**  
It verifies that a feature's first non-null value appears after enough source bars, but does not catch cases where feature values are computed using future data within sliding windows. The *primary path* of `_build_causal_feature_table` computes features on the full dataset at once then iterates — if any feature uses a future-centered statistic (like `shift(-5)` without safeguards), it would not be caught.

*File:* `aurum1/backtesting/engine.py:261-293`, `aurum1/features/engineer.py:28-58`

---

## Section 3: Model and ML Audit

**ML usefulness: 2/10**

### Verdict
- **RULE_ONLY** should be the default for robustness
- **RULE_REGIME** is safe but barely adds value — keep as secondary
- **FULL_ENSEMBLE** must remain experimental. Do not promote.

### RegimeClassifier

**HIGH: Label definition creates a no-man's-land around trending boundaries.**  
Labels: `ADX>25 AND ema_alignment>=3 → TRENDING_UP`, `ADX>25 AND ema_alignment<=-3 → TRENDING_DOWN`, else `RANGING`. What about `ADX=30, ema_alignment=-2`? That's a strong downtrend with nearly-aligned EMAs, classified as RANGING. The classifier's job is to learn from the training data that this should be TRENDING_DOWN, but the label itself has already discarded that information. The model can never be better than the label quality, and these labels are low-resolution.

*File:* `aurum1/models/regime_classifier.py:84-90`

**MEDIUM: `_CentroidClassifier` fallback is a dimensional disaster.**  
When LightGBM is not installed (no error raised, just `except ImportError`), the system uses nearest-centroid classification in 7-dimensional feature space with centroids computed from nans (as `np.nanmean`). This fallback silently runs with zero accuracy guarantees.

*File:* `aurum1/models/regime_classifier.py:36-67, 242-260`

**MEDIUM: Validation Sharpe of 0.85 does not translate to trading edge.**  
The regime label "validation Sharpe" is computed as `directional_sharpe(forward_return_5bar * direction)` — it measures whether *predicted regime labels* (not trading signals) align with forward returns. This is not the same as trading performance. A regime classifier that correctly identifies "trending up" still doesn't guarantee profitable entry timing.

*File:* `aurum1/models/regime_classifier.py:273-277`

### DirectionPredictor
The project's own memory says "limited predictive power." The DirectionPredictor uses a `SoftmaxSequenceModel` (centroid-based), which in practice produces near-random signals. It should not be used. The walk-forward silently catches exceptions from training it (`walk_forward.py:91: pass`), meaning many walk-forward windows may run without it.

### Sentiment Model
`sentiment_model.py` is essentially a placeholder. News API is behind `ALPHA_VANTAGE_API_KEY` which is likely unset, and the sentiment processing is basic keyword matching. All sentiment features end up as zeros in practice.

---

## Section 4: Trading Logic and Signal Audit

**Signal logic: 6/10**

### Strengths
- State machine transitions (SCANNING → ARMED → WINDOW_OPEN → EXECUTE) are cleanly separated
- Breakout buffer (`atr_breakout_buffer=0.3 * ATR`) is reasonable for gold ($4.50 on $2000 with $15 ATR)
- Armed timeout of 20 candles (5 hours) and window expiry of 6 candles (1.5 hours) are appropriate for M15

### Issues

**MEDIUM: Pullback detection is too simple.**  
`_is_pullback` defines pullback as `close < open` for BUY, `close > open` for SELL. This means any red candle during a BUY setup counts as a pullback, including minor intra-bar wiggles. With `min_pullback_candles=1`, the state machine can transition from ARMED to WINDOW_OPEN after a single red candle, even if that candle's low didn't retrace meaningfully.

*File:* `aurum1/signals/state_machine.py:206-211`

**MEDIUM: Session filter requires London OR NY (not both).**  
`session_london=1 or session_ny=1` allows trading during either session. This is ~15 hours/day, which is nearly all active hours. The filter removes Asian session (~9 hours), so removes ~37% of bars. Whether this improves or hurts was tested in D2 and found to degrade 11-year results, yet it's still the default (`require_session_filter=true`).

*File:* `aurum1/signals/state_machine.py:125`, `aurum1/config/settings.yaml:108`

**LOW: ADX threshold of 25 is convention, not empirical.**  
The code uses `ADX > 25` as the trend filter. This is the standard Wilder recommendation, but it's not tested against gold on M15. If gold on M15 has mean ADX of 22, this filter removes most bars.

---

## Section 5: Risk Management Audit

**Risk management quality: 4/10**

### CRITICAL: Kelly fraction doubly capped → positions near zero.

`_kelly_fraction` applies TWO caps:
1. `min(full_kelly * kelly_cap, kelly_max_fraction)` → capped at `kelly_max_fraction=0.25`
2. But `kelly_cap=0.25` and `kelly_max_fraction=0.25`

For XAU/USD with WR~37% and avg_win/avg_loss ratio ~1.9:
```
full_kelly = 0.37 - (1-0.37)/1.9 = 0.37 - 0.33 = 0.04
kelly_fraction = min(0.04 * 0.25, 0.25) = min(0.01, 0.25) = 0.01
```

The effective fraction is **0.01** (1% of full Kelly). On $10k equity with $25 base risk, positions risk **$0.25**. For a 2 ATR stop on gold at $2000 with $15 ATR = $30 stop distance:
```
units = $0.25 / ($30 * 1) = 0.008 units → rounds to 0 (minimum is 1 unit)
```

This means **no trade can ever be taken** in real Kelly mode. Below 20 trades (default mode), risk = $25 → $25/$30 = 0.83 units → also 0. The system never trades after full Kelly kicks in.

*File:* `aurum1/risk/manager.py:109-127`, `aurum1/config/settings.yaml:84-87`

### HIGH: Kelly uses dollar PnL instead of R-multiple.

`_realised_trade_pnl` returns dollar PnL. Kelly's original formula expects returns from a binary outcome (win/loss with consistent magnitude). Using dollar PnL means large winners (from many units) and small winners (from few units) are mixed, distorting the win/loss ratio. Should use R-multiple or % return.

*File:* `aurum1/risk/manager.py:190-195`

### HIGH: `open_risk_pct` is always 0.0 in both brokers.

`PaperBroker.get_account_state()` returns `open_risk_pct=0.0`. `OandaBroker.get_account_state()` also returns `open_risk_pct=0.0`. The portfolio risk check (`max_portfolio_risk_pct=3.0` and `initial_projected` budget) is never accurate because it counts only the proposed trade, not existing open positions.

*File:* `aurum1/execution/broker.py:220`, `aurum1/execution/broker.py:432`

### MEDIUM: Kill switches are static percentages, not dynamic.

Daily loss kill at -3% and total drawdown at -8% of 30-day peak are reasonable for buy-hold gold (which can drop 5% in a day), but they should be calculated from the strategy's statistical distribution, not static percentages. A strategy with 37% WR and 2R exits naturally has larger peak-to-trough moves than these thresholds allow.

---

## Section 6: Execution Audit

**Execution quality: 4/10**

### CRITICAL: D4 paper trader duplicates exit logic outside PaperBroker.

See Section 1. The paper trader's `_check_exits` and `_check_entries` methods replicate (with slight differences) what `PaperBroker.update_prices()` already does. This means:
- D4 paper trader calls `broker.submit_order()` directly — no execution engine logging
- `PaperBroker.update_prices()` is never called by D4 — PaperBroker's internal SL/TP checking is dead code
- Two different slippage models exist for the same system

### HIGH: `abs(gauss)` slippage creates a folded-normal distribution.

`_sample_slippage_distance` returns `abs(gauss(0, 0.5 pips))`. This folded-normal distribution has **zero probability of zero slippage** and **always hurts the trader**. Real slippage can be zero (in liquid markets with limit orders) and can be negative (price improvement). This systematically overstates costs.

*File:* `aurum1/execution/broker.py:316-322`

### HIGH: Spread cost formula may double-count.

`PaperBroker._spread_cost` calculates `2 * spread_pips * pip_value * units`. The docstring says "2x spread (half on entry, half on exit)." The `PaperBroker._close_position_at_price` also calculates `spread_cost` using the same formula `2 * spread_pips * pip_value * units` and subtracts it from gross PnL. But then `_augment_trade` in `engine.py` recalculates fee again as `2 * paper_spread_pips * pip_value * units`. Depending on the code path, spread cost may be applied 1×, 2×, or 3×. This must be traced end-to-end.

### MEDIUM: OANDA limit orders treated as fill-timeout.

`OandaBroker.submit_order` sends LIMIT orders. If the order doesn't fill immediately, the response has no `orderFillTransaction` and the system returns `fill_timeout`. OANDA limit orders remain working until cancelled or filled — the system should poll or accept the order as pending. For a system that enters at breakout levels, the limit may not fill for several candles, which is expected.

*File:* `aurum1/execution/broker.py:471-483`

---

## Section 7: Backtesting and Math Audit

**Backtest credibility: 5/10**

### CRITICAL: _build_causal_feature_table may leak data.

There are TWO code paths in this method:

1. **Primary path (try):** `FeatureEngineer.build_features(ohlcv, macro, cot, htf_frames)` builds features on the **full** dataset at once. Even though rolling statistics like `EMA(20)` only look backward when correctly implemented, pandas' `rolling()` and `ewm()` functions are causal **only if** the implementation doesn't use `center=True` or other non-causal parameters. The code passes the *full* OHLCV frame including future bars — any feature that uses future data would leak. The `assert_no_lookahead` check runs *after* the feature table is built, but only checks first-non-null appearance.

2. **Fallback path (except):** Builds features incrementally bar-by-bar, which is clearly causal but orders of magnitude slower.

The existence of these two paths means the primary path *can* silently pass with potential leakage while the fallback path is different. They should produce identical results. They should be verified.

*File:* `aurum1/backtesting/engine.py:261-293`

### HIGH: 29/29 positive walk-forward windows for Donchian 20 is suspicious.

Settings: `train_bars=33264 (~1.5yr on M15), test_bars=11088 (~6mo), step_bars=11088`. With `allow_overlap=false`, windows are non-overlapping. 29 positive-Sharpe windows out of 29 on a simple Donchian 20 breakout over 11 years of gold strongly suggests one of:
1. **Leakage through _build_causal_feature_table's full-dataset path** (above)
2. **Fee double-counting** inflating metrics by suppressing equity
3. **The strategy genuinely works on gold, but this consistency is suspicious** for a strategy with 37% WR

The probability of 29/29 positive windows by chance for a strategy with true Sharpe=0 is 0.5^29 ≈ 1.86 × 10^-9. Even for a strategy with Sharpe=0.5, the probability of 29/29 positive is only ~0.69^29 ≈ 0.0001%.

### HIGH: Fee accounting chain is unresolved.

The system has three places where fees/spread/slippage are calculated:
1. `PaperBroker._close_position_at_price` (broker.py:234-236)
2. `_augment_trade` (engine.py:694-744)
3. `_fee_adjusted_equity_curve` (engine.py:768-779)

It is unclear whether `PaperBroker` deducts costs from its internal equity balance (via `self._balance += net_pnl` where `net_pnl = gross_pnl - spread_cost`). If it does, and then `_fee_adjusted_equity_curve` also subtracts fees, costs are double-counted. If it does not, then `_augment_trade` correctly assigns them but the equity curve is unadjusted.

The test is simple: `eq(total_net_pnl, final_equity - initial_equity)` should hold. This assertion does not exist.

### MEDIUM: Shape ratio uses daily resampling on intraday data.

`_daily_returns` resamples the intraday equity curve to daily via `resample('1D').last().pct_change()`. This:
- Loses intraday information (multiple trades in one day become one daily return)
- Misses gap risk (overnight moves between last bar and next day's open)
- Uses `sqrt(252)` annualization which assumes zero autocorrelation and IID returns

For a strategy trading ~8 trades/week on M15, daily resampling is throwing away most of the trade-level information. Better to use trade-level returns or hourly returns with appropriate annualization.

*File:* `aurum1/backtesting/engine.py:789-803`

### MEDIUM: Fixed random seed (42) across all walk-forward windows.

The Monte Carlo resampling and any stochastic elements in models (LightGBM uses `random_state=seed`) use the same seed across all walk-forward windows. This means each window gets the same "random" number sequence, reducing the independence of windows.

---

## Section 8: Test Suite Audit

**No structured test assessment was performed** due to the synthesis agent being interrupted mid-response.

### Notable observations from code:
- `tests/` has 21 test files covering all phases
- Tests validate individual module behavior but do not test end-to-end integration
- No tests for: `PaperBroker.update_prices` → SL/TP logic, Kelly formula correctness at `risk/manager.py`, data source mixing detection, accidental live trading interlocks
- No performance/benchmark tests (no baseline for latency or memory)

---

## Section 9: Deployment and Operations Audit

**Ops readiness: 4/10**

### Issues

**MEDIUM: No WAL mode on SQLite databases.**  
The forward shadow service writes to `forward_shadow_market_cache.sqlite3` once per minute while the D4 paper trader reads from it. Without WAL mode and with the default journal mode (rollback), read queries will block or fail during writes. SQLite supports WAL mode which allows concurrent reads during writes.

**MEDIUM: No systemd files in repo.**  
Deployment relies on manually transferring service/timer files to the server. No `deploy/` directory, no Makefile, no deployment script.

**MEDIUM: Log rotation not configured.**  
`forward_shadow_donchian.py` uses `RotatingFileHandler` in its own setup, but the D4 paper trader has no log rotation. It prints to stdout (captured by journald), which can grow unbounded.

**LOW: Health endpoint referenced but not implemented.**  
The systemd unit for D4 paper trader doesn't expose a health endpoint, but `orchestrator.py` has health port 8080. No monitoring endpoint exists for the paper trader.

---

## Section 10: Security Audit

**Security score: 2/10**

### CRITICAL: API keys may be in git history.

The `.env` file with OANDA practice API key and FRED API key was tracked in git before `.gitignore` was updated. Even if removed now, the keys exist in prior commits. Anyone with repo access can extract them. **Keys must be rotated immediately.**

### MEDIUM: SQLite databases in the repository.

The `.sqlite3` files contain trade history, account equity, and strategy parameters. If they ever were committed (even accidentally), financial data leaks to anyone with repo access. Check `git log --all --diff-filter=A -- '*.sqlite3'`.

### STRENGTH: `_assert_oanda_interlocks` works correctly.

Requires `ALLOW_OANDA_ORDERS=true` for any OANDA access, and `ALLOW_LIVE_TRADING=true` for live. With `ALLOW_OANDA_ORDERS=false`, the paper trader cannot accidentally send real orders.

### STRENGTH: Dashboard on 127.0.0.1, not publicly exposed.

---

## Section 11: Improvement Roadmap

### A. CRITICAL — Fix before paper trading continues

| # | Issue | File | Type | Est. Effort |
|---|-------|------|------|-------------|
| 1 | Rotate leaked API keys, scrub git history | repo-wide | Security | 1 hour |
| 2 | Fix Kelly double-cap: `kelly_max_fraction` should be the cap, not both | `risk/manager.py:109-127` | Math/Risk | 15 min |
| 3 | Integrate D4 paper trader with `PaperBroker.update_prices()` or remove duplicate exit logic | `d4_paper_trader.py:164-195` | Execution | 2 hours |
| 4 | Trace fee accounting chain end-to-end, add `eq(total_net_pnl, final_equity - initial_equity)` assertion | `backtesting/engine.py`, `execution/broker.py` | Math | 1 hour |
| 5 | Run walk-forward with incremental feature builder only (not full-dataset path) | `backtesting/engine.py:261-293` | Backtest | 2 hours |

### B. IMPORTANT — Fix during paper trading

| # | Issue | File | Type | Est. Effort |
|---|-------|------|------|-------------|
| 6 | Enable WAL mode on all SQLite databases | `data/ingestion.py`, `d4_paper_trader.py` | Ops | 15 min |
| 7 | Change `abs(gauss)` slippage to gaussian (allow zero and negative slippage) | `execution/broker.py:316-322` | Execution | 15 min |
| 8 | Use R-multiple for Kelly calculation, not raw dollar PnL | `risk/manager.py:190-195` | Risk | 30 min |
| 9 | Fix `open_risk_pct` in both brokers to report actual open risk | `execution/broker.py:220,432` | Risk | 30 min |
| 10 | Add `MachineMode.RULE_ONLY` as the default operating mode | `signals/state_machine.py`, `config/settings.yaml` | Logic | 15 min |

### C. REQUIRED — Before any real-money deployment

| # | Issue | File | Type |
|---|-------|------|------|
| 11 | Add WAL mode persistence and crash recovery | All DB accesses | Ops |
| 12 | Add automated deployment with systemd files in repo | `deploy/` directory | Ops |
| 13 | Add health endpoint monitoring | `d4_paper_trader.py` | Ops |
| 14 | Add stale-data detection (no new candles in >2 hours = alert) | `d4_paper_trader.py` | Ops |
| 15 | Remove hardcoded COT/macro zeros, use NaN with model-side handling | `features/engineer.py:236-247` | Data |
| 16 | Tag data source per bar in BacktestResult, abort on mixed sources | `backtesting/engine.py`, `data/ingestion.py` | Backtest |
| 17 | Add OANDA limit order polling (don't timeout after 3 candles) | `execution/broker.py:471-483` | Execution |

### D. RESEARCH — After stability

| # | Issue | File | Type |
|---|-------|------|------|
| 18 | Replace ADX/EMA regime labels with volatility-regime labels (low/medium/high vol) | `models/regime_classifier.py:84-90` | ML |
| 19 | Test pullback as %-retracement of ARMED candle range, not red/green | `signals/state_machine.py:206-211` | Signal |
| 20 | Reject FULL_ENSEMBLE promotion until it demonstrates >5% improvement over RULE_ONLY on 3+ years OOS | `models/ensemble.py`, retrainer | ML/Mgmt |
| 21 | Test 55-bar Donchian lookback (from research dossier) | scripts | Research |
| 22 | Add R-multiple distribution to BacktestResult (not just PnL) | `backtesting/engine.py` | Math |

---

## Section 12: Final Professional Opinion

### What is genuinely strong
- **Causal design intent is clear** — the system was built by someone who understands lookahead bias, timezone handling, and modular design
- **Forward shadow with SHA-256 ledger hashing** is excellent infrastructure for auditability — most systems don't have this
- **Interlocks** (`_assert_oanda_interlocks`, paper_initial_equity, `ALLOW_LIVE_TRADING`) show real ops awareness
- **Feature engineer contracts** (DatetimeIndex, UTC, column validation) are professional-grade guardrails

### What is weak or fragile
- **D4 was clearly rushed.** The paper trader duplicates logic from PaperBroker, bypasses the execution engine, and has its own slippage model. This is the biggest code-quality concern.
- **Kelly math is broken.** Double-cap means the system is essentially sizing to zero after 20 trades. This is a mathematical error, not a design choice.
- **Walk-forward credibility is unproven.** 29/29 positive windows is too clean for a 37% WR strategy on gold. Must be independently verified with incremental feature construction.

### The biggest hidden risk
**Fee double-counting inflating backtest metrics.** If the `_fee_adjusted_equity_curve` subtracts fees that PaperBroker already deducted from equity, then the reported PnL, Sharpe, and drawdown are systematically wrong. Every downstream metric would be affected. This must be resolved before any backtest-based decision is trusted.

### The biggest technical risk
**SQLite without WAL mode under concurrent read/write.** The forward shadow service writes to the market cache once per minute. The D4 paper trader (and potentially shadow services) read from the same cache. Without WAL mode, reads can fail during writes, causing missed candles or data corruption.

### The biggest quant/statistical risk
**The strategy works on gold because gold trends persistently, not because Donchian 20 is an edge.** Gold on M15 has extended trends with low noise relative to ATR. A simple breakout system will catch some of these trends by chance. The 29/29 walk-forward windows may reflect gold's persistence rather than strategy skill. A proper test is: does the strategy work on a non-trending instrument or during gold's range-bound periods?

### The biggest operational risk
**No alerting for stale data or system failure.** If the forward shadow service stops fetching data (e.g., OANDA outage), the paper trader continues running but processes stale candles (no new OHLCV = no change = no signals). The system would appear "healthy" (PID running, memory nominal) while producing no trades. Days could pass before someone notices.

### What I would personally refuse to approve
- Real-money deployment at any account size. Not close.
- Full-ensemble promotion without evidence.
- Unsupervised paper trading without fixing the Kelly double-cap and exit-logic duplication.

### What I would personally approve for paper trading
- Current state, **with conditions**: rotate API keys first, fix Kelly double-cap, verify fee chain, enable WAL mode. Run in RULE_ONLY mode. Monitor weekly.

### What exact evidence would make me comfortable moving to micro live trading
1. Forward shadow D4 trades independently reconciled against backtester D4 over the same 3-month period — entries, exits, R-multiples all match within expected slippage bounds
2. Kelly produces position sizes ≥ 1 unit for $10k equity (i.e., the Kelly bug is fixed)
3. 6+ months of paper trading with: PF ≥ 1.05, positive PnL, max DD ≤ 10%, < ${VAR} peak-to-trough, and no more than ${N} system failures
4. Walk-forward replicated with incremental feature builder — confirming 80%+ positive windows
5. Stress test: 2× and 3× spread costs still produce PF ≥ 1.0

---

## Immediate Action Checklist

| Pri | Action | File/Module | Severity | Effort |
|-----|--------|-------------|----------|--------|
| 1 | **Rotate leaked API keys, scrub git history** | repo-wide | 🔴 CRITICAL Security | 1h |
| 2 | **Fix Kelly double-cap** | `risk/manager.py:122-126` | 🔴 CRITICAL Risk | 15m |
| 3 | **Trace and fix fee accounting chain** | `backtesting/engine.py`, `execution/broker.py` | 🔴 CRITICAL Math | 1h |
| 4 | **Verify walk-forward with incremental features** | `backtesting/engine.py:261-293` | 🔴 CRITICAL Backtest | 2h |
| 5 | **Remove D4 duplicate exit logic or integrate with PaperBroker** | `d4_paper_trader.py:164-195` | 🟠 HIGH Execution | 2h |
| 6 | **Enable WAL mode on all SQLite databases** | `data/ingestion.py`, `d4_paper_trader.py` | 🟠 HIGH Ops | 15m |
| 7 | **Change slippage from folded-normal to true gaussian** | `execution/broker.py:316-322` | 🟠 HIGH Execution | 15m |
| 8 | **Fix open_risk_pct in both brokers** | `execution/broker.py:220,432` | 🟠 HIGH Risk | 30m |
| 9 | **Use R-multiple for Kelly, not dollar PnL** | `risk/manager.py:190-195` | 🟠 HIGH Risk | 30m |
| 10 | **Add back `forward_shadow_donchian_d5.py` to repo** or document as research-only | repo | 🔵 MEDIUM Research | 15m |
