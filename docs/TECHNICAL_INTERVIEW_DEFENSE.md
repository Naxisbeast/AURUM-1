# AURUM-1 Technical Interview Defense

**Purpose**: Prepare to personally explain and defend every architectural,
testing, deployment, and design decision in AURUM-1. This is not a summary —
it's the "why" behind every choice, including the trade-offs and drawbacks
you accepted.

**How to use this**: For each topic, be ready to explain (1) what you built,
(2) WHY you chose that approach, (3) what alternatives you considered and
rejected, and (4) what the drawback is. Interviewers care most about the
reasoning, not the answer.

---

## 1. Architecture

### What AURUM's architecture is

```
┌─────────────────────────────────────────────────────┐
│  FORWARD SHADOW (data pipeline)                      │
│  forward_shadow_donchian.py                          │
│  Polls OANDA every 60s → writes M15 candles to SQLite│
└──────────────────────┬──────────────────────────────┘
                       │ reads fresh data
                       ▼
┌─────────────────────────────────────────────────────┐
│  D4 PAPER TRADER (decision engine)                   │
│  d4_paper_trader.py                                  │
│  Polls cache every 60s → Donchian 20 breakout check  │
│  → RiskManager → PaperBroker → persists to SQLite    │
└──────────────────────┬──────────────────────────────┘
                       │ writes trades/snapshots
                       ▼
┌─────────────────────────────────────────────────────┐
│  MONITORING (reads only)                             │
│  Streamlit dashboard → Cloudflare tunnel             │
│  Independent watchdog process (kill switch)          │
└─────────────────────────────────────────────────────┘
```

**Why this architecture?**
- **Separation of concerns**: The data pipeline (forward shadow) is completely
  decoupled from the decision engine (D4 trader). This means:
  - If one crashes, the other keeps running
  - The data pipeline can serve multiple strategies simultaneously
  - Improvements to one don't require touching the other
- **Single source of truth**: SQLite files are the persistence layer. Everything
  reads/writes the same DB. No duplicated state in memory that could drift.

**What did I consider and reject?**
- **A monolithic orchestrator** (the original design) — one process did
  everything: data + features + models + signals + risk + execution. I built
  this first and it was fragile. A bug in data ingestion would crash the
  trading loop. Killing it and decoupling was the refactor that made the
  system reliable.
- **A message queue / broker** (like Redis or Kafka) between components — overkill
  for a single-host system. SQLite + file polling is simpler and sufficient at
  this scale. I consciously accepted the trade-off: no real-time push, but much
  less infrastructure to maintain.

**Drawback I accepted**: The 60-second polling design means there's up to 60
seconds of latency between a market event and the system reacting. For M15
candles (15 minutes), that's negligible — but it means this architecture
could NOT do high-frequency trading. That's a deliberate scope decision.

---

## 2. Testing Philosophy

### What I test and why

265 automated tests across:
- **Unit tests**: PaperBroker (SL/TP logic, slippage), RiskManager (Kelly,
  kill switches), instruments (sizing math), signals
- **Regression tests**: D4 strategy on known data, trade-count reproducibility
- **Integration-ish tests**: Execution engine logging to SQLite, dashboard metrics
- **Audit tests**: watchdog thresholds, trial ledger, Deflated Sharpe Ratio

**Why this philosophy?**
- **Test what can lose money**: The PaperBroker SL/TP logic is the most
  safety-critical code — if it closes positions wrong, real money is at risk.
  It has the most tests.
- **Test what broke before**: Every bug I hit (Kelly double-cap, slippage model,
  persistence) got a regression test added. The tests exist because something
  actually broke, not to hit a coverage number.
- **Tests are documentation**: A test that says "BUY with SL gap should close at
  candle.open" documents intended behavior better than a comment.

**Trade-off I accepted**: I don't test the Streamlit UI rendering (it's hard to
unit test a web UI). I test the metric computations underneath it instead. The
UI is thin; the logic is what matters.

