# Building AURUM: From Curious Trader to Quantitative Researcher

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

That was my introduction to algorithmic trading.

At first, I thought it would be easy. Learn Python. Write some trading rules. Connect them to a broker. Done.

I couldn't have been more wrong.

---

### Chapter One — Building Things That Didn't Work

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

### Chapter Two — Discovering Quantitative Research

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

D4 won because it's the simplest configuration. The 2R exit compensates for a ~37% win rate. Adding SELL direction added +$25,522 over 11 years compared to BUY-only. No filters meant it never missed a good trade to avoid a bad one.

D6 matched D4's profit factor (1.14) but with ML dependencies that would break in production. D4 needed nothing but price data.

**Lesson:** When the simplest strategy beats every filtered variant over 11 years, stop adding filters.

---

### Chapter Three — The Bugs, Mistakes and Hard Lessons

There were days where I would spend four hours reading academic papers and understand maybe 30% of what I was reading. There were times where I opened a research paper and realised I needed to learn statistics before I could even understand the introduction. Other times I'd spend an entire weekend chasing a bug that ended up being a missing line of code.

#### Bug 1: Trades Not Persisting to DB

For the first week, trades were executing in-memory but **never saved to the SQLite database**. The `_persist_trade()` method existed but wasn't being called consistently. I only noticed when the dashboard showed 0 trades.

If the server had restarted during those first 7 days, all trade history and equity tracking would have been lost.

#### Bug 2: Slippage Was Favorable (Overstating Returns)

The slippage model used a Gaussian distribution centered at zero. For market orders at breakout levels, this allowed "favorable slippage" — price improvement that doesn't happen in reality. A market buy at breakout always hits the ask, never the bid.

Backtests and paper trading were overstating returns by a small but systematic margin.

#### Bug 3: Kelly Double-Cap Sizing Positions to Zero

The Kelly calculator had two caps applied in sequence. The result was near-zero position sizes despite the strategy showing positive edge. If I'd switched to Kelly-based sizing, positions would have been microscopic regardless of edge.

#### Bug 4: Sl/TP Not Rebased Around Fill Price

When entry slippage shifted the fill price, the SL and TP distances were calculated from the intended entry price, not the actual fill. This meant actual R-multiple differed from intended 2R.

---

### Chapter Four — Learning to Trust the Data

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

**The hardest lesson wasn't learning statistics. It was learning patience.**

27 trades feels like forever when you're watching them happen live. The market doesn't care about your timeline.

---

### Chapter Five — What Building This Taught Me

Some things I learned were technical:

- **Trusting ML too early.** I spent weeks building a machine learning pipeline that added virtually nothing over 11 years. The RegimeClassifier, DirectionPredictor, and SentimentScorer were elegant solutions to a problem that didn't exist.
- **Overengineering the orchestrator.** The state machine with its four-phase entry system was beautiful. It also missed most breakouts.
- **Ignoring test coverage.** For weeks the system ran with zero tests on critical paths. Every bug I found would have been caught by a single test.
- **Dead code lies.** The repo was full of archived experiments and orphaned modules. The repo wasn't telling the truth about what ran.
- **API keys in git history.** The .env file was committed in the initial push. It took a full git-filter-repo scrub, key rotation, and a pre-commit hook to fix. This should never have happened.

Other things were harder to learn:

- **Drawing conclusions from 10 trades.** I almost abandoned D4 during the first week because 14 trades showed +$47 and I panicked. The strategy wasn't broken — my patience was.
- **Wanting complexity to be valuable.** I wanted the ML to work. I wanted the orchestrator to be the right answer. Letting go of code I'd invested weeks in was harder than writing it.
- **Assuming more features meant more edge.** Every filter I tested removed good trades alongside bad ones. D4's edge isn't its complexity — it's the asymmetric 2R payout that turns a 37% win rate into profit.

**The biggest lesson wasn't about trading. It was learning how often I was wrong.**

---

### Chapter Six — The Hardening Sprint

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

### Chapter Seven — What AURUM Has Become

Somewhere along the way, AURUM stopped being a trading bot.

It became my laboratory.

Today, AURUM is the place where I experiment with ideas, validate assumptions, kill bad strategies, document mistakes, and learn how quantitative systems are actually built. Every failed strategy teaches me something. Every bug teaches me something. Every research paper sends me down another rabbit hole that I didn't know existed the week before.

Ironically, I care less about finding the "perfect strategy" than I did when I started.

The goal now is much bigger than making money from trading.

I want to become someone capable of building systems that can discover truth from data. Trading just happens to be the problem that forced me to learn how.

AURUM isn't the result of becoming an expert.

It's the process of becoming one.

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

The most surprising discovery wasn't finding D4.

It was discovering how often my assumptions were wrong.

I built machine learning models that couldn't outperform price. I built an elegant state machine that traded too little. I built filters that removed more edge than they added. I committed API keys to git history. I let 7 days of trades live only in memory. I almost abandoned a profitable strategy because 14 trades scared me.

Again and again, the data forced me to abandon ideas I was attached to.

That may be AURUM's greatest contribution. The goal was never to prove myself right. The goal was to discover when I was wrong — and to build a system that could do that honestly.

D4 may not be the strategy I trade five years from now. There may not even be a D4. What I hope survives is the process that found it.

If AURUM succeeds, it won't be because I built a profitable strategy.

It will be because I built a system that can honestly discover one.

---

*Started July 2026. Still learning. Still building.*
