# AURUM-1 Forward Shadow Dashboard

This dashboard is read-only. It observes the forward-shadow ledger, weekly JSON
reports, and market cache. It does not start trading, stop trading, place
orders, alter settings, or change the forward-shadow runner.

Current operating status must remain:

- research-only
- forward shadow only
- raw Donchian fixed 2R
- OANDA orders disabled
- live trading disabled

## Data Sources

- `reports/forward_shadow/donchian_shadow.sqlite3`
- `reports/forward_shadow/*weekly*.json`
- `aurum1/data/forward_shadow_market_cache.sqlite3`
- `logs/forward_shadow_donchian.log` for operator reference

The SQLite connections use read-only URI mode where available. Missing files
are shown as unavailable in the dashboard rather than initialized.

## Run Locally On The Server

From the repository root on the server:

```bash
streamlit run dashboard/forward_shadow_dashboard.py --server.address 127.0.0.1 --server.port 8501
```

Binding to `127.0.0.1` keeps the dashboard local to the server.

## Access Through An SSH Tunnel

From the laptop:

```powershell
ssh -i $env:USERPROFILE\.ssh\aurum1_key -L 8501:127.0.0.1:8501 root@178.105.245.66
```

Then open:

```text
http://localhost:8501
```

Do not expose this dashboard publicly. Keep it behind SSH tunneling or an
equivalent private access path only.

## Safety Notes

- The dashboard has no trade controls.
- The dashboard does not modify `settings.yaml`, `.env`, service files, market
  cache, the SQLite ledger, strategy modules, risk modules, or execution
  modules.
- The dashboard does not enable broker paper trading or live trading.
- The dashboard does not import or instantiate broker/order-placement code.