**How to defend**: If asked "why 265 tests and not more?", the honest answer is
that I test the *risk-critical* and *previously-broken* paths thoroughly, and
accept thin coverage on the display layer where a bug is visible, not dangerous.

---

## 3. Deployment

### How the system runs

- **Host**: Hetzner cloud VM (Ubuntu 24.04)
- **Process manager**: systemd services (one per component)
- **Remote access**: SSH with key auth (no password)
- **Public exposure**: Cloudflare tunnel → stable domain `dashboard.auram.software`

**Why systemd, not Docker or Kubernetes?**
- The professor's feedback explicitly raised containerization. I investigated it
  and **deliberately decided against it** (documented in
  `docs/system/CONTAINERIZATION_DECISION.md`).
- systemd already provides: restart-on-failure, logging, dependency ordering,
  resource limits. Docker adds image builds + volume management for file-based
  SQLite, with no benefit for 5 services on one host.
- **The trade-off**: less reproducible environments than containers. If someone
  else wanted to run AURUM, they'd need to replicate the systemd setup. I
  accepted this because portability isn't a current requirement.

**Why Cloudflare tunnel instead of opening a port?**
- Opens zero inbound ports on the server (security win)
- Gives HTTPS automatically
- The random trycloudflare URL was a problem — it changed on every restart. I
  bought `auram.software` and set up a named tunnel for a stable URL.

**Debugging story worth telling**: The tunnel initially used a quick tunnel
(`--url`), which generated random URLs that rotated. Twice the dashboard URL
just died. I migrated to a named tunnel with a token. The systemd service kept
failing with a subtle bug: `cloudflared tunnel --token X run` printed help and
exited with status 0 (which *looks* like success) because the `--token` flag
was in the wrong position relative to `run`. Fixing it required understanding
cloudflared's actual argument parsing, not just reading the error.

---

## 4. Debugging Stories (the important ones)

### Story 1: The Kelly double-cap bug
**Symptom**: Position sizes were near zero despite the strategy having edge.
**Root cause**: Two safety caps on the Kelly fraction multiplied against each
other — `kelly_cap` AND `kelly_max_fraction`. Each was "conservative" on its
own; together they canceled the position entirely.
**Why it was hard**: Nothing crashed. No exception. The system just quietly
sized every trade down to nothing. It looked like caution, not a defect.
**The fix**: Removed the double cap; single cap on `kelly_max_fraction`.
**The lesson**: Redundant safety mechanisms don't add up — they can cancel each
other out. Only way to catch it: compute what the combined logic does to real
numbers.

### Story 2: The favorable-slippage bug
**Symptom**: Backtest returns were overstated.
**Root cause**: The slippage model used a Gaussian centered at zero, which
allowed *favorable* slippage (price improvement). Market orders at breakout
levels never get favorable slippage — they always cross the spread.
**The fix**: Folded-normal (absolute value of Gaussian) — slippage is always
adverse.
**The lesson**: A bug that makes you look *better* is the most dangerous kind.
It doesn't crash; it silently lies to you about performance.

### Story 3: Trades not persisting
**Symptom**: Dashboard showed 0 trades despite the system executing them.
**Root cause**: The `_persist_trade()` method existed but wasn't called on every
close path. Trades lived only in memory for a week.
**The risk**: If the server had restarted, all trade history and equity tracking
would be lost.
**The fix**: Ensure every trade close path persists to SQLite.

### Story 4: The dead server / tunnel incident
**Symptom**: Dashboard URL stopped loading; SSH showed a "Palo Alto" banner;
port 80 served a login page.
**Diagnosis**: The IP wasn't reachable — the Cloudflare tunnel was down, and
something in the network path (not the server) was intercepting port 22.
**Resolution**: Restarted the tunnel service; later migrated to a permanent
named tunnel with a stable domain. All 76 trades and state survived because
they're in SQLite.
**The lesson**: SQLite persistence + state recovery means a full server
power-cycle is recoverable. The system came back exactly where it left off.

