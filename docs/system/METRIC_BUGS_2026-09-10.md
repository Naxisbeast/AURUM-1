# Metric Correctness Fixes — 2026-09-10

Found while reviewing the dashboard status bar, which showed:

```
Current equity: $12,017.73
Today P&L:      $2,017.73 (16.79%)     <-- wrong
```

That "Today P&L" was the **all-time** gain since the $10,000 start, not today's. It was
symptomatic of three real bugs, one of them safety-critical.

## Bug 1 (SAFETY) — `_daily_pnl` never reset at the day boundary

**Where**: `aurum1/execution/broker.py` (`PaperBroker._daily_pnl`)

**What**: `_daily_pnl` was initialised to `0.0` once at process construction and only ever
accumulated (`self._daily_pnl += net_pnl` on every close). There was **no day-boundary
reset anywhere in the codebase**. On restart it was restored verbatim from the latest
`account_snapshots` row, which likewise carried the accumulated value.

**Evidence**: with equity $12,017.73 and a $10,000 start, `daily_pnl = 2017.73` — an exact
match for all-time P&L. Trades actually closed that UTC day: 0.

**Why it matters (this is not cosmetic)**:
`daily_pnl` is the input to the **daily-loss kill switch** in two independent places:

- `aurum1/risk/manager.py` — rejects orders when `daily_pnl < -(equity * 3%)`
- `monitor/d4_watchdog.py` — stops the trader when `daily_pnl` loss exceeds `MAX_DAILY_LOSS_PCT`

Because the accumulator carried all prior profit, the switch could only trip after a loss
exceeding *all accumulated profit plus* the daily limit. With +$2,018 banked and a −$1,202
limit, a catastrophic −5% day (−$601) would leave `daily_pnl` at **+$1,417** — kill never
fires. **The daily-loss kill switch was effectively disabled.** This also silently
invalidated the 50-trade gate's "daily loss kill switch never triggers" criterion: it never
triggered because it could not.

**Fix**:
- `PaperBroker` tracks `_daily_pnl_date` and rolls `_daily_pnl` to 0 whenever a candle's
  UTC date changes (`_roll_daily_pnl_if_new_day`, called from `update_prices`).
- The paper trader's restart path only restores `_daily_pnl` from a snapshot **taken the
  same UTC day**; otherwise it starts at 0 and rebaselines to today.
- Boundary is **UTC**, matching how candles and the rest of the system are timestamped.

**Backtest safety**: `update_prices` is shared with the backtest engine, so this was
verified not to change backtest results — determinism audit is identical before and after
(8,178 trades, final equity $52,131.35 in both).

## Bug 2 — `signals_seen` was a no-op and always read 0

**Where**: `scripts/paper_trading/d4_paper_trader.py`

**What**: the only mutation in the entry path was
`self._signals_seen += 0  # already incremented above, this is a no-op placeholder`.
The promised "increment above" did not exist. Only `_missed_signals` was ever incremented,
so the health file and observability report reported `signals_seen: 0` permanently.

**Fix**: increment `_signals_seen` at the point a Donchian breakout direction is detected
(before the risk manager runs, so it counts signals that are later rejected/skipped, which
matches the intended "seen" semantics). Removed the no-op line.

## Bug 3 — evidence tracker read health-field names that do not exist

**Where**: `monitor/evidence.py`

**What**: the tracker read `health.get("uptime_hours")` and
`health.get("latest_candle_age_minutes")`, but the health file writes **`uptime_seconds`**
and **`market_latest_candle_age_minutes`**. Both lookups silently returned defaults, so the
evidence report always showed `uptime 0.0h` and never flagged stale market data.

**Fix**: read `uptime_seconds` (÷3600) and `market_latest_candle_age_minutes`.

**Not a bug elsewhere**: `monitor/metrics.py` (`load_system_health`) already normalises
both names correctly for the dashboard, so the dashboard's Uptime / candle-age panels were
never affected. The mismatch was isolated to `evidence.py`.

## Note — `peak_equity_30d` is misnamed (not fixed)

The field is named "30d" but never resets — it is a lifetime peak, and it feeds
`drawdown_pct`. Renaming would touch the `account_snapshots` schema and is deferred. Worth
knowing that `drawdown_pct: 0.0` means "at all-time high", not "calm over 30 days".

## Tests added

- `tests/test_paper_broker.py::TestDailyPnlRollover` — 4 tests (reset on new UTC day, no
  reset within a day, first-candle baseline, midnight-UTC boundary).
- `tests/test_evidence.py` — 2 tests (uptime derived from `uptime_seconds`; stale-data
  detection reads the correct key).

## Verification

- Backtest determinism: unchanged (identical trade count and equity before/after).
- `--run-once` smoke test: 148 trades, equity $11,809.68 — matches the DB.
- Affected suites: 137 passed. The 3 remaining failures
  (`test_phase1_observability` spread/slippage) are **pre-existing on `main`** and unrelated
  to these changes (confirmed by re-running against a clean tree).
