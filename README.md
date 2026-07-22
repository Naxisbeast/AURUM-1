# AURUM-1

**My public quantitative research laboratory.**

I'm a Computer Science and Electronics student building an autonomous algorithmic trading research platform. This repository documents the system, the strategies, the failed experiments, and what I'm learning along the way.

The goal isn't to find a profitable strategy. The goal is to build a system capable of honestly discovering one.

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
- How often my assumptions are wrong

---

## Where It Stands

D4 — Donchian 20-bar breakout, 2R exit, BUY+SELL — is currently the strongest candidate discovered through research. It's paper trading autonomously on a cloud server at 0.35% risk per trade.

**Evidence so far:**

| Test | Result |
|------|--------|
| Walk-forward (18 windows, 11 years) | 88.9% positive windows |
| Monte Carlo (10,000 simulations) | 0% ruin probability |
| TC stress (6p spread + 2p slippage) | Still profitable (PF 1.09) |
| Live paper trades | 29 trades |
| Signal stationarity (ADF test) | ✅ Stationary — not trading noise |

[Full status →](docs/STATUS.md)
[Live dashboard →](https://wear-boot-jennifer-brush.trycloudflare.com)
[The full story →](docs/JOURNEY.md)

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

---

## Architecture

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
- Kill switches run in-process; a separate watchdog service monitors independently
- All trades, snapshots, and missed signals persist to SQLite — survives restart

[**Live Dashboard →**](https://wear-boot-jennifer-brush.trycloudflare.com/)

---

## What Runs on the Server

| Service | What It Does |
|---------|-------------|
| `aurum1-d4-paper.service` | D4 paper trader (autonomous, continuous) |
| `aurum1-forward-shadow.service` | Market data pipeline (OANDA → cache) |
| `aurum1-dashboard.service` | Streamlit live dashboard |
| `aurum1-watchdog.service` | Independent kill switch monitor |
| `aurum1-tunnel.service` | Cloudflare tunnel |

---

## Quick Start

```bash
git clone git@github.com:Naxisbeast/AURUM-1.git
cd AURUM-1
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run tests
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