---

## 5. Trade-offs (be ready to defend these)

| Decision | Why chosen | Drawback accepted |
|----------|-----------|-------------------|
| SQLite not PostgreSQL | Single file, no server, zero config | No concurrent-write scaling; single-writer |
| systemd not Docker | Simpler, already handles restart/logging | Less portable environments |
| 60s polling not event-driven | Simple, sufficient for M15 | Up to 60s reaction latency |
| Python not C++/Rust | Fast to develop, good data libs | Slower execution (fine for M15) |
| Paper trading first | Catches real bugs (persistence, restart) | No live-money stress testing |
| Folded-normal slippage | Realistic (no favorable fills) | Slightly pessimistic backtests |
| Monolithic → decoupled refactor | Reliability (component isolation) | More moving parts to deploy |

---

## 6. Concurrency

AURUM is **mostly single-threaded by design** — this is intentional, not a gap.

**What's concurrent:**
- **5 systemd services** run in parallel (trader, shadow, dashboard, watchdog,
  tunnel). They communicate only through SQLite files, so there's no shared
  in-memory state to race on.
- **ThreadPoolExecutor** in the data ingestor for fetching multiple timeframes
  in parallel (network-bound, so threads are appropriate).

**What's single-threaded and why:**
- The D4 trading loop is single-threaded. It processes candles sequentially —
  there's only ever one position to manage (Donchian = sequential entries), so
  concurrency would add risk with zero benefit. A single-threaded loop cannot
  have race conditions.

**How I handle concurrency issues:**
- SQLite has a **single-writer** model. The trader is the only writer to
  `paper_trading.sqlite3`; the dashboard only reads. This avoids lock contention
  by design — no two processes write the same DB.
- The watchdog reads the health file; the trader writes it. Reader/writer
  separation avoids races.

**Trade-off**: I chose not to make the trading loop concurrent. If it were
multithreaded, I'd need locks around position state, which is exactly where a
race could double-trade. Single-threaded is the *safe* choice for a system
where correctness matters more than throughput.

---

## 7. Persistence

### What persists and where

| Data | Location | Why |
|------|----------|-----|
| Trades | `paper_trading.sqlite3` | Full trade history for analysis |
| Account snapshots | `paper_trading.sqlite3` | Equity curve every ~15 min |
| Open positions | `paper_trading.sqlite3` | Survive restart |
| Missed signals | `paper_trading.sqlite3` | Track why trades were rejected |
| Market data | `forward_shadow_market_cache.sqlite3` | M15 candles |
| Trial ledger | `trial_ledger.sqlite3` | Every backtest variant, for DSR |
| Health file | `run/d4_paper_trader_health.json` | JSON for dashboard/watchdog |

### Why SQLite, defensibly
- It's **ACID** — transactions are atomic. A crash mid-write won't corrupt the DB.
- It's **embedded** — no server to run, no connection pool, no auth. Perfect for
  a single-host system.
- **WAL mode** (write-ahead logging) allows readers to proceed while a writer is
  active — good for the dashboard reading while the trader writes.
- SQLite handles **survivable restarts** — this is the core reliability feature.
  The 76 trades and equity survived a full server power-cycle.

### Drawback I accepted
SQLite doesn't scale to concurrent writers across many machines. If AURUM ever
moved to multiple servers, I'd need PostgreSQL. That's a future problem, not a
current one.

---

## 8. API Design

AURUM has two "APIs":

### Internal — the broker interface
```
BrokerBase (abstract)
├── PaperBroker (in-memory simulation)
└── OandaBroker (real OANDA v20 REST)
```

**Why an abstract base**: The trading engine should not care whether it's
talking to a simulated or real broker. This makes the system testable (paper
broker in tests, OANDA in production) and safely switches between them via
settings (`paper_trade: true`). This is a classic dependency-inversion
pattern.

