# AURUM-1 Forward Shadow Plan

This plan locks the primary research candidate to:

- Strategy: raw Donchian fixed 2R
- Direction: BUY only
- Donchian lookback: 20
- Exit: fixed 2R take-profit with original stop
- Risk per trade: 0.25%
- Broker/order mode: no broker orders
- Minimum duration: 3 months
- Status: research-only

Do not change parameters, switch variants, enable ML, enable SELL, add filters,
or enable OANDA orders during the shadow window.

## Safety

`scripts/forward_shadow_donchian.py` fails closed if either environment variable
is enabled:

```bash
ALLOW_OANDA_ORDERS=true
ALLOW_LIVE_TRADING=true
```

The script reads M15 candles from the market cache and writes only to the
dedicated forward-shadow ledger:

```text
aurum1/data/forward_shadow_market_cache.sqlite3
reports/forward_shadow/donchian_shadow.sqlite3
```

It does not instantiate `OandaBroker`, submit OANDA orders, or mutate the runtime
paper/live SQLite database.

## Start

Initialize the shadow ledger:

```bash
/opt/aurum1/.venv/bin/python scripts/forward_shadow_donchian.py init
```

Run the update after new closed M15 candles have been written to the cache:

```bash
/opt/aurum1/.venv/bin/python scripts/forward_shadow_donchian.py run-once --start-date <FORWARD_SHADOW_START_DATE_UTC>
```

The start date should remain frozen for the full 3-month shadow window.
Use Python 3.12 from the project `.venv`; the runner exits before heavy imports
if invoked with any other Python minor version.

## Cloud Service

The single long-running command is:

```bash
/opt/aurum1/.venv/bin/python scripts/forward_shadow_donchian.py service --start-date <FORWARD_SHADOW_START_DATE_UTC>
```

Required environment:

```bash
OANDA_API_KEY=...
OANDA_ENV=practice
ALLOW_OANDA_ORDERS=false
ALLOW_LIVE_TRADING=false
```

`OANDA_ACCOUNT_ID` may exist in `.env`, but the forward-shadow service does not
create an OANDA broker or submit broker-paper/live orders.

Example systemd template:

```text
deploy/forward-shadow.service.template
```

Install outline:

```bash
sudo cp deploy/forward-shadow.service.template /etc/systemd/system/aurum1-forward-shadow.service
sudo editor /etc/systemd/system/aurum1-forward-shadow.service  # replace <FORWARD_SHADOW_START_DATE_UTC>
sudo cp deploy/forward-shadow-weekly-report.service.template /etc/systemd/system/aurum1-forward-shadow-weekly-report.service
sudo cp deploy/forward-shadow-weekly-report.timer.template /etc/systemd/system/aurum1-forward-shadow-weekly-report.timer
sudo cp deploy/forward-shadow-backup.service.template /etc/systemd/system/aurum1-forward-shadow-backup.service
sudo cp deploy/forward-shadow-backup.timer.template /etc/systemd/system/aurum1-forward-shadow-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable aurum1-forward-shadow
sudo systemctl enable --now aurum1-forward-shadow-weekly-report.timer
sudo systemctl enable --now aurum1-forward-shadow-backup.timer
sudo systemctl start aurum1-forward-shadow
```

Check status:

```bash
systemctl status aurum1-forward-shadow
python scripts/forward_shadow_donchian.py status
```

View logs:

```bash
journalctl -u aurum1-forward-shadow -f
tail -f logs/forward_shadow_donchian.log
```

Safely stop/restart:

```bash
sudo systemctl stop aurum1-forward-shadow
sudo systemctl restart aurum1-forward-shadow
```

The service handles `SIGINT`/`SIGTERM`, records shutdown events, and resumes from
the persistent SQLite ledger on restart. Duplicate signals/trades/candles are
protected by primary keys and `INSERT OR REPLACE` writes.

## Storage And Backups

Forward-shadow state lives here by default:

```text
reports/forward_shadow/donchian_shadow.sqlite3
```

Non-destructive backup:

```bash
mkdir -p backups
sqlite3 reports/forward_shadow/donchian_shadow.sqlite3 ".backup 'backups/donchian_shadow_$(date -u +%Y%m%d_%H%M%S).sqlite3'"
```

Automated daily backup template:

```text
deploy/forward-shadow-backup.timer.template
```

Manual backup command:

```bash
scripts/backup_forward_shadow_db.sh /opt/aurum1
```

Restore only while the service is stopped:

```bash
sudo systemctl stop aurum1-forward-shadow
cp backups/forward_shadow/<backup-file>.sqlite3 reports/forward_shadow/donchian_shadow.sqlite3
sudo chown aurum1:aurum1 reports/forward_shadow/donchian_shadow.sqlite3
sudo systemctl start aurum1-forward-shadow
```

Logs rotate through Python's rotating file handler and the repo logrotate
template:

```text
deploy/logrotate/aurum1
```

## Weekly Report

Generate the latest weekly report:

```bash
python scripts/forward_shadow_donchian.py weekly-report
```

Automated weekly report template:

```text
deploy/forward-shadow-weekly-report.timer.template
```

The weekly JSON report includes:

- gross P&L
- net P&L
- profit factor
- Sharpe estimate
- max drawdown
- trade count
- win rate
- average R
- median R
- best trade
- worst trade
- skipped signals
- execution/logging issues
- uptime/downtime
- data gaps
- API failures
- comparison to historical expectations
- health section with stale-data status, runtime environment, audit snapshot count,
  and recent error count

## Failure Criteria

Pause the shadow test if any of these occur:

- execution or logging bugs
- trades differ from the intended raw Donchian fixed-2R rules
- drawdown exceeds 10-15%
- profit factor collapses below 1.0
- trade count wildly differs from historical rate
- repeated data/feed issues

## Success Criteria After 3 Months

The strategy remains research-only unless the full 3-month shadow test shows:

- net P&L positive or near-flat with acceptable drawdown
- profit factor at least 1.10
- Sharpe not catastrophically worse than backtest
- max drawdown at or below 10%
- no execution realism issues
- trade count broadly consistent with historical expectation
- no manual intervention needed
