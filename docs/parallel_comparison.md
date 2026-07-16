# Parallel Comparison: D4 (20-bar) vs D7 (10-bar)

## The Setup

I'm running both variants side-by-side on the server. Same data source, same exit logic (2R), same risk (0.25%), no filters. The only difference: D4 uses a 20-bar Donchian lookback, and D7 uses a 10-bar lookback.

Both are shadow-only — they don't trade or submit orders. They just read the same market cache every 15 minutes and log their decisions to the journal.

## Why I'm Doing This

My backtests showed the 10-bar beats the 20-bar across every metric:
- **PF**: 1.204 vs 1.156 (+4.1%)
- **PnL**: +$152k vs +$58k (+163%) — same $10k start
- **WR**: 37.9% vs 37.0%
- **Avg R**: 0.128 vs 0.099 (+29%)

But backtests aren't reality. Let me collect real data and see.

## Services

| Variant | Lookback | Service | Timer |
|---------|----------|---------|-------|
| D4 (current) | 20-bar | `aurum1-d4-shadow.service` | Every 15min |
| **D7 (10-bar)** | **10-bar** | **`aurum1-d7-shadow.service`** | **Every 15min** |

## How To Check

```bash
# Latest D7 run
journalctl -u aurum1-d7-shadow.service -n 20 --no-pager

# Compare all shadow variants at once
python scripts/shadow/forward_shadow_donchian_d7.py  # D7
python scripts/shadow/forward_shadow_donchian_d4.py   # D4

# D7 runs from the forward shadow cache
sqlite3 aurum1/data/forward_shadow_market_cache.sqlite3 "SELECT MAX(timestamp), COUNT(*) FROM ohlcv_M15;"
```

## What I'm Watching

After a few weeks of parallel data I'll know which one actually holds up. The key question: does the 10-bar's higher backtest PF translate to better real-world performance, or was it overfitted to the 2016-2026 period?

My bet is on the 10-bar. But let the data decide.
