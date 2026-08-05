# Containerization Decision — AURUM-1

**Date**: 2026-08-05
**Decision**: Do NOT containerize AURUM at this time
**Issue**: #5 (Containerization Decision)

---

## Context

The professor's feedback recommended "container hardening" as part of the
security review. AURUM currently runs as systemd services directly on a
Hetzner VM (Ubuntu 24.04), not in containers.

This document records the decision to keep the current architecture, and the
reasoning behind it — so the decision is explicit and re-evaluable, not
silently avoided.

---

## What Containerization Would Provide

1. **Isolation**: Each service (trader, shadow, dashboard, watchdog) in its own container
2. **Reproducibility**: Identical environment across machines via Docker images
3. **Hardening**: Container-specific security defaults (read-only FS, non-root, resource limits)
4. **Portability**: Could run on any host with Docker

---

## Why AURUM Is NOT Containerizing Now

### 1. Single-host architecture doesn't benefit
Containerization's biggest value is managing multiple services across machines
(orchestration). AURUM runs 5 services on ONE Hetzner VM. systemd already
manages them fine — restart-on-failure, logging, dependencies. Adding Docker
adds an orchestration layer for something systemd already handles.

### 2. SQLite is file-based, not network-attached
All AURUM state lives in SQLite files:
```
aurum1/data/paper_trading.sqlite3
aurum1/data/forward_shadow_market_cache.sqlite3
reports/forward_shadow/donchian_shadow.sqlite3
```
Containers make file access harder (volume mounts, permissions, backups).
The current design just reads/writes local files — simpler and safer.

### 3. Added operational complexity with no benefit at this scale
Docker would add:
- Dockerfile(s) and image builds
- Volume management for 3+ SQLite DBs
- Container restart policies
- A build/deploy pipeline

For a solo project with 5 services on one box, this is complexity that
doesn't earn its keep. This directly contradicts the project's core
principle: **complexity must justify its existence.**

### 4. The system already survived a full server power-cycle
The Hetzner VM was powered off and on earlier this month (the tunnel/SSH
incident). All 5 services came back, all trades and state survived in SQLite,
and the system resumed exactly where it left off. This demonstrated the
current architecture is already resilient enough.

### 5. Container hardening is only meaningful with a threat model
Hardening matters when there's an external threat. AURUM is paper trading —
no live capital, no real keys on the box (credentials are env vars), and the
only exposure is the read-only dashboard. There's no realistic attack surface
that containers would materially reduce.

---

## What Would Change This Decision

Containerization becomes worthwhile if any of these happen:

| Trigger | Why |
|---------|-----|
| Live capital deployment | Real money → stronger isolation/hardening justified |
| Multi-server or multi-region deployment | Orchestration becomes necessary |
| Another developer joins | Reproducible dev environment becomes valuable |
| A concrete container-specific security requirement | e.g. an auditor requires it |

Until then, the current systemd setup is the right tool for the job.

---

## Security Measures Currently In Place (the "hardening" we do have)

- **No live trading**: OANDA orders blocked by env interlocks
- **No credentials in repo**: `.env` gitignored, pre-commit hook blocks leaks
- **Secret scanning**: GitHub-native, enabled
- **Dependency scanning**: Dependabot, enabled
- **SBOM**: GitHub auto-generated, enabled
- **Independent watchdog**: separate process, hardcoded kill thresholds
- **Least-privilege by default**: ALLOW_OANDA_ORDERS=false, OANDA_ENV=practice

These cover the practical security needs without container overhead.

---

## Conclusion

**Not containerizing is a deliberate decision, not an oversight.** The current
systemd + single-VM + SQLite architecture is simpler, already proven resilient,
and has no attack surface that containers would meaningfully reduce at paper
trading scale. This will be revisited at the trigger points above — most
notably if live capital deployment becomes real.
