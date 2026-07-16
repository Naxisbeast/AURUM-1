# Dashboard Rebuild — Phase 0, 1, 2 Completion Report

## Phase 0: Cleanup (committed, isolated)
- Removed `PaperBroker` import and unused parameter from `get_system_status()`
- Updated both callers (`monitor/dashboard.py`, `test_phase8_monitor.py`)
- All 9 monitor tests pass
- Commit: `092c27c`

## Phase 1: D7 Persistence (committed, isolated)
- `forward_shadow_donchian_d7.py` now writes per-trade records and equity curve to `donchian_d7.sqlite3`
- 1,093 trades persisted on first run with full detail
- All original JSON stdout output and computation logic unchanged
- Verified: same aggregate metrics before and after
- Commit: `a247c8c`

## Phase 2: Dashboard Built (uncommitted)
File: `dashboard/aurum_monitor.py`

### Sections Built

1. **Health bar** (top, always visible)
   - Status: HEALTHY / HALTED_DD / HALTED_DAILY / STANDBY / NO_DATA
   - HALTED states display specific reason + "risk framework executing correctly" message
   - Updated timestamp with human-readable freshness
   - Equity, today's PnL, open positions, last trade time, kill switch status

2. **D4 KPI cards**
   - Profit Factor, Win Rate, Current Drawdown, Total Trades, Avg R
   - Each shows "— not yet tracked —" if data doesn't exist

3. **D7 aggregate comparison** (side-by-side)
   - Same KPIs for D7 from its SQLite DB
   - Shows only if D7 DB exists (deployed server)

4. **Current State — Open Position**
   - Full detail if exists, or explicit "No open positions" (not blank)

5. **Equity curve** with drawdown shading and ATH markers
   - Plotly chart, two rows: equity + drawdown

6. **Trade log**
   - Last 50 trades with color-tinted rows (green for win, red for loss)
   - Sortable columns in dataframe

### Empty/Unpopulated Field Decisions

| Field | Current State | Dashboard Display |
|-------|---------------|-------------------|
| `missed_signals` table | Empty (no rows recorded) | **Not displayed** — entire section hidden |
| `open_positions` table | Empty when no position | **"No open positions"** — styled dashed border, not blank |
| Trade count = 0 | No trades yet | **"— not yet tracked —"** on KPI cards |
| Profit Factor | No trades to compute | **"— not yet tracked —"** |
| Win Rate | No trades to compute | **"— not yet tracked —"** |
| Avg R | No trades with r_multiple | **"— not yet tracked —"** |
| Equity curve | No snapshots | **"— not yet tracked —"** caption |
| D7 DB | Doesn't exist | **Entire D7 section hidden** |
| `paper_trading.sqlite3` | Doesn't exist | **"NO DATA"** status, "Paper trading database not found" detail |

### Comparison Card (added per addendum)
- Side-by-side D4 | D7 with days live, trade count, PF, WR, PnL, equity
- D7 shows "early sample" label when days_live < 3
- D7 shows "Trades computed from historical cache — not live yet" on day 0
- Maturity gap is explicit: D4 head start of 14 days is shown as "Day 14 · 24 trades" vs D7 "Day 0 · early sample · 1094 trades"
- D7 dedup fixed (INSERT OR IGNORE) to prevent duplicate trades across timer runs
- No blended equity curves, no hidden data gaps

### Not Built (as specified)
- D7 per-trade detail view: **not built** — needs more live data history
- Decision artifacts / entry checklist: **not built** — depends on Phase 3+ logging
- Audit log of state changes: **not built** — depends on Phase 4 logging
- Promotion gate tracking: **not built** — data doesn't exist
- Monthly heatmap: **not built** — data doesn't exist yet

### Deployment
- Copy `dashboard/aurum_monitor.py` to server
- Run via existing dashboard Streamlit setup or as a standalone service
- Dashboard reads from local `paper_trading.sqlite3` and `donchian_d7.sqlite3` — paths work on both local and server

### Verification
- No `aurum1/` package files were modified outside Phase 0
- `monitor/` and `tests/` modifications in Phase 0 only
- `scripts/shadow/` modification in Phase 1 only
- `dashboard/` is entirely new, no existing files touched
