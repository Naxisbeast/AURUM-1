# AI Trading Co-Pilot: Concept & Feasibility

## The Vision

An AI agent that understands AURUM-1's strategy deeply, monitors market conditions in real-time, and makes intelligent decisions about execution — sizing, exits, regime detection — without being a black box.

This is NOT a model predicting prices. It's a **reasoning system** that applies strategy rules with contextual awareness.

## How It Would Work

### Architecture

```
Market Data (M15 candles) 
        │
        ▼
┌─────────────────────┐
│  1. Signal Detector  │  ← Donchian breakout rules (deterministic)
│   (10-bar Donchian)  │     Same as current, no ML
└─────────┬───────────┘
          │ raw signal
          ▼
┌─────────────────────┐
│  2. Context Builder  │  ← Gathers market state into structured context
│                      │     ATR percentile, ADX, session, recent trades,
│                      │     position status, equity curve state
└─────────┬───────────┘
          │ structured context
          ▼
┌─────────────────────┐
│  3. AI Reasoning     │  ← LLM evaluates context against strategy rules
│   (Claude/GPT)       │     "ADX is 15 — ranging market. Reduce size to 50%"
│                      │     "Price hit 1R — move stop to breakeven"
│                      │     "3 consecutive wins — hot streak confirmed"
└─────────┬───────────┘
          │ decision
          ▼
┌─────────────────────┐
│  4. Risk Manager     │  ← Validates AI decision against hard limits
│   (Safety Layer)     │     Never exceed max_dd, max_position_size
└─────────┬───────────┘
          │ approved order
          ▼
┌─────────────────────┐
│  5. Execution        │  ← PaperBroker / OANDA
└─────────────────────┘
```

### What the AI Would Decide

| Decision | Current D4 | AI Co-Pilot |
|----------|-----------|-------------|
| **Position sizing** | Fixed 0.25% | Adjusts 0.1%-0.5% based on ADX, volatility, streak |
| **Stop placement** | Fixed 2× ATR | 1.5× (tight trend) to 3× (high vol) based on context |
| **Take-profit** | Fixed 2R | Partial at 1R if ADX dropping, full 2R if ADX rising |
| **Breakeven** | Never | Moves to BE when price reaches 0.75R |
| **Trade selection** | All breakouts | Skips low-confidence setups (high ATR, low ADX, against H1 trend) |
| **Regime detection** | None | Identifies trending/ranging/transitioning and adjusts strategy |

### The Prompt Structure

The AI would receive a structured prompt every candle:

```
You are AURUM-1's strategy manager. You manage a 10-bar Donchian breakout
system on XAU/USD M15. Rules:
- Entry: price breaks above/below 10-bar Donchian channel
- Exit: 2R fixed, or breakeven, or trail depending on context
- Base risk: 0.25% per trade

Current state:
- Position: LONG @ $2,350.50, +0.8R, open for 12 bars
- ATR(14): $0.85 (35th percentile — low vol)
- ADX(14): 22 (moderate trend)
- Today: London session, Wednesday
- Recent trades: W, L, W, W, L (last 5)
- Equity: $10,450 (peak $10,500)
- H1 trend: BULLISH
- COT positioning: Net long 15%

Decide: Should we: (a) hold with current stop (b) move stop to breakeven
(c) close 50% and trail (d) close fully

Your decision must include: action, price level, reasoning.
```

### Feasibility

**Is this possible today?** Yes, with some caveats:

| Aspect | Possible? | Challenge |
|--------|-----------|-----------|
| Reading market data | ✅ Yes | Simple — candles → text |
| Reasoning about context | ✅ Yes | Claude/GPT are good at this |
| Making size decisions | ✅ Yes | Quantitative reasoning works |
| Executing trades | ✅ Yes | → API call |
| **Latency** | ⚠️ 2-5 seconds per inference | Fine for M15 (15 min candles) |
| **Cost** | ⚠️ ~$0.01-0.05 per decision | ~$300-500/month for M15 |
| **Hallucination** | ⚠️ Risk of bad decisions | Safety layer (hard limits) required |
| **Consistency** | ⚠️ May vary day to day | Prompt engineering + temperature=0 |

### Risk Mitigation

The AI wouldn't be a "brain" — it's a **recommendation system** with hard guards:

```python
def safety_check(ai_decision: dict, market_state: dict) -> bool:
    """Hard limits the AI cannot override."""
    if ai_decision['size'] > 1.0:           # Max 1% risk
        return False
    if ai_decision['stop'] < -3 * atr:      # Max 3x ATR stop
        return False
    if market_state['daily_drawdown'] > 0.08:  # DD kill switch
        return False
    if ai_decision['action'] not in ALLOWED_ACTIONS:
        return False
    return True
```

### What You'd Need To Build

1. **Context builder** — converts candles + positions → structured text (~100 lines)
2. **Prompt template** — the system prompt for the AI (~50 lines)
3. **Decision parser** — extracts structured actions from AI output (~50 lines)
4. **Safety layer** — hard limits the AI cannot override (~50 lines)
5. **API integration** — calls Claude API → executes via broker (~30 lines)

The strategy rules stay in your code. The AI just makes parameter decisions. This is **much smarter than an ML model** because it can reason about context the way a human trader would — but faster and without emotion.

---

## TL;DR

| Question | Answer |
|----------|--------|
| Is it possible? | ✅ **Yes, absolutely** |
| Would it work? | **Likely** — the reasoning capability is there |
| Would it be better than rules? | **Probably** — can adapt to context in ways rules can't |
| Is it ready to build? | **Yes** — clean architecture, clear boundaries |
| Biggest risk | Hallucination — but safety layer mitigates this |
