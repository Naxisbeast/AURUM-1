# OBSIDIAN Phase 0 Extraction Plan

This audit identifies only generic data-infrastructure ideas from AURUM-1 that
are useful for a separate Project OBSIDIAN starter template. OBSIDIAN is not an
AURUM-1 module, not a trading bot, and not integrated into AURUM-1 runtime,
strategy, forward-shadow, broker, dashboard, deployment, or research flows.

## Files Inspected

- `aurum1/data/ingestion.py`
- `aurum1/features/engineer.py`
- `aurum1/config/settings.yaml`
- `scripts/fetch_oanda_history.py`
- `scripts/audit_market_cache.py`
- `scripts/research_edge_prototypes.py`
- `tests/test_phase1_ingestion.py`
- `tests/test_phase2_features.py`
- `tests/test_phase11_history.py`
- `README.md`
- `.env.example`

## Files Useful For OBSIDIAN Phase 0

- `aurum1/data/ingestion.py`
  - Useful ideas: provider-isolated ingestion, environment variable names
    rather than inline credentials, OANDA candle REST shape, UTC normalization,
    chunked OANDA range fetching, closed-candle filtering, OHLCV schema
    normalization, SQLite persistence, duplicate handling, and sorted loads.
  - Do not copy directly because it is AURUM-1 phase code and also contains
    macro, COT, news, calendar, trade log, and performance log behavior that is
    outside OBSIDIAN Phase 0.
- `scripts/fetch_oanda_history.py`
  - Useful ideas: standalone data-only script, `.env` loader, credential
    presence checks, date-range fetch, market-cache writes, duplicate count, and
    refusal to write to runtime DB.
  - Rewritten for OBSIDIAN without AURUM settings, runtime DB assumptions, or
    AURUM output text.
- `scripts/audit_market_cache.py`
  - Useful ideas: cache inspection report, duplicate timestamp check, gap
    counting, start/end row summary, readiness-style diagnostics.
  - Rewritten for OBSIDIAN to inspect `timestamp_utc`, completeness, stale
    candles, invalid OHLC rows, and table names in
    `ohlcv_<instrument>_<timeframe>` format.
- `aurum1/features/engineer.py`
  - Useful ideas: UTC DatetimeIndex contract, backward `merge_asof` alignment,
    and lookahead checks for higher timeframe features.
  - Only the generic alignment idea is reused; no technical indicators, model
    features, labels, or strategy assumptions are extracted.
- `tests/test_phase1_ingestion.py`
  - Useful test ideas: settings contain environment variable names, SQLite
    schema creation, UTC OHLCV load contract, OANDA candle normalization,
    closed-candle filtering, chunk overlap deduplication, and retry behavior.
- `tests/test_phase2_features.py`
  - Useful test ideas: session flags are binary, no-lookahead assertions, and
    higher-timeframe merge coverage.
- `tests/test_phase11_history.py`
  - Useful test ideas: history script writes only to market cache, rejects
    missing OANDA credentials, deduplicates candles, and rejects non-OANDA
    proxy data.
- `aurum1/config/settings.yaml`
  - Useful ideas: environment variable indirection, default OANDA practice
    environment, XAU_USD first, M15 included, request timeout, and retry config.

## Files Explicitly Excluded

- `scripts/forward_shadow_donchian.py`
  - Excluded because it is forward-shadow runner/service logic and tied to the
    locked Donchian research direction.
- `deploy/*`
  - Excluded because OBSIDIAN Phase 0 must not add services, deployment files,
    timers, or runtime operations.
- `aurum1/signals/*`
  - Excluded because these contain trading signal/state-machine logic.
- `aurum1/risk/*`
  - Excluded because risk management is trading-bot behavior, not data
    infrastructure.
- `aurum1/execution/*`
  - Excluded because PaperBroker and OandaBroker order execution are outside
    OBSIDIAN Phase 0.
- `aurum1/backtesting/*`
  - Excluded except for the broad idea that data alignment must avoid
    lookahead. The engine, metrics, trade math, Monte Carlo, reports, and
    walk-forward code are AURUM-specific.
- `aurum1/models/*`
  - Excluded because OBSIDIAN Phase 0 has no ML.
- `aurum1/orchestrator.py`
  - Excluded because OBSIDIAN Phase 0 is not live, paper, or orchestrated.
- `monitor/*` and `scripts/run_dashboard.py`
  - Excluded because no dashboard is part of Phase 0.
- `reports/*`, runtime SQLite files, logs, `.env`, keys, and generated outputs
  - Excluded because they are not source templates and may contain sensitive or
    AURUM-specific state.
- `aurum1/config/settings.yaml` strategy, risk, execution, backtesting,
  forward-shadow, monitor, and orchestrator sections
  - Excluded because they encode AURUM-specific behavior.

## Reusable Functions And Classes

The following are reusable as concepts, not as direct imports:

- `load_settings(path)`
  - Reuse as a small OBSIDIAN config loader with env-var indirection and no
    credentials in files.
- `initialize_database(db_path)`
  - Reuse as a market-cache initializer, simplified to only OHLCV tables named
    `ohlcv_<instrument>_<timeframe>`.
- `load_ohlcv(timeframe, db_path)`
  - Reuse as a cache loader that returns sorted UTC timestamps and numeric
    OHLCV columns.
