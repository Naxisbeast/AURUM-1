# The Trade That Never Saved

**What happened**: For the first week, trades were executing in-memory but never saved to the SQLite database. The `_persist_trade()` method existed but wasn't being called consistently. I only noticed when the dashboard showed 0 trades.

**Impact**: If the server had restarted during those first 7 days, all trade history and equity tracking would have been lost.

**Fix**: Ensured every trade close path calls `_persist_trade()`.

**Lesson**: If it's not persisted, it doesn't exist. Test your persistence layer on day one, not day eight.