### External — OANDA REST (consumed)
- Only used in the data pipeline (forward shadow)
- Credentials via environment variables (`OANDA_API_KEY`), never hardcoded
- Safety interlocks: `ALLOW_OANDA_ORDERS=false` blocks real orders; `OANDA_ENV`
  locked to practice

### External — the dashboard (serves)
- Streamlit reads from SQLite and renders. It never sends commands to the
  trading system — **read-only**, which is a deliberate security boundary.

**Defensible point**: I chose to NOT expose a full HTTP API for the trading
system. The dashboard reads the DB directly. This trades away the ability to
programmatically query trades for the security of having no write surface
exposed. For a solo system, that's the right call.

---

## 9. Refactoring Decisions

### The ML → no-ML refactor
**What**: Removed the entire ML ensemble (regime classifier, direction
predictor, sentiment scorer) from the live pipeline.
**Why**: D6 (ML) had identical profit factor to D4 (no ML) — the ML added
nothing measurable and introduced fragile dependencies.
**How**: Archived the ML code (`archive/aurum1_ml_models/`), kept the interface
via a compatibility shim so the backtest engine still imports cleanly.
**Defensible**: This shows I can *remove* complexity when the data says it's not
earned — the hardest engineering decision.

### The dead-code archive discipline
**What**: Every dead module (experiments, orchestrator, ML, phase audits) is in
`archive/` with a README explaining why it was retired.
**Why**: The repo should tell the truth about what runs. Dead code in the live
tree means readers can't tell what's real.
**Defensible**: "Everything earns its place, including AURUM itself."

### The `parents[2]` path fix
**What**: 27 files had `parents[1]` (wrong path depth) after a script
reorganization, breaking imports.
**Why it matters**: It's a concrete example of a real bug from refactoring —
moving files without updating path resolution. Fixed systematically (all
instances), not one-off.

---

## 10. How to Prove You Personally Built This

The professor explicitly wants the interviewer to verify you understand the
architecture, not that you generated it. Be ready to:

1. **Explain any file's role in 2 sentences** — e.g. "broker.py implements the
   execution layer with an abstract base so PaperBroker (simulated) and
   OandaBroker (real) are interchangeable, and PaperBroker handles SL/TP
   natively by evaluating each candle against open positions."

2. **Re-derive the core math by hand** — Kelly criterion, 2R position sizing,
   R-multiple calculation. If you can't write them on a whiteboard, you don't
   own the system.

3. **Trace a specific bug to its fix** — pick any of the debugging stories above
   and walk through: symptom → hypothesis → root cause → fix → regression test.

4. **Defend a trade-off from both sides** — e.g. "I chose SQLite for its
   simplicity and ACID guarantees, accepting that it doesn't scale to
   multi-writer workloads; if AURUM grew to multiple servers I'd migrate to
   PostgreSQL, but that migration isn't justified today."

5. **Say what you'd do differently** — the strongest signal of ownership.
   E.g. "If I rebuilt this, I'd add proper integration tests with a fake OANDA
   server earlier, and I'd build the trial ledger from day one instead of
   reconstructing it."

---

## Quick Reference — "What does X do?" One-liners

| File | What it does | Why it exists |
|------|-------------|---------------|
| `broker.py` | Paper/Oanda broker, SL/TP, slippage | Executes trades, tests execution logic |
| `risk/manager.py` | Kelly sizing, kill switches | Prevents over-risk, caps drawdown |
| `instruments.py` | XAU/USD unit math | Correct position sizing |
| `d4_paper_trader.py` | The trading loop | Generates signals, manages the cycle |
| `forward_shadow_donchian.py` | Data pipeline | Fetches/stores market data |
| `ingestion.py` | SQLite I/O, settings loading | Persistence + config |
| `monitor/metrics.py` | Dashboard metrics | Computes Sharpe/PF/DD from DB |
| `d4_watchdog.py` | Independent kill switch | Survives trader crash |
| `research/trial_ledger.py` | Logs every backtest trial | Feeds Deflated Sharpe Ratio |
| `research/deflated_sharpe.py` | Selection-bias correction | Validates edge is real, not luck |
| `scripts/audit/*.py` | Capacity, determinism, decay | Proves the system is trustworthy |

