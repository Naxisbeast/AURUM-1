# AURUM-1 Deployment Guide

## Server Architecture

Production target: `/opt/aurum1` on Ubuntu 24.04 with Python 3.12.

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

### Forward Shadow Service (Raw Donchian 2R)

```bash
# Edit the service file to set the start date
sudo vi /etc/systemd/system/aurum1-forward-shadow.service
# Replace <FORWARD_SHADOW_START_DATE_UTC> with e.g. 2026-05-01T00:00:00Z

sudo systemctl daemon-reload
sudo systemctl enable --now aurum1-forward-shadow.service

# Verify
systemctl status aurum1-forward-shadow.service
```

### D1 Shadow Journal Timer

```bash
sudo systemctl enable --now aurum1-d1-shadow.timer
systemctl status aurum1-d1-shadow.timer
```

### D2 Shadow Timer

```bash
sudo systemctl enable --now aurum1-d2-shadow.timer
systemctl status aurum1-d2-shadow.timer
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

| Service | Type | Purpose |
|---------|------|---------|
| `aurum1-forward-shadow.service` | Continuous | Raw Donchian 2R shadow testing |
| `aurum1-d1-shadow.timer` | Every 15 min | D1 filtered shadow journal |
| `aurum1-d2-shadow.timer` | Every 15 min | D2 simulation comparison |
| `aurum1-forward-shadow-weekly-report.timer` | Weekly | Generate performance reports |
| `aurum1-forward-shadow-backup.timer` | Daily | SQLite database backup |

---

## Common Operations

### Check Service Status

```bash
systemctl status aurum1-forward-shadow.service --no-pager
systemctl status aurum1-d1-shadow.timer --no-pager
systemctl status aurum1-d2-shadow.timer --no-pager
```

### View Logs

```bash
# Shadow service
journalctl -u aurum1-forward-shadow.service -n 50 --no-pager

# D1 shadow
tail -f /opt/aurum1/logs/aurum1/phase_s5_d1_shadow.log

# D2 shadow
journalctl -u aurum1-d2-shadow.service -n 20 --no-pager

# Full log files
tail -f /opt/aurum1/logs/forward_shadow_donchian.log
```

### Generate Reports

```bash
# Current status
sudo -u aurum1 /opt/aurum1/.venv/bin/python \
  /opt/aurum1/scripts/forward_shadow_donchian.py status

# Weekly report
sudo -u aurum1 /opt/aurum1/.venv/bin/python \
  /opt/aurum1/scripts/forward_shadow_donchian.py weekly-report

# D2 simulation
sudo -u aurum1 /opt/aurum1/.venv/bin/python \
  /opt/aurum1/scripts/forward_shadow_donchian_d2.py
```

### Safe Update

```bash
cd /opt/aurum1
sudo -u aurum1 git pull --ff-only
sudo -u aurum1 .venv/bin/pip install -r requirements.txt
# Restart services if needed
sudo systemctl restart aurum1-forward-shadow.service
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
- [ ] Risk manager has kill switches configured
- [ ] No `.env` files in git-tracked paths

---

## Monitoring

All services log to:
- `journalctl` (systemd)
- `/opt/aurum1/logs/` (file logs)

Health endpoint (if main orchestrator is running):
```
http://127.0.0.1:8080/health
```

Forward shadow status:
```bash
sudo -u aurum1 /opt/aurum1/.venv/bin/python \
  /opt/aurum1/scripts/forward_shadow_donchian.py status
```

---

## Troubleshooting

**Service won't start**:
- Check `journalctl -u <service-name> -n 50 --no-pager`
- Verify Python 3.12: `.venv/bin/python --version`
- Verify `.env` exists and has OANDA_API_KEY

**No new candles**:
- Check market cache: `sqlite3 aurum1/data/forward_shadow_market_cache.sqlite3 "SELECT MAX(timestamp) FROM ohlcv_M15"`
- Verify OANDA API key is valid
- Check network connectivity

**Database growing too large**:
- The shadow database grows ~3MB/day. 28 daily backups are retained.
- Clean old backups manually if needed: `rm backups/forward_shadow/old_backup.sqlite3`
