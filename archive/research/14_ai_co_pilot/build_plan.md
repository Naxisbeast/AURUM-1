# AI Trading Co-Pilot — Build Plan

## What We're Building

An LLM-powered reasoning layer that sits between AURUM-1's signal detection and execution. It reads market context, evaluates positions, and makes intelligent decisions about sizing, exits, and trade selection — without replacing the core Donchian strategy.

## Architecture

```
┌──────────────────────────────────────────────┐
│           AURUM-1 Signal Layer                │
│  (Donchian breakout detection, existing)      │
└──────────────────┬───────────────────────────┘
                   │ raw signal + market data
                   ▼
┌──────────────────────────────────────────────┐
│         ✅ Phase 1: Context Builder           │  ← THIS BUILD
│  Builds structured context from market state  │
│  Converts candles, positions, metrics → text  │
└──────────────────┬───────────────────────────┘
                   │ structured prompt
                   ▼
┌──────────────────────────────────────────────┐
│         ✅ Phase 2: Claude Agent              │  ← THIS BUILD
│  Makes decisions: size, stop, TP, BE, trail   │
│  Returns structured JSON decision             │
└──────────────────┬───────────────────────────┘
                   │ decision JSON
                   ▼
┌──────────────────────────────────────────────┐
│         ✅ Phase 3: Safety Layer              │  ← THIS BUILD
│  Hard limits the AI cannot override           │
│  Validates decision against risk rules        │
└──────────────────┬───────────────────────────┘
                   │ approved order
                   ▼
┌──────────────────────────────────────────────┐
│         Existing Execution Layer              │
│  (PaperBroker / OANDA)                        │
└──────────────────────────────────────────────┘
```

## Build Phases

### Phase 1: Context Builder (~100 lines)
- Reads current candle data, position state, recent trade history
- Builds structured JSON context for the AI
- Computes: ATR percentile, ADX, session, equity curve stats, recent WR
- Output: a dict ready to be stringified into the AI prompt

### Phase 2: Claude Agent (~100 lines)
- System prompt defining AURUM-1 strategy rules
- User message with current context
- Claude responds with structured decision
- Decision format: `{action, reason, size_mult, stop_adjustment, tp_adjustment}`

### Phase 3: Safety Layer (~50 lines)
- Hard limits: max risk 1%, max stop 3× ATR, max drawdown 8%
- Validates AI output before execution
- Logs all decisions for audit

### Phase 4: Historical Backtest (~200 lines)
- Run the AI co-pilot over historical 10-bar backtest data
- Compare its decisions vs fixed 2R rules
- Measure: did the AI improve PF, DD, or WR?
- This is the critical validation step

## Decision Types the AI Will Make

| Decision | Options | Current Behavior |
|----------|---------|-----------------|
| **Take trade?** | Yes / No / Half-size | Always yes |
| **Position sizing** | 0.25% base ± adjustment | Fixed 0.25% |
| **Stop placement** | 1.5× to 3.0× ATR | Fixed 2× |
| **Breakeven** | At 0.5R / 0.75R / 1.0R / Never | Never |
| **Partial close** | At 0.5R / 1.0R / 1.5R / Never | Never |
| **Trailing stop** | 2.0× to 4.0× ATR / Off | Off |

## Files to Create

| File | Purpose |
|------|---------|
| `aurum1/ai_co_pilot/__init__.py` | Package init |
| `aurum1/ai_co_pilot/context.py` | Context builder |
| `aurum1/ai_co_pilot/agent.py` | Claude integration |
| `aurum1/ai_co_pilot/safety.py` | Safety validation |
| `experiments/run_ai_backtest.py` | Historical validation |

## Success Criteria

The AI must improve at least 2 of these vs the fixed 2R baseline:
- ✅ Profit Factor (baseline: 1.204)
- ✅ Max Drawdown (baseline: ~15%)
- ✅ Win Rate (baseline: 37.9%)
- ✅ Avg R (baseline: 0.128)

If it matches or beats the baseline, it's worth deploying.
If it's worse, we know LLMs aren't suitable for this yet.
