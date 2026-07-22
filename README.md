# AURUM-1

> My public quantitative research laboratory.

I'm a Computer Science and Electronics student who became obsessed with a simple question:

> How do professional trading systems know when they're actually right?

That question eventually became AURUM.

This repository documents my journey of building an autonomous quantitative research platform — from failed experiments and overengineered ideas to statistically validated trading systems and the lessons they taught me along the way.

The goal isn't to find a profitable strategy.

The goal is to build a system capable of honestly discovering one.

---

## What AURUM Is

Today AURUM is:

- An autonomous paper trading system running on a cloud server
- A quantitative research platform with 265 tests
- A software engineering project I've rebuilt more times than I can count
- A personal learning laboratory
- A public journal of my growth as an engineer and researcher

The strategies are temporary.

The research process is the product.

---

## Things That Didn't Work

Building AURUM has taught me that complexity is easy. Being correct is hard.

Some failed experiments include:

- Machine learning ensembles that added virtually no edge
- Sophisticated market regime classifiers
- Multi-stage state machine entry systems
- Over-filtered breakout strategies
- Incorrect slippage models that overstated returns
- Broken persistence layers that could have lost trade history
- Risk models that looked good on paper and failed in practice

Every failed experiment is archived and documented because knowing why something doesn't work is often more valuable than knowing why it does.

---

## Where It Stands

D4 — Donchian 20-bar breakout, 2R exit, BUY+SELL — is currently the strongest candidate discovered through research. It's paper trading autonomously on a cloud server at 0.35% risk per trade.

| Test | Result |
|------|--------|
| Walk-forward (18 windows, 11 years) | 88.9% positive windows |
| Monte Carlo (10,000 simulations) | 0% ruin probability |
| TC stress (6p spread + 2p slippage) | Still profitable (PF 1.09) |
| Signal stationarity (ADF test) | ✅ Stationary — not trading noise |
| Live paper trades | 29 trades and counting |

[Full status →](docs/STATUS.md)
[Live dashboard →](https://wear-boot-jennifer-brush.trycloudflare.com)
[The full story →](docs/JOURNEY.md)

---

## Architecture

```
                 RESEARCH                   
                      ↓                     
              STRATEGY VALIDATION           
                      ↓                     
                PAPER TRADING               
                      ↓                     
                 MONITORING                 
                      ↓                     
            EVIDENCE COLLECTION             
                      ↓                     
                 DECISIONS                  
```

**Technical architecture:**

```
OANDA API → Forward Shadow (data pipeline) → Market Cache (SQLite)
                                                    ↓
                                           D4 Paper Trader
                                           (Donchian 20 · 2R · BUY+SELL)
                                                    ↓
                                           PaperBroker (simulated execution)
                                                    ↓
                                           Paper Trading DB (SQLite)
                                                    ↓
                                           Streamlit Dashboard → Cloudflare Tunnel
```

**Key decisions:**
- Data pipeline decoupled from trading (separate services)
- PaperBroker handles SL/TP natively with session-aware spread and folded-normal slippage
- Kill switches run in-process; a separate watchdog monitors independently
- All trades, snapshots, and missed signals persist to SQLite — survives restart

---

## What I'm Learning By Building This

- Algorithmic trading systems
- Quantitative research methodologies
- Monte Carlo simulations and walk-forward analysis
- Risk management and position sizing
- Cloud infrastructure and autonomous systems
- Software architecture and testing
- Data engineering and pipeline design
- Market microstructure
- AI-assisted development workflows
- Learning to prove my own assumptions wrong

---

## Questions I'm Trying to Answer

Building AURUM is really an excuse to answer difficult questions:

- What makes a trading strategy robust?
- Can simplicity consistently outperform complexity?
- How much evidence is enough before trusting a strategy?
- How should autonomous systems manage risk?
- What can software engineering teach us about financial systems?
- Can one person build an honest quantitative research process?

I'm still trying to answer all of them.

---

## Principles That Emerged

1. **Data decides.** Not intuition. Not gut feel. Not what I want to be true.
2. **Simplicity beats complexity.** If a 20-bar channel competes with a gradient-boosted ensemble, the channel wins.
3. **No strategy earns trust without evidence.** Walk-forward, Monte Carlo, TC stress — every number gets verified.
4. **Dead code gets archived.** The repo should tell the truth about what runs.
5. **Every bug becomes documentation.** If it broke once, it'll break again.
6. **Production is the final backtest.** Paper trades reveal what backtests can't.
7. **Failed experiments are still valuable.** Every rejected strategy taught me something.
8. **Complexity must justify its existence.** Every filter and feature must prove it adds more edge than it removes.
9. **Never optimise around short-term results.** 27 trades is noise. 100 trades is a conversation. 500 trades is evidence.
10. **No strategy gets promoted without earning it.**

---

## What's Next

**Current phase:**
- Autonomous paper trading at 0.35% risk
- Evidence collection toward 50-trade risk review gate
- Analytics pipeline development

**Near term:**
- Real-time execution analytics
- Prop firm challenge simulations
- Multi-strategy experimentation framework
- Improved dashboard and monitoring

**Long term:**
- Multi-asset quantitative research
- Portfolio-level strategy management
- Advanced ML experimentation
- Real capital deployment (only if the evidence supports it)

No strategy gets promoted without earning it.

---

## Quick Start

```bash
git clone git@github.com:Naxisbeast/AURUM-1.git
cd AURUM-1
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run tests (265 tests)
python -m pytest -q --basetemp .pytest_tmp -p no:cacheprovider

# Run D4 paper trader once
python -m scripts.paper_trading.d4_paper_trader --run-once
```

---

## Repository Structure

```
aurum1/       Core library (data, signals, risk, execution, backtesting)
scripts/      Executables (paper trader, research tools, shadows)
monitor/      Dashboard, metrics, trade quality, prop firm sim, watchdog
deploy/       Systemd service definitions
docs/         Documentation
tests/        265 tests
archive/      Dead code preserved for reference
```

---

## Read More

| Document | What's Inside |
|----------|--------------|
| [JOURNEY.md](docs/JOURNEY.md) | The full story — why I started, what I learned, the bugs, the mistakes |
| [STATUS.md](docs/STATUS.md) | Current operational state |
| [TRUTH_MAP.md](docs/system/TRUTH_MAP.md) | Forensic map of the entire system |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design and component interaction |
| [AUDIT_ROADMAP.md](docs/system/AUDIT_ROADMAP.md) | What's next for audit readiness |

---

*Started July 2026. Still learning. Still building.*
