# AURUM-1 Deployment Summary

**Date**: 2026-06-28
**Server**: aurum1-paper-server (178.105.245.66)

---

## Services Deployed

| Service | Strategy | Exit | Filters | Status |
|---------|----------|------|---------|--------|
| **forward-shadow** | Raw Donchian 20 | Fixed 2R | None | ✅ **Active** (continuous) |
| **D1 shadow** | Donchian 20 (D1 filter) | Fixed 1R | Vol != high, Session != London | ✅ **Timer** (every 15min) |
| **D2 shadow** | Donchian 20 (D2 filter) | Fixed 1R | Vol != high, Session != London | ✅ **Timer** (every 15min) |

## Current Performance Comparison

| Metric | Raw (2R, no filter) | D1 (1R, filtered) | D2 (1R, filtered) |
|--------|--------------------|--------------------|--------------------|
| Trades | 34 | 36 (closed) | 542 (simulated) |
| Win Rate | 23.5% | **52.8%** | **57.6%** |
| Profit Factor | 0.61 | **1.24** | **1.32** |
| Net R | -10.06R | — | **+75.88R** |
| Net PnL | -$254.01 | — | **+$2,144.19** |

## What Changed on the Server

### 1. D2 Shadow Service Deployed ✅
- **Script**: `/opt/aurum1/scripts/forward_shadow_donchian_d2.py`
- **Service**: `aurum1-d2-shadow.service` (oneshot, runs every 15 min via timer)
- **Timer**: `aurum1-d2-shadow.timer` (enabled)
- Analyzes all 25,379 M15 candles (June 2025 - June 2026)
- Simulates: Donchian 20 breakout + fixed 1R exit + filter(high vol, london session)

### 2. D2 Session Performance
| Session | Trades | Wins | Losses | R |
|---------|--------|------|--------|---|
| Asia | 242 | 135 | 107 | +24.36R |
| London-NY Overlap | 182 | 113 | 69 | **+42.07R** |
| Rollover | 63 | 34 | 29 | +4.78R |
| New York | 55 | 30 | 25 | +4.66R |

### 3. D2 Exit Breakdown
| Exit Reason | Count | % |
|-------------|-------|---|
| Take Profit (1R) | 312 | 57.6% |
| Stop Loss (-1R) | 225 | 41.5% |
| Stop Loss Gap | 5 | 0.9% |

## Recommended Next Steps

### Immediate (24h)
1. ✅ D2 shadow deployed and collecting data
2. 🔲 Start monitoring D2 weekly reports
3. 🔲 Check D2 performance after 1 week of live shadow data

### Short-Term (7 days)
1. 🔲 Restart `aurum1.service` (main orchestrator) if needed — currently stopped
2. 🔲 Compare D1 vs D2 weekly — pick the winner after 1 month
3. 🔲 Add D2 weekly report generation to the backup timer

### Medium-Term (30 days)
1. 🔲 Train ML models and deploy full ensemble mode
2. 🔲 **Deploy D2 as paper trading strategy** if it maintains PF > 1.20
3. 🔲 Enable SELL signals (D2 only generates BUY currently)
4. 🔲 Add telemetry dashboard showing all 3 variants side-by-side

### The D2 Systemd Files

```bash
# Service file: /etc/systemd/system/aurum1-d2-shadow.service
[Unit]
Description=AURUM-1 D2 shadow

[Service]
Type=oneshot
User=aurum1
WorkingDirectory=/opt/aurum1
ExecStart=/opt/aurum1/.venv/bin/python scripts/forward_shadow_donchian_d2.py --json

# Timer file: /etc/systemd/system/aurum1-d2-shadow.timer
[Unit]
Description=Run D2 shadow every 15 minutes

[Timer]
OnCalendar=*:0/15
Persistent=true

[Install]
WantedBy=timers.target
```

## Quick Commands

```bash
# Check D2 latest run
journalctl -u aurum1-d2-shadow.service -n 20 --no-pager | tail -10

# Run D2 manually
sudo -u aurum1 /opt/aurum1/.venv/bin/python \
  /opt/aurum1/scripts/forward_shadow_donchian_d2.py

# Check all shadow services
systemctl status aurum1-forward-shadow.service --no-pager
systemctl status aurum1-d1-shadow.timer --no-pager
systemctl status aurum1-d2-shadow.timer --no-pager

# View shadow status
sudo -u aurum1 /opt/aurum1/.venv/bin/python \
  /opt/aurum1/scripts/forward_shadow_donchian.py status

# View weekly report
sudo -u aurum1 /opt/aurum1/.venv/bin/python \
  /opt/aurum1/scripts/forward_shadow_donchian.py weekly-report
```
