# Project OBSIDIAN Phase 0 Template

OBSIDIAN Phase 0 is a standalone data-infrastructure starter for ICT concept
hypothesis testing. It is not a trading bot and does not place orders.

## What Phase 0 Includes

- OANDA OHLCV history fetching for XAU_USD/M15 first.
- SQLite market cache tables named `ohlcv_<instrument>_<timeframe>`.
- Canonical UTC timestamps stored in `timestamp_utc`.
- Separate America/New_York session-derived columns.
- Closed-candle-only OANDA normalization by default.
- Validation helpers for duplicates, gaps, OHLC integrity, UTC timestamps,
  incomplete candles, stale latest candle checks, and sorted timestamps.
- Multi-timeframe alignment helpers designed to avoid future HTF leakage.
- Cache inspection and history fetch scripts.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Create a local `.env` file if you want to fetch from OANDA:

```text
OANDA_API_KEY=your-practice-api-token
OANDA_ACCOUNT_ID=your-practice-account-id
OANDA_ENV=practice
```

`.env` is ignored by Git and is not included in this template zip.

## Fetch OANDA History

```bash
python scripts/fetch_oanda_history.py --instrument XAU_USD --timeframe M15 --years 1
```

The default cache path is `data/obsidian_market_cache.sqlite3`.

## Inspect Cache

```bash
python scripts/inspect_cache.py --instrument XAU_USD --timeframe M15
```

## Run Tests

```bash
pytest
```

## Phase 0 Data Contract

Required OHLCV columns:

- `timestamp_utc`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `complete`
- `instrument`
- `timeframe`

Session columns are derived separately and never replace `timestamp_utc`.
