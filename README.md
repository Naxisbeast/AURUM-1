# AURUM-1

AURUM-1 is a phased XAU/USD trading system with live-capable data ingestion,
feature engineering, model validation, signal generation, risk management,
execution, backtesting, and monitoring.

## Running AURUM-1

Paper trading + dashboard:

```bash
python scripts/run_dashboard.py
```

Backtest:

```bash
python scripts/run_backtest.py
```

Validation:

```bash
python scripts/validate_phase3.py
```

## Deployment Notes

The private GitHub repository is the deployment source of truth. The server
should use a read-only GitHub deploy key and pull from:

```bash
git@github.com:Naxisbeast/AURUM-1.git
```

Runtime secrets belong only in `/opt/aurum1/.env`. Do not commit `.env`.
Use `.env.example` as the placeholder template.

Safe update flow:

```bash
cd /opt/aurum1
git pull --ff-only
source .venv/bin/activate
pip install -r requirements.txt
python scripts/validate_phase3.py
python scripts/run_backtest.py
```

Backtests are isolated from the runtime execution database by default. They may
read/update the market-data cache DB, but trade/order/equity outputs are written
to a temporary backtest execution DB.

Dashboard binding defaults to `127.0.0.1`. Expose it publicly only with an
explicit reverse proxy/TLS/authentication plan.

Runtime DB archive, non-destructive:

```bash
python scripts/archive_runtime_db.py
```

No cleanup command should be run against `aurum1/data/aurum1.sqlite3` without an
explicit backup and approval.
