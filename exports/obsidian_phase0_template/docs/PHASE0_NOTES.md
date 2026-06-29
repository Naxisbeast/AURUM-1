# OBSIDIAN Phase 0 Notes

Phase 0 is data-only. Its job is to fetch, cache, validate, inspect, and align
market candles so later hypothesis tests can be built on a clean UTC contract.

## Timestamp Rules

- Store canonical market timestamps in `timestamp_utc`.
- Treat `timestamp_utc` as immutable after ingestion.
- Derive New York fields into separate columns.
- Reject or flag naive timestamps and non-UTC offsets during validation.
- Use `America/New_York` through Python `zoneinfo` so DST is handled by the
  timezone database.

## OANDA Rules

- Default instrument: `XAU_USD`.
- Default timeframe: `M15`.
- Fetch mid candles with `price=M`.
- Filter to complete candles by default.
- Never place orders or construct broker/order objects.

## Session Windows

The initial session utilities use these New York local-time windows:

- London killzone: 02:00 to 05:00.
- NY AM killzone: 08:30 to 11:00.
- Silver bullet: 10:00 to 11:00.
- London close: 10:00 to 12:00.
- Asia session: 18:00 to 24:00.

These are labels for hypothesis testing inputs, not trading signals.
