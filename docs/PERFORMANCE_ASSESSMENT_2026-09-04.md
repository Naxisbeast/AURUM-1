# D4 Live Performance Assessment — Under-Risking Review

**Date**: 2026-09-04 · **Trades**: 148 · **Status**: Live paper, all services healthy

## TL;DR

D4 has a **statistically significant, healthy edge** (mean R +0.555, t = +4.47 on 148
trades). The "under-risking" finding (running ~0.17% vs the 0.35% config) is **not a bug and
does not need fixing before the 200-trade DSR gate**. Risk sizing is scale-invariant — it does
not touch the edge, only dollar PnL and drawdown. Raising risk is a risk-budget decision, and
the pre-registered gate says: confirm DSR at 200 trades first.

## Edge Quality (scale-invariant — unaffected by position sizing)

| Metric | Live | Backtest baseline | Read |
|--------|------|-------------------|------|
| Mean R | **+0.555** | ~+0.12 | ~4.6x baseline |
| 95% CI (mean R) | [+0.31, +0.80] | — | excludes 0 |
| t-stat (H0: R=0) | **+4.47** | — | significant at 5% |
| Std R | 1.510 | — | — |
| Per-trade Sharpe | +0.368 | — | — |
| Win rate | 52.0% | ~37% | hot but in range |
| Profit factor | 2.145 | ~1.14 | ~2x baseline |
| Daily Sharpe | +0.472 | — | annualized ≈ +7.5 |
| Max drawdown | **1.64%** | — | snapshot-derived |

**Verdict: the edge is real.** t = +4.47 is well past the ±1.96 significance line. This is
*not* a small-sample artifact riding on noise — the mean R is robustly above zero.

## Risk Deployed — the real numbers

| Layer | Value | Why |
|-------|-------|-----|
| Configured (`risk_per_trade_pct`) | **0.35%** | headline number in settings.yaml |
| Kelly intent | **0.088%** | 0.35% × kelly_max_fraction 0.25 |
| **Actual deployed (avg)** | **0.173%** | 1-unit floor forces risk UP past intent |
| Actual range | 0.100% – 0.351% | varies with ATR stop distance |
| 1-unit trades | 142 / 148 (96%) | gold min position dominates sizing |

**Key correction to the earlier finding:** the system is NOT running *below* its risk target.
It runs at ~2x its Kelly *intent* (0.173% vs 0.088%) because the 1-unit floor can't size lower.
The "0.35%" config is aspirational — Kelly caps it to 0.088% before the floor ever matters.

## Does Under-Risking Need Addressing?

**Short answer: not yet. Current sizing is defensible. Here is the honest logic:**

1. **Risk sizing does not change the edge.** Mean R, WR, PF, and Sharpe are identical whether
   you risk 0.1% or 1% per trade. Sizing only converts R into dollars and sets drawdown. So
   running at 0.17% is not "degrading performance" in any edge-quality sense.

2. **What it leaves on the table is dollars only.** At a flat 0.35% (no Kelly cap) the same
   edge projects to ~**+$3,350** over these 148 trades vs the observed +$1,655 — ~2x the
   dollars at ~2x the drawdown (per the Monte Carlo: each doubling of risk roughly doubles DD).

3. **The sample is below the pre-registered gate.** 148 trades is statistically significant
   (t=4.47) but still short of the **200-trade DSR gate** the project committed to. The gate
   exists precisely to make this kind of call with more evidence. Discipline says wait.

4. **Current drawdown is tiny** (1.64% max). There is genuine headroom — but *when* to take it
   is a gate decision, not a now decision.

### Decision rule going forward

- **If** the 200-trade DSR gate passes (raw DSR above the threshold at 200 trades), revisit
  lifting risk toward 0.35% flat — e.g. raising `kelly_max_fraction` toward 1.0, or raising
  `risk_per_trade_pct` — accepting ~2x drawdown for ~2x dollars.
- **If** the gate fails, staying small is exactly right: the edge is not confirmed enough to
  deploy more.
- The micro-sizing change (`min_units: 0.1`) is orthogonal — it would let sub-$1k accounts run
  D4 sanely but does not change this risk-budget decision for the existing account.

## Methodology

- Source: `paper_trading.sqlite3` (server snapshot 2026-09-04), 148 trades, 6,149 equity
  snapshots.
- Equity reconstruction: $10,000 initial, +net_pnl sequentially → per-trade equity-at-risk.
- Daily Sharpe: equity snapshots resampled to daily closes, pct_change, std ddof=1, ×√252.
- Max DD: running peak-to-trough on the snapshot equity series.
- R-distribution is treated as scale-invariant (correct for a % of-equity, fixed-SL sizing model).

## Artifacts

- Script: `scripts/research/run_performance_assessment.py`
- JSON: `reports/research/performance_assessment.json`
- Scaling simulation: `scripts/research/run_live_scaling_sim.py` →
  `reports/research/live_scaling_simulation.json`
- Related: `docs/DEPLOYMENT.md` (server state), `aurum1/config/settings.yaml` (risk config)
