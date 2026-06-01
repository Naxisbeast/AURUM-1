# AURUM-1 Forward Shadow Deployment Checklist

Generated: 2026-06-01

Final status: **research-only, forward shadow only, no broker paper orders, no live trading**.

Pytest result: **PASS - 217 passed, 1 skipped** using Python 3.12.

## Locked Strategy

- [x] PASS - Strategy remains `raw_donchian_fixed_2r`.
- [x] PASS - Direction remains BUY-only.
- [x] PASS - Donchian lookback remains 20.
- [x] PASS - Exit remains fixed 2R.
- [x] PASS - Forward-shadow risk remains 0.25% per trade.
- [x] PASS - `paper_trade=true` remains required.
- [x] PASS - `allow_oanda_orders=false` remains required.
- [x] PASS - ML remains disabled for this runner.
- [x] PASS - SELL remains disabled for this runner.
- [x] PASS - No broker-paper or live order path is enabled.

## Hardening Items

- [x] PASS - Python runtime safety added: forward shadow exits before heavy imports unless run with Python 3.12.
- [x] PASS - `.venv` safety added: service mode requires a virtualenv unless explicitly overridden for controlled diagnostics.
- [x] PASS - Stale-data unhealthy status added with latest-candle age and threshold reporting.
- [x] PASS - Heartbeat-gap downtime calculation added to weekly health reporting.
- [x] PASS - Automated weekly report systemd service/timer templates added.
- [x] PASS - Weekly report health section added.
- [x] PASS - Dedicated market cache configured: `aurum1/data/forward_shadow_market_cache.sqlite3`.
- [x] PASS - Automated parity test added against the Donchian historical runner.
- [x] PASS - Safety tests added for unsafe environment and config states.
- [x] PASS - Static no-order-path test added for the forward-shadow runner.
- [x] PASS - Audit trail hardened with append-only `shadow_audit_snapshots` around idempotent current-state writes.
- [x] PASS - Daily SQLite backup script and systemd service/timer templates added.
- [x] PASS - Deployment docs updated for deploy, status, logs, reports, backup, and restore.
- [x] PASS - Hardcoded systemd start date removed.
- [x] PASS - Service template now uses `<FORWARD_SHADOW_START_DATE_UTC>` and instructs the operator to set it at deployment.

## Verification Commands

- [x] PASS - Full test suite:
  - Command: `python -m pytest -q --basetemp .pytest_tmp -p no:cacheprovider`
  - Result: `217 passed, 1 skipped in 31.43s`
- [x] PASS - Compile check:
  - Command: `python -m py_compile scripts/forward_shadow_donchian.py tests/test_forward_shadow_donchian.py`
- [x] PASS - Default ledger init:
  - Command: `python scripts/forward_shadow_donchian.py init`
  - Output: initialized `reports/forward_shadow/donchian_shadow.sqlite3`
- [x] PASS - Default ledger run-once:
  - Command: `python scripts/forward_shadow_donchian.py run-once --start-date 2026-05-01T00:00:00Z`
  - Output: 97 signals, 32 closed trades, 65 skipped signals, 1999 equity points, no OANDA orders sent.
- [x] PASS - Default ledger status:
  - Command: `python scripts/forward_shadow_donchian.py status`
  - Output: status `unhealthy` because the existing ledger contains recent prior error events; stale-data check itself reported `ok`.
- [x] PASS - Default ledger weekly report:
  - Command: `python scripts/forward_shadow_donchian.py weekly-report`
  - Output: report written to `reports/forward_shadow/donchian_shadow_weekly_20260601_192010.json`; health section reported `unhealthy` due prior events.
- [x] PASS - Clean verification DB init:
  - Command: `python scripts/forward_shadow_donchian.py --shadow-db reports/forward_shadow/deployment_check.sqlite3 init`
- [x] PASS - Clean verification DB run-once:
  - Command: `python scripts/forward_shadow_donchian.py --shadow-db reports/forward_shadow/deployment_check.sqlite3 run-once --start-date 2026-05-01T00:00:00Z`
  - Output: 97 signals, 32 closed trades, 65 skipped signals, 1999 equity points, no OANDA orders sent.

## Local Verification Notes

- [x] PASS - Existing runtime DB was not deleted or cleaned.
- [x] PASS - Dedicated forward-shadow market cache was seeded locally from the existing OANDA market cache for verification.
- [x] PASS - No OANDA broker-paper or live orders were sent.
- [x] PASS - Runtime environment warning appeared locally because Python 3.12 was invoked outside `.venv`; this is expected for local diagnostics. The systemd service uses `/opt/aurum1/.venv/bin/python`.

## Server Deployment Must-Do Items

- [ ] FAIL - Not yet deployed on the server.
- [ ] FAIL - Operator must replace `<FORWARD_SHADOW_START_DATE_UTC>` in the installed systemd service before starting it.
- [ ] FAIL - Operator must verify `/opt/aurum1/.venv/bin/python --version` is Python 3.12.
- [ ] FAIL - Operator must install and enable `aurum1-forward-shadow.service`.
- [ ] FAIL - Operator must install and enable `aurum1-forward-shadow-weekly-report.timer`.
- [ ] FAIL - Operator must install and enable `aurum1-forward-shadow-backup.timer`.
- [ ] FAIL - Operator must confirm `OANDA_ENV=practice`, `ALLOW_OANDA_ORDERS=false`, and `ALLOW_LIVE_TRADING=false` in `/opt/aurum1/.env`.
- [ ] FAIL - Operator must run server-side `pytest` before starting the service.
- [ ] FAIL - Operator must run server-side `init`, `run-once`, `status`, and `weekly-report`.

## Deployment Readiness Summary

Code readiness for forward-shadow deployment: **PASS with operator steps remaining**.

Operational deployment readiness: **FAIL until the server-side checklist is completed**.

Broker paper readiness: **FAILED**.

Live readiness: **FAILED**.

Final verdict: **research-only forward shadow candidate; no broker paper orders and no live trading**.