---

## 11. CI/CD and Deployment Pipeline

### What CI/CD exists

```
[push/PR to main] → [GitHub Actions] → [9 test groups, 265 tests] → [pass/fail badge on README]
```

**The pipeline**: `.github/workflows/test.yml` runs on every push to main and
every pull request. It:
1. Sets up Python 3.12
2. Installs dependencies from `requirements.txt`
3. Runs 9 groups of tests in parallel (core, D4 regression, trade quality,
   prop firm, evidence, execution, metrics, forward shadow, watchdog)

**Why CI matters here**: A trading system has a hard correctness requirement.
CI catches a broken import or a risk-logic regression the moment it's committed,
before it can affect the live system. The 27 `parents[2]` path bugs I fixed would
have been caught immediately if I'd had full CI from the start.

**Deployment**:
- Code is pushed to GitHub → **manual deploy** to the server via SSH (the system
  isn't auto-deployed; a human reviews and pushes)
- The server runs 5 systemd services that pick up the new code on restart
- **Why not full CD (auto-deploy)?** A trading system should not auto-deploy
  without review. A bad auto-deploy could stop trading or, worse, trade wrong.
  Manual deploy is a deliberate safety choice.

**Security in the pipeline**:
- **Secret scanning**: GitHub-native, catches credentials in commits
- **Dependabot**: auto-detects vulnerable dependencies, opens PRs
- **SBOM**: GitHub auto-generates a software bill of materials
- **Pre-commit hook**: blocks `.env` files and credential patterns locally

---

## 12. Future Improvements (and why)

An interviewer will ask "what would you do next?" — here's the honest answer
that shows forward thinking without overpromising.

### Immediate (evidence collection)
- **Let D4 reach 100 trades** — the pre-registered strategy review gate, where
  the Deflated Sharpe Ratio gets computed for real
- **Build the 100-trade gate tooling now** — wire the DSR check to run
  automatically when the gate hits

### Near-term (engineering)
- **Integration tests with a fake OANDA server** — test the full data pipeline
  and broker interactions without hitting the real API. This is the biggest
  testing gap.
- **Deterministic RNG throughout** — already verified the backtest is
  deterministic; would formalize it as a CI check.

### Medium-term (architecture)
- **Second uncorrelated strategy** — the highest-value research direction.
  Diversify return streams rather than optimizing D4 further (which we proved
  fails).
- **PostgreSQL migration** — only if the system grows beyond single-writer
  SQLite limits. Not justified today.
- **Proper event-driven pipeline** — if I wanted sub-second reaction, I'd need
  to move off 60s polling. Not justified for M15 trading.

### Long-term (the FPGA/C++ direction)
- **Migrate core algorithmic layers to bare-metal C++ and FPGA (SystemVerilog)**
  to study bypassing OS/kernel scheduling latency. This is a research goal, not
  a production need — it's about learning systems-level engineering.

**Why "what would you do differently" is your strongest answer**: It shows you
own the decisions, not just the code. E.g. "I'd build the trial ledger from day
one, and I'd add integration tests earlier — both would have saved debugging
time."

---

## 13. Industry-Style Interview Q&A (with model answers)

These are the questions an interviewer would actually ask, with the answers
you should give. Practice saying them out loud — your own words, not memorized.

### Q1: Explain your architecture in 2 minutes.
**A**: "AURUM is a paper-trading validation platform. It has three decoupled
layers: a data pipeline that fetches gold price data and stores it in SQLite; a
trading engine that reads that data, applies a Donchian breakout strategy,
checks it through a risk manager, and executes through a simulated broker; and
a monitoring layer that reads the results and displays them on a dashboard. The
key decision was decoupling data from trading — so a data failure doesn't
crash the trading loop, and the data can serve multiple strategies."

### Q2: Why SQLite instead of a real database?
**A**: "SQLite gives me ACID transactions and restart survival with zero
operational overhead — no server, no connection pool, no auth. For a
single-host system with one writer, it's the right tool. I accepted that it
doesn't scale to multi-writer workloads, but that's a future problem. If AURUM
grew to multiple servers, I'd migrate to PostgreSQL — and I can articulate that
migration."

### Q3: Your system is single-threaded. Isn't that a weakness?
**A**: "It's deliberate. The trading loop is single-threaded because there's
only ever one position (Donchian = sequential entries), and single-threaded
code can't have race conditions around position state. The concurrency that
exists is at the service level — five systemd services run in parallel and
communicate only through SQLite, which avoids shared-memory races. I chose
correctness over throughput, which is the right trade for a trading system."

### Q4: Walk me through a production failure and how you fixed it.
**A**: "The most interesting one was the Kelly double-cap bug. The system was
sizing positions to near-zero despite the strategy having edge. Nothing crashed
and no exception was raised — the two safety caps on the Kelly fraction
multiplied against each other and silently canceled positions. It looked like
caution, not a defect. I traced it by computing what the combined logic actually
did to position sizes, found the double cap, removed one, and added a regression
test. The lesson: redundant safety mechanisms can cancel each other out, and
the only way to catch that is to compute real numbers."

### Q5: How do you know your backtest results are trustworthy?
**A**: "Five ways. First, walk-forward validation on 18 out-of-sample windows
over 11 years — not one backtest. Second, Monte Carlo simulation shuffling trade
outcomes 10,000 times to check ruin probability. Third, a stationarity test to
confirm the signal isn't just noise. Fourth, a determinism audit — I ran the
backtest twice and confirmed identical results, so there's no unseeded
randomness. Fifth, a Deflated Sharpe Ratio that corrects for the fact that I
tested multiple variants before picking the winner. Together those give me
confidence the edge is real, not luck."

### Q6: Why did you remove the machine learning?
**A**: "Because the data said it added nothing. The ML ensemble variant had a
profit factor of 1.14 — identical to the no-ML variant. The ML introduced
fragile dependencies without any measurable improvement. Removing it was the
right call because AURUM's philosophy is that complexity must justify its
existence. I documented the failure in a mistakes/ folder and archived the code
rather than deleting it — knowing why it failed is as valuable as knowing why
D4 works."

### Q7: How would you handle real (live) money?
**A**: "I wouldn't yet. The system is in evidence collection at 0.35% risk with
a pre-registered gate: at 100 trades, the Deflated Sharpe Ratio determines
whether D4's edge survives selection-bias correction. Only if it clears that
gate would I consider live capital, and even then I'd start with a micro lot
under close monitoring. The discipline of pre-registering the decision before
seeing the results prevents me from moving the goalposts when the number comes
in."

### Q8: What's the hardest engineering problem you solved?
**A**: "Not writing the strategy — building validation infrastructure rigorous
enough to catch my own mistakes. The clearest example was the slippage bug:
the model allowed favorable slippage, which silently overstated backtest
returns. Nothing crashed. The system just lied about performance. Catching a
bug that makes you look better is harder than catching one that crashes,
because it doesn't announce itself. That taught me that the most dangerous
bugs are the ones that flatter you."

---

## Summary: The Interviewer's Verification Checklist

When the interviewer probes whether you *personally* understand the system, they
will check that you can:

- [ ] Explain the architecture in 2 minutes without reading anything
- [ ] Re-derive the Kelly / 2R / R-multiple math by hand
- [ ] Walk through 2-3 real debugging stories end-to-end
- [ ] Defend a trade-off from both sides (why X, and X's drawback)
- [ ] Explain why you chose single-threaded + SQLite + polling
- [ ] Describe CI/CD and why deployment is manual (safety)
- [ ] Articulate future improvements and what you'd do differently
- [ ] State honestly what's NOT yet proven (live edge, DSR at 100 trades)
