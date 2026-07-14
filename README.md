# AURUM-1 🏆

**Autonomous algorithmic trading system for XAU/USD (Gold) on M15** — live paper trading on a cloud server with a proven Donchian breakout strategy. Up **+4.49% ($449)** in the first 8 days of live trading.

> **Current status**: ✅ **D4 Paper Trader live** — executing autonomous Donchian 2R BUY+SELL trades on XAUUSD since Jul 2, 2026. 20 trades closed, 55% win rate, net PnL **+$294.05**, peak equity gain **+$449**.
> 
> 🖥️ **Live Dashboard**: [`http://178.105.245.66:8501`](http://178.105.245.66:8501)
> 
> 📊 [View Full Status →](docs/STATUS.md)

---

## 🎯 The Strategy — D4 Donchian Breakout

The simplest strategy wins. **D4** is pure price action:

1. **Entry**: Buy when price breaks above the 20-bar high. Sell when it breaks below the 20-bar low.
2. **Exit**: Fixed 2R — take profit at +2× risk, stop loss at -1× risk.
3. **No filters**: No volatility filters, no session filters, no ML. Just clean breakouts.
4. **Both directions**: BUY and SELL signals.

| Metric | Walk-Forward (18 windows) | Live Trading (8 days) |
|--------|:------------------------:|:---------------------:|
| **Profit Factor** | 1.14 | — |
| **Mean Sharpe** | 1.27 | — |
| **Positive windows** | 88.9% (16/18) | — |
| **Live Win Rate** | — | **55.0%** (11/20) |
| **Live Net PnL** | — | **+$294.05** |
| **Live Avg R** | — | **+0.61R** |
| **Peak Equity** | — | **+$449.15** (+4.49%) |

*Walk-forward: 11 years of M15 data, sliding 2yr train / 6mo test windows*

---

## 📈 Live Performance

```
Jul 02 — $10,000 ──► Start
Jul 03 — $10,150 ──► +1.5%  (3 trades)
Jul 07 — $10,214 ──► +2.1%  (first TP hit, DB recording confirmed working)
Jul 08 — $10,384 ──► +3.8%  (SELL streak: 4 consecutive wins)
Jul 09 — $10,400 ──► +4.0%  (mixed BUY/SELL, 11 trades total)
Jul 10 — $10,350 ──► +3.5%  (drawdown: 2 stop losses)
Jul 13 — $10,420 ──► +4.2%  (recovery: 3 SELL wins)
Jul 14 — $10,449 ──► +4.5%  🏆 peak (20 trades, 55% WR)
```

[**Live Dashboard →**](http://178.105.245.66:8501)

---

## 🔧 Quick Start

```bash
# Clone and enter
git clone git@github.com:Naxisbeast/AURUM-1.git
cd AURUM-1

# Set up Python 3.12
python3.12 -m venv .venv
source .venv/bin/activate  # Linux/Mac
# or: .venv\Scripts\Activate.ps1  # Windows

# Install
pip install -r requirements.txt

# Run tests
python -m pytest -q --basetemp .pytest_tmp -p no:cacheprovider
```

---

## 🏛️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      AURUM-1 System                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │           Market Data Pipeline                        │   │
│  │  OANDA API → Forward Shadow (continuous) → SQLite   │   │
│  │                     ↓                                 │   │
│  │           D4 Paper Trader (autonomous)               │   │
│  │  Donchian 20 → 2R exit → PaperBroker → SQLite DB    │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ D1-D6 Shadow │  │ ML Retrain  │  │ Streamlit        │  │
│  │ Timers (15m) │  │ (Sat weekly)│  │ Dashboard (8501) │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Validation Layer                                    │   │
│  │  Walk-Forward │ MC Simulation │ TC Stress │ ICIR    │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Key design decisions:**
- **D4 is the best** — simplest configuration (Donchian 20, 2R, no filters) dominates over 11 years
- **SELL signals essential** — add +$25,522 vs BUY-only over 11 years
- **No ML needed** — D6 (ML ensemble) produces identical results to D4
- **PaperBroker first** — real broker disabled by safety interlocks

---

## 📋 All Strategy Variants

| Rank | Variant | Entry | Exit | Direction | Trades | PF | Status |
|:----:|---------|------|:---:|:---------:|:-----:|:--:|--------|
| **1** | **D4 🏆** | Donchian 20 | 2R | BUY+SELL | 8,175 | **1.14** | **✅ Paper trading live** |
| 2 | D6 | Donchian 20 + ML | 2R | BUY+SELL | 8,169 | 1.14 | 🟡 Shadow timer |
| 3 | Raw | Donchian 20 | 2R | BUY only | 4,879 | 1.14 | 🔴 Running |
| 4 | D2 | Donchian 20 | 1R | BUY only (filtered) | 6,890 | 1.03 | 🟡 Shadow timer |
| 5 | D3 | Donchian 20 | 1R | BUY+SELL (filtered) | 3,544 | 1.02 | 🟡 Shadow timer |

See [docs/STRATEGIES.md](docs/STRATEGIES.md) for full detail.

---

## 🧪 Validation Results

### Walk-Forward (18 windows over 11 years)
| Metric | D4 (L20) | D4 (L55) |
|--------|:--------:|:--------:|
| Mean Sharpe | **1.11** | 0.61 |
| Mean PF | **1.12** | 1.09 |
| Positive windows | **82%** | 73% |
| Mean MaxDD | 5.6% | **4.8%** |

### TC Stress Test (D4 baseline: 1.5p spread / 0.5p slippage)
| Scenario | Sharpe | vs Baseline |
|----------|:------:|:-----------:|
| Baseline | **1.11** | — |
| Wide spread (2.5p) | 1.05 | -5.9% |
| Stress: 4p + 1p slip | 0.92 | -17% |
| **Max stress: 6p + 2p slip** | **0.75** | **-33%** ✅ Still profitable |

### ICIR Signal Quality
- **IC = -0.076** (p<0.001) — statistically significant but weak short-term predictive power
- Profit comes from **asymmetric 2R payoff**, not signal timing
- Decay: signal fades gracefully over 5 hours, no reversal (no overfitting)

### Risk Sensitivity (Monte Carlo — 10k sims)
| Risk/Trade | Med DD | 99th DD | Ruin |
|:----------:|:------:|:-------:|:----:|
| **0.25%** ⬅️ | **11.9%** | **20.3%** | **0%** |

---

## 🚀 Deployed Cloud Services

| Service | Function | Schedule |
|---------|----------|----------|
| `aurum1-d4-paper.service` | **🏆 D4 autonomous paper trader** | Continuous |
| `aurum1-forward-shadow.service` | Market data cache (OANDA → SQLite) | Continuous |
| `aurum1-dashboard.service` | **Streamlit live dashboard** (port 8501) | Continuous |
| `aurum1-d1-shadow.timer` | D1 filtered 1R journal | Every 15 min |
| `aurum1-d2-shadow.timer` | D2 comparison | Every 15 min |
| `aurum1-d3-shadow.timer` | D3 SELL test | Every 15 min |
| `aurum1-d4-shadow.timer` | D4 best variant comparison | Every 15 min |
| `aurum1-d6-shadow.timer` | D6 ML variant comparison | Every 15 min |
| `aurum1-ml-retrain.timer` | ML model retraining | Weekly (Sat) |

**Server**: Ubuntu 24.04, 3.7GB RAM, 38GB disk (53% used), Python 3.12

---

## 📁 Project Structure

```
aurum1/             # Core package (data, signals, risk, execution, models, backtesting)
scripts/            # Run scripts, research tools, paper trader
monitor/            # Dashboard (Streamlit) + metrics
deploy/             # Systemd service definitions + logrotate
docs/               # Documentation
tests/              # Test suite (pytest)
reports/            # Generated research reports (gitignored)
```

Full structure in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## 🛡️ Safety Interlocks

Live trading is shielded behind multiple gates:
1. **ALLOW_OANDA_ORDERS** — must be `true` to send real orders
2. **ALLOW_LIVE_TRADING** — must be `true` for live capital
3. **OANDA_ENV** — locked to `practice`
4. **Risk manager** — daily loss kill, drawdown kill, spread filters

```bash
# Safe defaults (never change for production)
ALLOW_OANDA_ORDERS=false
ALLOW_LIVE_TRADING=false
OANDA_ENV=practice
```

---

## 📖 Documentation

| Doc | What You'll Find |
|-----|------------------|
| [STATUS.md](docs/STATUS.md) | Live operational state, equity, trade log |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Full system design, component interaction |
| [STRATEGIES.md](docs/STRATEGIES.md) | All strategy variants with performance |
| [RESEARCH.md](docs/RESEARCH.md) | Research phases S1-S5, findings |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Server setup, systemd, monitoring |
| [DATA_FLOW.md](docs/DATA_FLOW.md) | End-to-end data pipeline |

---

## 🏁 License

Private — AURUM-1 Trading System. All rights reserved.
