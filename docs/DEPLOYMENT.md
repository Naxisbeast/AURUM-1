# AURUM-1 Deployment Guide

**Last updated**: 2026-07-21 (Hardening v1.0 complete)

## Server Architecture

Production target: `/opt/aurum1` on Ubuntu 24.04 with Python 3.12.

**Data flow**: OANDA API → forward-shadow service → local market cache → D4 paper trader reads from cache (no OANDA key needed for paper trader).

**State recovery**: On restart, D4 paper trader restores equity from account snapshots, last_processed_ts from settings, missed signals from missed_signals table, and open positions from open_positions table. Full state survival across restarts.

---

## Initial Setup

### 1. Create System User

```bash
sudo useradd --system --home /opt/aurum1 --shell /usr/sbin/nologin aurum1
sudo mkdir -p /opt/aurum1
sudo chown aurum1:aurum1 /opt/aurum1
```

### 2. Clone Repository

```bash
cd /opt/aurum1
sudo -u aurum1 git clone git@github.com:Naxisbeast/AURUM-1.git .
```

### 3. Set Up Python Environment

```bash
sudo -u aurum1 python3.12 -m venv .venv
sudo -u aurum1 .venv/bin/pip install --upgrade pip
sudo -u aurum1 .venv/bin/pip install -r requirements.txt
```

### 4. Configure Secrets

```bash
sudo -u aurum1 cp .env.example .env
# Edit .env with real API keys:
#   OANDA_API_KEY, OANDA_ACCOUNT_ID, FRED_API_KEY, etc.
sudo -u aurum1 vi .env
```

### 5. Verify Environment

```bash
sudo -u aurum1 .venv/bin/python --version  # Must be Python 3.12
```

---

## Deploying Services

### Forward Shadow Service (Data Cache + Raw Donchian 2R)

The forward shadow is the data backbone. It fetches M15 candles from OANDA and maintains the shared market cache that all other services read from.

```bash
sudo systemctl enable --now aurum1-forward-shadow.service

# Verify
systemctl status aurum1-forward-shadow.service
```

### D4 Paper Trader 🏆

The autonomous paper trading service. Reads from the forward shadow's market cache, executes Donchian 2R BUY+SELL trades through PaperBroker, and persists to `paper_trading.sqlite3`.

```bash
# Deploy the systemd service
sudo cp deploy/aurum1-d4-paper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aurum1-d4-paper.service

# Verify
systemctl status aurum1-d4-paper.service

# View live logs
journalctl -u aurum1-d4-paper.service -f

# View recent trades
journalctl -u aurum1-d4-paper.service -n 50 --no-pager | grep -E 'ENTRY|EXIT|EQ='
```

### Shadow Timer Services (D1-D6 Comparison)

```bash
# Deploy all shadow variant timers
for t in aurum1-d1-shadow aurum1-d2-shadow aurum1-d3-shadow aurum1-d4-shadow aurum1-d6-shadow; do
  sudo systemctl enable --now $t.timer
done

# Verify all
for t in aurum1-d1-shadow aurum1-d2-shadow aurum1-d3-shadow aurum1-d4-shadow aurum1-d6-shadow; do
  echo "$t: $(systemctl is-active $t.timer)"
done
```

### ML Retrain Timer

```bash
sudo systemctl enable --now aurum1-ml-retrain.timer
```

### Weekly Report Timer

```bash
sudo systemctl enable --now aurum1-forward-shadow-weekly-report.timer
```

### Daily Backup Timer

```bash
sudo systemctl enable --now aurum1-forward-shadow-backup.timer
```

---

## Service Reference

| Service | Type | Strategy | Purpose |
|---------|------|----------|---------|
| `aurum1-forward-shadow.service` | Continuous | Raw Donchian 2R BUY-only | Market data cache + locked 3-month study |
| `aurum1-d4-paper.service` | Continuous | D4 Donchian 2R BUY+SELL | **Autonomous paper trading** 🏆 |
| `aurum1-d1-shadow.timer` | Every 15 min | Donchian 1R + vol/session | D1 journal |
| `aurum1-d2-shadow.timer` | Every 15 min | Donchian 1R + filter (BUY) | D2 comparison |
| `aurum1-d3-shadow.timer` | Every 15 min | Donchian 1R + filter (BUY+SELL) | D3 SELL test |
| `aurum1-d4-shadow.timer` | Every 15 min | Donchian 2R BUY+SELL no filters | Best variant comparison |
| `aurum1-d6-shadow.timer` | Every 15 min | Donchian 2R + ML ensemble | ML variant comparison |
| `aurum1-ml-retrain.timer` | Weekly (Sat) | Retrain RegimeClassifier + DirectionPredictor | Continuous learning |
| `aurum1-forward-shadow-weekly-report.timer` | Weekly | Generate performance reports | Monitoring |
| `aurum1-forward-shadow-backup.timer` | Daily | SQLite database backup | Data safety |

