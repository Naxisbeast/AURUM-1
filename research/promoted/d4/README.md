# D4 — Donchian 20, 2R Exit, BUY+SELL, No Filters

**Status**: ✅ Promoted — paper trading live at 0.35% risk since July 2, 2026.

**Evidence**:
- Walk-forward (18 windows, 11 years): 88.9% positive windows
- Monte Carlo (10,000 simulations): 0% ruin probability
- TC stress test: survives 6p spread + 2p slippage (PF 1.09)
- Signal stationarity (ADF): ✅ Stationary — not trading noise
- Live trades: 29 and counting

**Why D4 won**: It's the simplest configuration. The 2R exit compensates for a ~37% win rate. SELL direction added +$25,522 over 11 years compared to BUY-only. No filters meant it never missed a good trade to avoid a bad one.

**Key insight**: D4 needed nothing but price data. The ML ensemble variant (D6) produced identical results with fragile dependencies.
