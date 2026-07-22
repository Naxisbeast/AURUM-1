# Building AURUM: From Curious Trader to Quantitative Researcher

**Last updated**: 2026-07-22

---

### Chapter Zero — Falling Down the Rabbit Hole

I had absolutely no idea what quantitative research was when I started.

I thought I was building a trading bot.

If you told me when I first started watching trading videos on YouTube that I'd eventually be reading academic papers on walk-forward validation and arguing with myself over whether a slippage model was realistic, I would have laughed.

One thing I find funny looking back is that I had absolutely no intention of learning half of the things I've learned while building this project. I accidentally signed myself up for learning:

- Statistics
- Quantitative finance
- Software architecture
- Market microstructure
- Cloud infrastructure
- Monte Carlo simulations
- Machine learning
- Research methodologies
- Data engineering
- Risk management
- Systems design

Somewhere along the way, AURUM stopped being a trading bot. It became my laboratory.

---

### Chapter One — Why I Started

If I'm being completely honest, AURUM didn't start because I wanted to become a quantitative researcher or build an algorithmic trading platform.

It started because I wanted to make money.

Like a lot of South African guys, I became fascinated with trading. You see people online talking about funded accounts, financial freedom, and making money from charts. Then you watch movies like *The Wolf of Wall Street* and wonder how people can sit in front of screens all day making decisions worth millions of dollars.

The question that kept bothering me wasn't whether people could make money trading. It was *how*.

How do they know their strategy actually works?

How do they know when they're wrong?

Surely they aren't just drawing lines on charts and hoping for the best.

So I started learning to trade. Sometimes I made money. Most of the time I made mistakes. I'd enter trades too early, close winners too quickly, hold losers too long, or convince myself that I had discovered some incredible strategy after three winning trades.

Eventually I realised something that changed the direction of this entire project.

**The biggest weakness in my trading wasn't my strategy. It was me.**

I have emotions. I get impatient. I become overconfident after winning and doubtful after losing. Computers don't do that.

That led me down a rabbit hole of questions:

- Can trading decisions be turned into code?
- Can strategies be tested honestly instead of emotionally?
- How do banks and hedge funds approach this problem?
- Can one person build a system capable of researching and validating ideas objectively?

At first, I thought it would be easy. Learn Python. Write some trading rules. Connect them to a broker. Done.

I couldn't have been more wrong.

The deeper I went, the more I realised I wasn't building a smart system. I was building a robot that blindly followed instructions. Then I discovered statistics, risk management, quantitative research, machine learning, market microstructure, Monte Carlo simulations, walk-forward testing, and software engineering problems that were far more interesting than trading itself.

---

### Chapter Two — Building Things That Didn't Work

I have a terrible habit as an engineer. Whenever I learn something new, I immediately want to implement it.

Discover machine learning? Let's build an ensemble model.

Learn about market regimes? Let's classify them.

Read about state machines? Let's design the most beautiful trading pipeline imaginable.

By May 2026, I had built what I genuinely believed was an intelligent trading system. It had an orchestrator, multiple ML models, feature engineering pipelines, and more buzzwords than I care to admit.

It was elegant.

It was complicated.

And it barely traded.

After several weeks of watching this thing produce three trades a week, I realised I had fallen into a trap that I still fight today: **confusing complexity with intelligence.**

The market couldn't care less about how clever my architecture was.

---

### Chapter Three — Discovering Quantitative Research

One of the biggest lessons AURUM has taught me is that the market is surprisingly good at humbling your ego.

I wanted machine learning to win.

I wanted my sophisticated models to outperform the boring breakout strategy that anyone could understand in five minutes.

Instead, after running years of historical data and systematically comparing multiple variants, the winner was the simplest strategy in the entire experiment.

**D4** had no fancy filters. No machine learning models. No sentiment analysis. No market regime classifier.

It simply listened to price.

That was a painful but valuable lesson. I realised something that changed how I approach this project:

**Complexity doesn't deserve a seat at the table unless it can justify its existence.**

