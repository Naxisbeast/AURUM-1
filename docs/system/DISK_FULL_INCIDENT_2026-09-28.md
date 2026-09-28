# Disk-Full Outage — 2026-09-28

**Symptom**: The live dashboard (`dashboard.auram.software`) stopped showing data —
not even historical data. The D4 paper trader appeared to keep running, but nothing
was updating.

**Root cause**: the server's disk was **100% full** (0 bytes free on a 38 G volume).
A latent bug in `scripts/backup_forward_shadow_db.sh` had no retention: it created a
daily backup of `donchian_shadow.sqlite3` but never deleted old ones. Over ~4 months
(Jun 1 → Sep 27) that accumulated **133 backups = 19 GB**, filling the disk.

## Bug — `backup_forward_shadow_db.sh` had no retention logic

**Where**: `scripts/backup_forward_shadow_db.sh`

**What**: the script did `sqlite3 .backup 'donchian_shadow_${STAMP}.sqlite3'` once a day
(via `aurum1-forward-shadow-backup.timer`) and then exited. There was no prune step.
`docs/DEPLOYMENT.md` documented "28 daily backups are retained" — but that retention
was **never implemented**. The backups grew unbounded until the volume hit 100%.

**Evidence**: on 2026-09-28 the backup dir held 133 `donchian_shadow_*.sqlite3` files
(19 GB), the oldest from Jun 1, the newest Sep 27. `df -h /` reported `100%` with
0 bytes available.

## Impact

With no free space, every service that needs to write failed:

- `aurum1-forward-shadow.service` — crash-looped **3,748+ times** with
  `sqlite3.OperationalError: disk I/O error` on `PRAGMA journal_mode=WAL` (the restart
  counter kept climbing every ~30 s).
- `aurum1-d4-shadow.service`, `aurum1-forward-shadow-backup.service`,
  `aurum1-forward-shadow-weekly-report.service` — all failed (same I/O errors).
- Dashboard — rendered an empty app because SQLite could not operate against the
  paper-trading DBs on a full volume.
- D4 paper trader — process stayed "running" but could not persist new snapshots.

**No data was lost.** All three SQLite DBs passed `PRAGMA integrity_check` = ok after
space was freed: `paper_trading.sqlite3` (201 trades, 8,301 snapshots),
`aurum1.sqlite3`, `forward_shadow_market_cache.sqlite3`.

## Fix

1. **Freed ~21 GB** on the server:
   - Removed 92 of the 133 shadow backups, keeping the newest 28 (`~13 GB`).
   - Cleared `/root/.cache/pip` (`2.9 GB`), vacuumed `/var/log/journal` to 100 M
     (`704 MB` freed), `apt-get clean`.
   - Disk: `100%` → `56%` (17 GB free).
2. **Added retention to `backup_forward_shadow_db.sh`**: after creating a backup, keep
   only the newest 28 (`KEEP="${SHADOW_BACKUP_RETENTION:-28}"`), pruning the rest.
   - Deployed to the server (old script backed up to
     `backups/backup_forward_shadow_db.sh.bak-20260928`), ownership `aurum1:aurum1`.
   - Tested: `aurum1-forward-shadow-backup.service` exits 0 and the dir count stays at 28.

## Verification

- `aurum1-forward-shadow.service` — recovered; heartbeat reports `errors_24h=0` and
  fresh candles flow again.
- `aurum1-d4-paper.service` — trading resumed; new `account_snapshots` rows written.
- `aurum1-dashboard.service` — running, no errors in its journal.
- `aurum1-d4-shadow.service` and `aurum1-forward-shadow-weekly-report.service` —
  re-triggered cleanly (`Result=success`, exit 0).
- Dashboard requires a browser refresh to re-render after the DBs became writable.

## Prevention

- The backup script now self-prunes to 28 backups, so the disk stays bounded
  (~5.5 GB for the backup dir at current growth, vs 19 GB before).
- The docs statement ("28 daily backups are retained") now matches the implementation.
