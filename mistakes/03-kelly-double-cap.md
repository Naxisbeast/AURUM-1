# The Kelly Double-Cap

**What happened**: The Kelly calculator had two caps applied in sequence — `kelly_cap` and `kelly_max_fraction`. The result was near-zero position sizes despite the strategy showing positive edge.

**Impact**: If I'd switched to Kelly-based sizing, positions would have been microscopic regardless of edge.

**Fix**: Removed the double cap. Kelly now uses a single cap.

**Lesson**: Two safety nets don't make you twice as safe. Sometimes they cancel each other out entirely.