| Variant | Exit | Directions | Filters | 11-Year PF | What I Learned |
|---------|------|-----------|---------|-----------|----------------|
| D1 | 1R | BUY only | Vol + Session | Weak | Too many filters kill edge |
| D2 | 1R | BUY only | Vol + Session | 1.03 | Marginal at best |
| D3 | 1R | BUY+SELL | Vol + Session | 1.02 | Filters broke the SELL edge |
| **D4** 🏆 | **2R** | **BUY+SELL** | **None** | **1.14** | **Simplest wins** |
| D6 | 2R | BUY+SELL | ML ensemble | 1.14 | ML added nothing |

I have probably spent more time deleting code than writing it. D4 won because it's the simplest configuration. The 2R exit compensates for a ~37% win rate. Adding SELL direction added +$25,522 over 11 years compared to BUY-only. No filters meant it never missed a good trade to avoid a bad one.

**Lesson:** When the simplest strategy beats every filtered variant over 11 years, stop adding filters.

---

### Chapter Four — Building With AI

One thing I'm not interested in pretending is that this is a "100% hand-coded genius solo project."

It's not.

I build with AI every single day.

AI has helped me learn concepts I didn't understand, review architectures, debug systems, explain academic papers, and iterate on ideas faster than I ever could have on my own. Large sections of the broker, the backtesting engine, the dashboard, the test suite, and the hardening cleanup were built in collaboration with an AI.

**But here's what AI didn't do:**

- AI didn't decide to build AURUM
- AI didn't choose D4 as the strategy
- AI didn't design the architecture — the decoupled data pipeline, the PaperBroker pattern, the single-instance lock — those were mine
- AI didn't interpret the backtest results or decide when to bump risk
- AI didn't catch the bugs or decide which fixes mattered most
- AI didn't sit through 29 trades wondering if the whole thing was broken
- AI didn't learn the hard way that simplicity beats complexity

AI is probably the most powerful learning tool I've ever had access to. Building AURUM without using it would feel like voluntarily refusing to use Google twenty years ago. But it doesn't replace the judgment, the domain knowledge, or the patience that building a system like this requires.

If you're also building with AI assistance, stop feeling guilty about it. The tool doesn't write the vision. You do.

---

### Chapter Five — The Bugs That Nearly Fooled Me

One of my favourite bugs was discovering that my system was making money partly because I had accidentally allowed favourable slippage in situations where it shouldn't exist.

For about five minutes I was very happy.

Then I realised I had essentially been lying to myself with statistics.

#### The Slippage Bug

The slippage model used a Gaussian distribution centered at zero. For market orders at breakout levels, this allowed "favorable slippage" — price improvement that doesn't happen in reality. A market buy at breakout always hits the ask, never the bid. Backtests were overstating returns by a small but systematic margin.

**Fix:** Changed to folded-normal (absolute of Gaussian) — slippage is always adverse for market orders.

#### The Trade That Never Saved

For the first week, trades were executing in-memory but never saved to the SQLite database. The `_persist_trade()` method existed but wasn't being called consistently. I only noticed when the dashboard showed 0 trades.

If the server had restarted during those first 7 days, all trade history and equity tracking would have been lost.

#### The Kelly Double-Cap

The Kelly calculator had two caps applied in sequence. The result was near-zero position sizes despite the strategy showing positive edge. If I'd switched to Kelly-based sizing, positions would have been microscopic regardless of edge.

#### The SL/TP That Didn't Match

When entry slippage shifted the fill price, the SL and TP distances were calculated from the intended entry price, not the actual fill. This meant actual R-multiple differed from intended 2R.

One thing university doesn't prepare you for is spending eight hours chasing a bug that ends up being a single missing line of code.

---

### Chapter Six — Learning to Trust the Data

One of the hardest parts wasn't building the system.

It was trusting it.

The first 10 trades were rocky:
- July 7: 4 trades, 3 wins, +$143 — feeling good
- July 8: 4 trades, 2 wins, +$36 — okay
- July 9-10: 4 trades, 1 win, -$96 — doubt creeping in
- July 12: SL gap loss (-1.74R, -$42) — worst trade yet

At this point I had 14 trades, net +$47. I started questioning whether the 11-year backtest was lying to me.

