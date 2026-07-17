# Phase 0 Complete — Summary

All 8 Phase 0 tasks from the game plan have been implemented.

## Task 0.1: Fix Kelly Double-Cap Bug (CRITICAL)
**Files changed:** `aurum1/risk/manager.py`, `aurum1/config/settings.yaml`
**What:** Removed `kelly_cap: 0.25` multiplier. Kelly now applies a single cap at `kelly_max_fraction: 0.25`. Also aligned `risk_per_trade_pct` from 1%→0.25% to match D4's actual risk.
**Before:** D4's 37% WR, 2:1 WLR → 0.01375 Kelly → <1 unit → **zero trades**
**After:** D4's 37% WR, 2:1 WLR → 0.055 Kelly → meaningful position sizes

## Task 0.2: Fix open_risk_pct in OandaBroker
**Files changed:** `aurum1/execution/broker.py`
**What:** OandaBroker.get_account_state() now computes open_risk_pct from open position unrealized PnL. PaperBroker already had this correct.

## Task 0.3: Rotate API Keys / Scrub Git History
**No files changed.**
**Result:** No API keys were ever committed — only `.env.example` with empty placeholders. No rotation needed. No SQLite databases committed.

## Task 0.4: Enable WAL Mode on All SQLite Databases
**Files changed:** `scripts/shadow/forward_shadow_donchian.py`
**What:** Added `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=NORMAL` to all 4 connection points in the forward shadow service (init, write_state, record_event, status_query). The `initialize_database` function in ingestion.py and D4's `_init_paper_db` already had WAL.

## Task 0.5: Re-evaluated Spread Cost Double-Count
**No files changed.**
**Finding:** After tracing the full fee chain, PaperBroker's `_spread_cost()` is correct — it uses mid-prices and charges spread separately. The real issue was in `_augment_trade` in the backtest engine (fixed in 0.6).

## Task 0.6: Trace and Fix Fee Accounting Chain
**Files changed:** `aurum1/backtesting/engine.py`
**What:**
- `_augment_trade` no longer recalculates `fee` when PaperBroker already set it (was using hardcoded 1.5 pip spread)
- Added `AURUM1_STRICT_FEE_CHECK=1` env var that makes fee discrepancies a hard assertion error instead of a warning
- The existing fee integrity check (total_net_pnl ≈ initial + final equity) was already there — good

## Task 0.7: Fix D4 Execution Duplication
**Files changed:** `scripts/paper_trading/d4_paper_trader.py`
**What:**
- Removed hardcoded `self.slip_dist` from D4's entry price (was adding 0.5 pips slippage on TOP of PaperBroker's Gaussian slippage — double-slippage bug)
- D4 entry price is now the raw signal price; PaperBroker handles all slippage
- Removed unused `self.sp`, `self.slip_pips`, `self.slip_dist` initialization
- Documented the change with comments

## Task 0.8: Add Stale Data Alerting
**Files changed:** `scripts/paper_trading/d4_paper_trader.py`
**What:**
- Added `_send_alert()` method that sends webhook to `ALERT_WEBHOOK_URL` env var (Slack/Discord/Telegram compatible)
- Existing stale data detection (already implemented at 2-hour threshold) now triggers the webhook
- Alerting is silent if `ALERT_WEBHOOK_URL` is not set — backward compatible