- `AurumDataIngestor.fetch_ohlcv_range(...)`
  - Reuse the chunked OANDA date-range pattern, OANDA 5000 candle limit, UTC
    cursor logic, and final dedupe.
- `AurumDataIngestor._fetch_oanda_ohlcv_range_chunk(...)`
  - Reuse the REST endpoint shape: `/v3/instruments/{instrument}/candles`,
    `from`, `to`, `granularity`, and `price=M`.
- `AurumDataIngestor._normalize_oanda_candles(...)`
  - Reuse the candle normalization idea and closed-candle-only filtering.
- `AurumDataIngestor._finalize_ohlcv(...)`
  - Reuse sorting, duplicate removal, timestamp parsing, and numeric coercion.
- `AurumDataIngestor._records_for_sql(...)`
  - Reuse ISO UTC serialization for SQLite writes.
- `AurumDataIngestor._parse_datetime(...)` and `_to_utc(...)`
  - Reuse UTC parsing and timezone normalization.
- `scripts.fetch_oanda_history.deduplicate_candles(...)`
  - Reuse the explicit duplicate count idea.
- `scripts.audit_market_cache._missing_gap_count(...)`
  - Reuse the timeframe-gap inspection idea.
- `aurum1.features.engineer._merge_asof_on_index(...)`
  - Reuse backward as-of alignment for higher timeframe data.

## Risks Of Copying Directly

- Direct copies would preserve AURUM package names, phase terminology, runtime
  database paths, reports paths, and AURUM-specific assumptions.
- `aurum1/data/ingestion.py` creates non-OBSIDIAN tables such as macro, COT,
  news, event, trade, and performance tables.
- AURUM's OHLCV table names are timeframe-only (`ohlcv_M15`), while OBSIDIAN
  requires instrument and timeframe in the table name
  (`ohlcv_XAU_USD_M15`).
- AURUM uses a `timestamp` column; OBSIDIAN requires canonical
  `timestamp_utc` and separate New York-derived columns.
- AURUM feature/session logic uses UTC hour approximations, not
  `America/New_York` with DST.
- AURUM scripts contain runtime DB protections and output text specific to
  AURUM research/backtest caches.
- Pulling in feature engineering or research scripts would accidentally import
  strategy, risk, broker, and backtest dependencies.

## Recommended OBSIDIAN Folder Structure

```text
obsidian/
  README.md
  requirements.txt
  .gitignore
  src/
    obsidian/
      __init__.py
      config.py
      pipeline/
        __init__.py
        ingestion.py
        cache.py
        validation.py
        alignment.py
        sessions.py
      utils/
        __init__.py
        time.py
  scripts/
    fetch_oanda_history.py
    inspect_cache.py
  tests/
    test_cache.py
    test_validation.py
    test_alignment.py
    test_sessions.py
  data/
    .gitkeep
  results/
    .gitkeep
  docs/
    PHASE0_NOTES.md
```

## AURUM To OBSIDIAN Mapping

| AURUM-1 concept | OBSIDIAN Phase 0 module |
| --- | --- |
| OANDA env var names in `settings.yaml` | `src/obsidian/config.py` |
| `.env` read pattern from history script | `src/obsidian/config.py` and `scripts/fetch_oanda_history.py` |
| `AurumDataIngestor.fetch_ohlcv_range` | `src/obsidian/pipeline/ingestion.py::fetch_oanda_range` |
| OANDA 5000 candle chunking | `src/obsidian/pipeline/ingestion.py` |
| `_normalize_oanda_candles` | `src/obsidian/pipeline/ingestion.py::normalize_oanda_candles` |
| Closed-candle-only filtering | `normalize_oanda_candles(..., closed_only=True)` |
| `initialize_database` OHLCV table idea | `src/obsidian/pipeline/cache.py::ensure_ohlcv_table` |
| `persist_ohlcv` | `src/obsidian/pipeline/cache.py::save_ohlcv` |
| `load_ohlcv` | `src/obsidian/pipeline/cache.py::load_ohlcv` |
| `timestamp` UTC parsing | `src/obsidian/utils/time.py` |
| Duplicate removal before cache writes | `src/obsidian/pipeline/cache.py::deduplicate_ohlcv` |
| Market-cache audit script | `scripts/inspect_cache.py` |
| Gap counting | `src/obsidian/pipeline/validation.py::missing_timeframe_gaps` |
| UTC DatetimeIndex contract | `src/obsidian/pipeline/validation.py` |
| `merge_asof` higher-timeframe pattern | `src/obsidian/pipeline/alignment.py::align_higher_timeframe` |
| No-lookahead assertion idea | `src/obsidian/pipeline/alignment.py::assert_no_future_htf_leakage` |
| UTC-hour session flags | Rewritten as DST-aware `America/New_York` utilities in `src/obsidian/pipeline/sessions.py` |

## Phase 0 Output Created From This Audit

- Standalone template folder: `exports/obsidian_phase0_template/`
- Standalone zip artifact: `exports/obsidian_phase0_template.zip`

The template does not import AURUM-1 modules and does not modify AURUM-1
strategy, forward-shadow runtime, broker execution, deployment, dashboard,
or current research direction.