Then the market shifted. Five straight SELL wins over two days:

- Jul 13: SELL @ $4,069 → TP @ $4,027 (+$42)
- Jul 13: SELL @ $4,036 → TP @ $3,991 (+$45)
- Jul 14: BUY @ $4,002 → TP @ $4,037 (+$35)
- Jul 14: BUY @ $4,030 → TP @ $4,077 (+$46)

Equity went from $10,350 → $10,449. The backtest wasn't lying — D4 needed about 15 trades before the distribution started matching expectations.

I'd check the dashboard multiple times a day. I'd refresh the equity curve hoping it changed. I'd catch myself thinking "maybe if I add just one filter..." — the exact impulse that the whole research process was designed to resist.

I've realised that quantitative research is probably one of the most humbling things I've ever tried learning because it forces you to admit when you're wrong statistically rather than emotionally.

**The hardest lesson wasn't learning statistics. It was learning patience.**

---

### Chapter Seven — The Hardening Sprint

After D4 accumulated 27 trades, I paused new development to harden the system. The goal was to stabilize before bumping risk.

**Phase 0: The Truth Map**

I did a forensic scan of the entire repository. What I found was ugly:

- 6 broken imports from a script reorganization
- No package init files in any scripts subdirectory
- Dead code everywhere — experiment scripts, ML orchestrator, research directories
- Silent error handlers in critical paths
- 9 stale service templates pointing to wrong script paths
- 8 backtesting scripts with broken path resolution
- Dashboard had 0 tests

**Phase 1: Stabilization**

Everything got fixed. Imports corrected. Dead code archived. Silent errors logged. Service templates updated. Path resolutions fixed across the entire repo.

**Phase 2: Validation**

Re-ran every validation to confirm nothing broke:
- Walk-forward L20: 88.9% positive windows — same as before
- Monte Carlo: 0% ruin across 10,000 simulations — same as before
- TC Stress Test: Survives 6p spread + 2p slippage — same as before

**Phase 3: Analytics**

Built the analysis layer that should have existed from day one:
- Trade quality scoring with MAE/MFE analysis
- Prop firm challenge simulator (FTMO, The5ers, FundingPips)
- System health dashboard with latency and slippage tracking
- Experiment framework template

**Phase 4: Evidence Collection**

Risk bumped from 0.25% to 0.35% — the first deliberate risk increase, driven by data rather than gut feel. D4 runs untouched while I wait for 50 trades (risk review gate) and 100 trades (strategy review gate).

---

### Chapter Eight — What AURUM Has Become

Today AURUM is:

- An autonomous paper trading system running on a cloud server
- A quantitative research platform with 265 tests
- A software engineering project that I've rebuilt more times than I can count
- A personal learning laboratory
- A public journal of my growth as an engineer and researcher

The strategies are temporary. The research process is the product.

If D4 dies tomorrow and D8 replaces it, AURUM still succeeds if the process that found D4 survives. AURUM is an experiment in building systems that make decisions from evidence rather than intuition.

The goal isn't to find a profitable strategy. The goal is to build a system capable of honestly discovering one.

---

### What Comes Next

D4 isn't finished. Over the coming months:

- Reach 50 trades for the first risk review gate
- Reach 100 trades for the strategy review gate
- Expand the analytics pipeline
- Improve execution monitoring
- Stress test larger risk profiles
- Determine whether D4 deserves real capital

Eventually D4 may die. If it does, that's acceptable — it means I found something better, or the data told me the edge wasn't real.

The purpose of AURUM isn't to prove D4 works. It's to discover what actually does.

---

### Final Thought

Sometimes people ask me whether I think AURUM will eventually become profitable.

The truth is I don't know.

And that's probably my favourite thing about this project.

Five years from now, D4 might be dead. AURUM might look completely different. I might discover that every assumption I currently have about markets is wrong.

If that happens, then AURUM has done exactly what it was designed to do.

Because the point was never to build a profitable trading system.

The point was to become the kind of engineer that knows how to discover one.

---

*Started May 2026. Still learning. Still building.*
