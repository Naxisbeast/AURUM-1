# The Slippage Bug

**What happened**: The slippage model used a Gaussian distribution centered at zero. For market orders at breakout levels, this allowed "favorable slippage" — price improvement that doesn't happen in reality. A market buy at breakout always hits the ask, never the bid.

**Impact**: Backtests and paper trading were overstating returns by a small but systematic margin.

**Fix**: Changed to folded-normal (absolute of Gaussian) — slippage is always adverse for market orders.

**Lesson**: A bug that makes you look better is the most dangerous kind. It doesn't crash your system. It silently lies to you.