---

## Common Operations

### Check Service Status

```bash
systemctl status aurum1-d4-paper.service --no-pager
systemctl status aurum1-forward-shadow.service --no-pager

# All timers
for t in aurum1-d1-shadow aurum1-d2-shadow aurum1-d3-shadow aurum1-d4-shadow aurum1-d6-shadow aurum1-ml-retrain; do
  echo "$t: $(systemctl is-active $t.timer)"
done
```

### View Logs

```bash
# D4 paper trader — last 30 lines
journalctl -u aurum1-d4-paper.service -n 30 --no-pager

# D4 paper trader — live tail
journalctl -u aurum1-d4-paper.service -f

# D4 — show only trades and errors
journalctl -u aurum1-d4-paper.service --since today --no-pager | grep -E 'ENTRY|EXIT|EQ=|DB persist|error'

# Forward shadow — cache status
journalctl -u aurum1-forward-shadow.service -n 10 --no-pager

# Timer shadow logs
journalctl -u aurum1-d1-shadow.service -n 20 --no-pager
journalctl -u aurum1-d2-shadow.service -n 20 --no-pager
```

### Paper Trader Database

The database at `aurum1/data/paper_trading.sqlite3` contains these tables:

| Table | Purpose |
|-------|---------|
| `trades` | Completed trades with entry/exit prices, R-multiple, PnL |
| `account_snapshots` | Equity/balance history every ~15 min |
| `settings` | Key-value store (e.g., `last_processed_ts`) |
| `missed_signals` | Rejected signals with timestamp, direction, price, reason |
| `open_positions` | Currently open positions (persisted for restart recovery) |

```bash
# Trade count + PnL summary
sqlite3 /opt/aurum1/aurum1/data/paper_trading.sqlite3 \
  "SELECT COUNT(*) as trades, COALESCE(SUM(net_pnl),0) as total_pnl, \
   COALESCE(SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END),0) as wins, \
   COALESCE(SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END),0) as losses FROM trades;"

# Last 10 trades
sqlite3 /opt/aurum1/aurum1/data/paper_trading.sqlite3 \
  "SELECT entry_time, exit_time, direction, r_multiple, net_pnl, exit_reason FROM trades ORDER BY exit_time DESC LIMIT 10;"

# Account snapshots
sqlite3 /opt/aurum1/aurum1/data/paper_trading.sqlite3 \
  "SELECT * FROM account_snapshots ORDER BY timestamp DESC LIMIT 5;"

# Missed signals
sqlite3 /opt/aurum1/aurum1/data/paper_trading.sqlite3 \
  "SELECT timestamp, direction, price, reason FROM missed_signals ORDER BY id DESC LIMIT 5;"

# Open positions
sqlite3 /opt/aurum1/aurum1/data/paper_trading.sqlite3 \
  "SELECT direction, entry_price, stop_loss, take_profit FROM open_positions;"

# Health file (live metrics)
cat /opt/aurum1/run/d4_paper_trader_health.json | python3 -m json.tool
```

### Market Cache Status

```bash
# Freshness + candle count
sqlite3 /opt/aurum1/aurum1/data/forward_shadow_market_cache.sqlite3 \
  "SELECT MAX(timestamp), COUNT(*) FROM ohlcv_M15;"
```

### Generate Reports

```bash
# Forward shadow status
sudo -u aurum1 /opt/aurum1/.venv/bin/python \
  /opt/aurum1/scripts/forward_shadow_donchian.py status

# Weekly report
sudo -u aurum1 /opt/aurum1/.venv/bin/python \
  /opt/aurum1/scripts/forward_shadow_donchian.py weekly-report

# D4 paper trader — run once (manual)
sudo -u aurum1 /opt/aurum1/.venv/bin/python \
  /opt/aurum1/scripts/d4_paper_trader.py --run-once
```

