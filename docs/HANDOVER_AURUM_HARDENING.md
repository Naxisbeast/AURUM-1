# AURUM Hardening v1.0 — Session Handover

> **⚠️ HISTORICAL DOCUMENT** — Hardening v1.0 is complete as of 2026-07-21.
> See `CLAUDE.md` for current system state, `docs/system/TRUTH_MAP.md` for the
> forensic map, and `docs/system/AUDIT_ROADMAP.md` for outstanding audit items.
> This document is preserved for reference but no longer updated.

## Mission

Execute AURUM Hardening v1.0 — a focused sprint to stabilize the system, remove dead code, fix bugs, improve testing, and build an analytics layer. **No new strategies, no new assets, no major optimizations.** The D4 runs untouched at 0.25% risk while we harden the infrastructure around it.

## Current System State

### Live Services (all active as of 2026-07-17)

| Service | Status | Details |
|---------|--------|---------|
| D4 Paper Trader | ✅ Active | BUY @ $4,001.26 open. 27 trades. +$317 net. $10,472 equity |
| Forward Shadow | ✅ Active | Latest candle 20:45 UTC. data pipeline |
| Dashboard | ✅ Active | Port 80 via nginx, Cloudflare Tunnel |
| Cloudflare Tunnel | ✅ Active | https://wear-boot-jennifer-brush.trycloudflare.com |

### Known Issues from Repo Reorganization

- Scripts were reorganized into subdirectories (`scripts/paper_trading/`, `scripts/shadow/`, `scripts/research/`, `scripts/backtesting/`)
- ROOT path resolution in moved scripts was fixed (parents[1] → parents[2])
- Systemd paths were updated to point to new script locations
- PYTHONPATH added to systemd units
- Some import paths within moved scripts still need verification
- Check `scripts/shadow/forward_shadow_donchian.py` line ~48 for `from scripts.donchian_research_runner` — was fixed but verify other shadow scripts

### Key Files

| File | Purpose |
|------|---------|
| `scripts/paper_trading/d4_paper_trader.py` | The D4 autonomous paper trader (what actually runs) |
| `scripts/shadow/forward_shadow_donchian.py` | Forward shadow data pipeline |
| `monitor/dashboard.py` | Streamlit dashboard (439 lines) |
| `monitor/metrics.py` | Dashboard metric computations (277 lines) |
| `scripts/run_live_vs_backtest_comparator.py` | Comparator tool for drift detection |
| `aurum1/execution/broker.py` | PaperBroker + OandaBroker |
| `aurum1/risk/manager.py` | Risk management (Kelly sizing, kill switches) |
| `aurum1/instruments.py` | InstrumentSpec (XAU/USD config) |

## The Truth Map (To Be Completed)

The first deliverable is `docs/system/TRUTH_MAP.md` containing:

1. **System Identity** — what AURUM is, what it runs
2. **Actual Runtime Architecture** — the real data flow, not the folder structure
3. **Repository Reality Check** — every module classified as: Production / Research / Legacy / Archive
4. **Dependency Graph** — who imports who, circular deps, orphaned modules
5. **Testing Reality** — coverage per critical module
6. **Risk Decision Record** — formalize the 0.35% decision as EXP-000

## The Plan

### Phase 0: Truth Map (Day 1)
- Forensic scan → dead code, broken imports, orphaned modules
- Test coverage baseline per module
- Runtime map → what actually runs vs what exists
- No code changes, only understanding

### Phase 1: Stabilization (Days 2-4)
- **Priority 1** — anything that can lose money (risk calc, execution, state recovery, data freshness)
- **Priority 2** — anything that creates false confidence (incorrect metrics, misleading dashboards, bad tests)
- **Priority 3** — cleanliness (dead code, naming, structure, repo organization)
- Delete dead code: `aurum1/orchestrator.py`, old ML modules, unused backtesting files
- Fix remaining broken import paths
- Replace silent `except: pass` with proper error handling
- Add critical path tests (risk: 50%→80%, execution: 60%→80%, D4: 60%→85%)
- Run full test suite — all must pass

### Phase 2: Validation (Day 5)
- Re-run walk-forward, Monte Carlo, TC stress test
- Verify no regression from cleanup
- **Bump to 0.35% risk** with EXP decision record
- Deploy and monitor for 24h confirmation

### Phase 3: Analytics (Weeks 2-3, parallel)
- Trade quality scoring
- Prop firm simulator (FTMO, The5ers, FundingPips)
- System health dashboard
- Experiment framework (EXP-001 template)

### Phase 4: Evidence Collection (Weeks 4-10)
- D4 runs untouched at 0.35%
- 50 trades → risk review (0.50%?)
- 100 trades → strategy review
- Analytics layer matures independently

## Decision Framework

### Four-Question Gate for Any Change
1. Did it improve **performance**?
2. Did it improve **reliability**?
3. Did it improve **explainability**?
4. Did it **reduce uncertainty**?

If none of the above → reject.

### Repository Philosophy
- The repo should tell the truth about what the system is
- Dead code should be archived, not left in place
- Rejected hypotheses should be preserved (in `archive/`), not destroyed
- Everything earns its place in AURUM, including AURUM itself

## Server Access

- **Host**: `178.105.245.66`
- **SSH**: `ssh -i ~/.ssh/aurum1_key root@178.105.245.66`
- **Python venv**: `/opt/aurum1/.venv/bin/python`
- **Working dir**: `/opt/aurum1`
- **User**: `aurum1` (system user)
- **Dashboard**: `https://wear-boot-jennifer-brush.trycloudflare.com`

### Systemd Services
- `aurum1-d4-paper.service` — D4 paper trader
- `aurum1-forward-shadow.service` — forward shadow data pipeline
- `aurum1-dashboard.service` — Streamlit dashboard
- `aurum1-tunnel.service` — Cloudflare tunnel

### Key Commands
```bash
systemctl status aurum1-d4-paper.service
journalctl -u aurum1-d4-paper.service -n 20 --no-pager
sudo -u aurum1 sqlite3 /opt/aurum1/aurum1/data/paper_trading.sqlite3 "SELECT COUNT(*), SUM(net_pnl) FROM trades;"
```

## Files Changed Recently (Post-Reorg Fixes)
- `scripts/paper_trading/d4_paper_trader.py` — ROOT path fixed (parents[1]→parents[2])
- `scripts/shadow/forward_shadow_donchian.py` — ROOT path fixed + import path fixed
- `/etc/systemd/system/aurum1-d4-paper.service` — ExecStart + PYTHONPATH updated
- `/etc/systemd/system/aurum1-forward-shadow.service` — ExecStart + PYTHONPATH updated

## Pending Questions
- What is the target coverage for the test suite? (Suggested: risk 80%, execution 80%, D4 85%)
- Should we archive or delete old ML models? (Suggest archive)
- What is the exact 0.35% decision gate? (Suggested: after stabilization + validation pass)
