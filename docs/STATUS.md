# AURUM-1 System Status

**Last updated**: 2026-06-28

## Operational Status

| Component | Status | Details |
|-----------|--------|---------|
| Main Orchestrator | **STOPPED** | Last run May 27 2026, killed by signal_2. Not restarted. |
| Forward Shadow (Raw Donchian 2R) | ✅ **ACTIVE** | Running since June 11. PF=0.61, WR=23.5% (34 trades) |
| D1 Shadow Journal | ✅ **TIMER ACTIVE** | Every 15 min. WR=52.8%, PF=1.24 (36 closed) |
| D2 Shadow (1R + filter) | ✅ **TIMER ACTIVE** | Every 15 min. Simulated 543 trades. PF=1.33, WR=57.6% |
| Dashboard | **STOPPED** | Not deployed on cloud server |
| Daily Backups | ✅ **ACTIVE** | 28 daily backups, growing from 152K to 82MB |
| Weekly Reports | ✅ **ACTIVE** | 9 reports generated to date |

## Live Performance — Forward Shadow

```
Equity:    $10,000.00 → $9,745.99 (-2.54%)
Trades:    34
Winners:   8 (all +2.0R take_profit)
Losers:    26 (all -1.0R stop_loss)
PF:        0.61
WR:        23.5%
```

## D2 Simulated Performance (12-month lookback)

```
Equity:    $10,000.00 → $12,183.87 (+21.8%)
Trades:    543
Winners:   313 (all +1.0R)
Losers:    225 (all -1.0R)
PF:        1.33
WR:        57.6%
```

## Server

| Detail | Value |
|--------|-------|
| Host | `aurum1-paper-server` (178.105.245.66) |
| OS | Ubuntu 24.04.4 LTS |
| Disk | 38GB total, 53% used |
| Memory | 3.7GB total, ~11% used |
| Python | 3.12.3 |
| Working directory | `/opt/aurum1` |

## Key Decisions

- **May 27**: Main orchestrator shut down (signal_2). Not restarted. Data ingestion via yfinance was failing.
- **June 11**: Forward shadow service deployed with D1 timer.
- **June 28**: D2 shadow deployed alongside existing services.

## Next Actions

1. Monitor D2 shadow performance over next week
2. Convert D2 from simulation to real-time forward shadow if PF stays > 1.20
3. Consider restarting main orchestrator with corrected data ingestion (OANDA instead of yfinance)
4. Enable SELL signals in D2 variant