### Safe Update

```bash
cd /opt/aurum1
sudo -u aurum1 git pull --ff-only
sudo -u aurum1 .venv/bin/pip install -r requirements.txt
# Restart continuous services
sudo systemctl restart aurum1-forward-shadow.service
sudo systemctl restart aurum1-d4-paper.service
```

### Restart Paper Trader

```bash
sudo systemctl restart aurum1-d4-paper.service
```

### Backup and Restore

```bash
# Manual backup
sqlite3 /opt/aurum1/reports/forward_shadow/donchian_shadow.sqlite3 \
  ".backup '/opt/aurum1/backups/forward_shadow/manual_$(date -u +%Y%m%d_%H%M%S).sqlite3'"

# List backups
ls -lh /opt/aurum1/backups/forward_shadow/

# Restore (stop service first)
sudo systemctl stop aurum1-forward-shadow.service
cp /opt/aurum1/backups/forward_shadow/<backup-file>.sqlite3 \
   /opt/aurum1/reports/forward_shadow/donchian_shadow.sqlite3
sudo chown aurum1:aurum1 /opt/aurum1/reports/forward_shadow/donchian_shadow.sqlite3
sudo systemctl start aurum1-forward-shadow.service
```

---

## Safety Checklist

- [ ] `ALLOW_OANDA_ORDERS=false` in `.env`
- [ ] `ALLOW_LIVE_TRADING=false` in `.env`
- [ ] `OANDA_ENV=practice` in `.env`
- [ ] Forward shadow asserts these at startup and fails closed
- [ ] D4 paper trader uses local cache only — no OANDA API key needed
- [ ] Risk manager has kill switches configured
- [ ] No `.env` files in git-tracked paths

---

## Monitoring

All services log to:
- `journalctl` (systemd)
- `/opt/aurum1/logs/` (file logs)

Key monitoring commands:

```bash
# Quick health check
systemctl is-active aurum1-d4-paper.service aurum1-forward-shadow.service

# Trade activity since midnight
journalctl -u aurum1-d4-paper.service --since today --no-pager | grep -E 'ENTRY|EXIT'

# Market data freshness
sqlite3 /opt/aurum1/aurum1/data/forward_shadow_market_cache.sqlite3 \
  "SELECT MAX(timestamp) FROM ohlcv_M15;"

# Paper trading DB summary
sqlite3 /opt/aurum1/aurum1/data/paper_trading.sqlite3 \
  "SELECT COUNT(*), COALESCE(SUM(net_pnl),0) FROM trades;"

# Observability health file (all live metrics)
cat /opt/aurum1/run/d4_paper_trader_health.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps({k:v for k,v in d.items() if k != 'raw_response'}, indent=2))"

# Observability report from journal
journalctl -u aurum1-d4-paper.service --since today --no-pager | grep 'OBSERVABILITY REPORT'
```

---

## Troubleshooting

**Service won't start**:
- Check `journalctl -u <service-name> -n 50 --no-pager`
- Verify Python 3.12: `.venv/bin/python --version`
- Verify `.env` exists (for forward-shadow) or market cache exists (for D4)

**No new candles** (forward-shadow):
- Check market cache: `sqlite3 .../forward_shadow_market_cache.sqlite3 "SELECT MAX(timestamp) FROM ohlcv_M15"`
- Verify OANDA API key is valid
- Check network connectivity

**D4 paper trader not trading**:
- Check market cache is fresh (above)
- Check D4 logs for entry conditions: `journalctl -u aurum1-d4-paper.service -n 50 --no-pager | grep -E 'ENTRY|EQ='`
- Verify `paper_trading.sqlite3` is writable by aurum1 user

**DB persist errors**:
- If you see `DB persist error` in D4 logs, check the error message:
  - `'time'` — trade dict key mismatch. Fix: `trade["time"]` → `trade.get("open_time", "")` in `_persist_trade()`
  - Other — check file permissions and disk space

**Database growing too large**:
- The shadow database grows ~3MB/day. 28 daily backups are retained.
- Clean old backups manually if needed: `rm backups/forward_shadow/old_backup.sqlite3`
