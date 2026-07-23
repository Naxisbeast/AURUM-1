# Audit Decisions Record

This document records every significant design decision made in response to
the July 2026 external design review, along with the reasoning behind each
decision and the observed benefit.

---

## Decision 1: Archive `aurum1/models/` (1,944 lines)

**Feedback**: The review noted that `aurum1/models/` was still in the live
package despite being disabled in production (`enable_direction_predictor: false`)
and despite our own `mistakes/05-ml-added-no-edge.md` documenting that ML added
no edge. This was the single biggest gap between what the README claimed ("no ML
in production") and what the repo contained.

**Action**: Archived all 10 ML files (`ensemble.py`, `meta_labeler.py`,
`sentiment_model.py`, `regime_classifier.py`, `direction_predictor.py`,
`retrainer.py`, `overfitting.py`, `ablation.py`, `utils.py`, `__init__.py`) to
`archive/aurum1_ml_models/`. Created `aurum1/signals/_legacy_compat.py`
containing stubs for `SignalResult`, `REGIME_LABELS`, and a `RegimeClassifierStub`
so that `backtesting/engine.py` and `walk_forward.py` continue to resolve their
imports. The stubs maintain the same interface — any code path that depended on
the full ML pipeline will still compile and run (it's disabled by default in
settings.yaml).

**Why it's beneficial**: The repo now tells the truth. The ML code is preserved
in archive for reference but no longer lives in the live package. A reviewer
reading "no ML in production" won't find a 2,000-line ML package contradicting
it.

---

## Decision 2: Remove `run_d4_walk_forward.py` (v1 with hardcoded path)

**Feedback**: The reviewer noted that `run_d4_walk_forward.py` had
`ROOT = Path('/opt/aurum1')` — a hardcoded path that made the script
un-runnable for anyone who cloned the repo. The `_v2` version fixed this
with `Path(__file__).resolve().parents[2]` but the v1 was still present.

**Action**: Archived `run_d4_walk_forward.py` (v1) to `archive/scripts/`.
The v2 version is now the canonical script.

**Why it's beneficial**: Anyone cloning the repo can run the walk-forward
without editing scripts. The `_v2` naming convention is eliminated. No
duplicate scripts to maintain.

---

## Decision 3: Rename repo description to "Public quantitative systems research journal"

**Feedback**: The review described the project as "a research journal with
a production-grade validation harness bolted on, not a trading system." The
trading strategy (D4) is about 150 lines of decision logic; everything else
exists to validate or monitor it. Calling it a "trading system" undersells
the actual work (validation infrastructure) while overclaiming what the
trading results demonstrate (29 paper trades is not evidence).

**Action**: Updated the README tagline from "My public quantitative research
laboratory" to reflect the journal framing. The `research/` and `mistakes/`
directories already support this framing.

**Why it's beneficial**: Honest framing attracts the right audience (engineers,
researchers, recruiters) instead of the wrong one (people looking for a
profitable trading bot). It also sets appropriate expectations: the main
output is research and engineering process, not trading returns.

---

## Decision 4: Separate live evidence from simulated evidence visually

**Feedback**: STATUS.md presented 27-trade live results in a performance table
styled identically to walk-forward/Monte Carlo tables. A skimming reader would
reasonably conflate "simulated on history" with "actually happened live."

**Action**: STATUS.md now clearly labels live vs simulated sections with
explicit headers and grouping. The pre-registered gate criteria (50-trade,
100-trade) make clear that 27 trades is not yet evidence.

**Why it's beneficial**: Prevents misinterpretation. A recruiter or professor
reading the repo won't conflate backtested results with live-trade confirmation.

---

## Decision 5: Pre-register gate criteria with fail branches

**Feedback**: The review noted that pre-registering a DSR ≥ 0.95 criterion
without also writing down what happens if it's NOT met is not a real gate —
it's a way to quietly move the goalposts when the number comes in below
expectation.

**Action**: STATUS.md now has explicit pass AND fail branches for both the
50-trade risk review and 100-trade strategy review gates. The fail branches
include concrete next actions ("extend to 200 trades," "demote to shadow-only,"
"stay in paper, no capital consideration") written before the trade counts
are reached.

**Why it's beneficial**: Prevents motivated reasoning. The decision is made
now, when there are 29 trades and no emotional stake in the outcome, rather
than at 100 trades when there *will* be an emotional stake.

---

## Decision 6: Build trial ledger and Deflated Sharpe Ratio machinery

**Feedback**: The review identified that D4's selection process (testing D1-D7
and picking the best performer) inflates the apparent edge — the deflated
Sharpe ratio (Bailey & López de Prado, 2014) corrects for this.

**Action**: Built `aurum1/research/trial_ledger.py` (append-only SQLite ledger
for every backtest trial) and `aurum1/research/deflated_sharpe.py` (full DSR
implementation). The actual DSR computation will be run at the 100-trade gate
when there's enough data. Added paper reference to `research/references/`.

**Why it's beneficial**: The machinery is in place and tested before it's
needed. When the 100-trade gate arrives, the DSR check can be run immediately
rather than having to build it under pressure.

---

## Summary of impact

| Metric | Before | After |
|--------|--------|-------|
| Live ML code in package | 1,944 lines | 0 lines |
| Duplicate scripts | 2 (v1 + v2) | 1 (v2 canonical) |
| Hardcoded absolute paths | 1 script | 0 scripts |
| Pre-registered gate criteria | None | Both gates, pass+fail |
| Trial ledger | None | Working, 9 tests |
| DSR implementation | None | Working, 10 tests |
| Repo description | "research laboratory" | "research journal" |
| Test suite | 265 passing | 283 passing |
