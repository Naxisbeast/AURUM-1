export const meta = {
  name: 'aurum1-full-audit',
  description: 'Complete AURUM-1 quantitative systems audit across data, models, risk, execution, backtesting, and deployment',
  phases: [
    { title: 'Data-Features-Backtest-Math', detail: 'Audit data pipeline, feature engineering, backtest engine, walk-forward, and metrics math' },
    { title: 'Risk-Execution-Deployment-Ops', detail: 'Audit risk manager, broker, execution engine, systemd, security, and ops readiness' },
    { title: 'ML-Models-Signals-StateMachine', detail: 'Audit models, ensemble, sentiment, retrainer, and signal state machine' },
    { title: 'Synthesize-Final-Report', detail: 'Merge all audit findings into structured final report with scores and verdict' },
  ],
}

const ROOT = 'C:\\Users\\thape\\Desktop\\Trading algorithim'

// ============================================================
// PHASE 1a: Data, Features, Backtest & Math
// ============================================================
phase('Data-Features-Backtest-Math')

const dataBacktestAudit = await agent(
  'You are a senior quantitative researcher auditing a trading system. Read ALL these files and produce a brutally honest audit covering exactly the sections below.\n\n' +
  'FILES TO READ:\n' +
  '- ' + ROOT + '/aurum1/data/ingestion.py\n' +
  '- ' + ROOT + '/aurum1/features/engineer.py\n' +
  '- ' + ROOT + '/aurum1/instruments.py\n' +
  '- ' + ROOT + '/aurum1/backtesting/engine.py\n' +
  '- ' + ROOT + '/aurum1/backtesting/walk_forward.py\n' +
  '- ' + ROOT + '/aurum1/backtesting/report.py\n' +
  '- ' + ROOT + '/aurum1/backtesting/monte_carlo.py\n' +
  '- ' + ROOT + '/scripts/forward_shadow_donchian_d4.py\n' +
  '- ' + ROOT + '/scripts/forward_shadow_donchian.py (signal gen + data flow)\n' +
  '- ' + ROOT + '/scripts/d4_paper_trader.py\n' +
  '- ' + ROOT + '/aurum1/config/settings.yaml\n\n' +
  'PRODUCE ANALYSIS FOR:\n\n' +
  'A. DATA AND FEATURES AUDIT\n' +
  '- Timestamp handling, timezone consistency, candle alignment, UTC correctness\n' +
  '- Lookahead bias: does assert_no_lookahead catch everything? Target construction in _add_target uses shift(-TARGET_HORIZON_BARS) on close, threshold compares against atr_14 built from same close - is there any leakage?\n' +
  '- NaN/Inf handling in pipeline\n' +
  '- OANDA/yfinance fallback behavior when no API key\n' +
  '- COT and macro placeholder hardcoding (cot_net_long_pct=0.0)\n' +
  '- Feature name consistency between training and inference\n' +
  '- WARMUP_BARS=200 being enough for lookbacks of 200 bars\n\n' +
  'B. BACKTEST ENGINE AUDIT (MOST IMPORTANT)\n' +
  '1. Entry timing: does engine execute on SAME candle as signal generation or NEXT? Check pending_stop vs next_open paths. Lookahead?\n' +
  '2. Feature table building: _build_causal_feature_table builds features on ENTIRE ohlcv at once then iterates. Does this leak future data into rolling stats?\n' +
  '3. SL/TP checking: correct? Gap fills handled?\n' +
  '4. Spread/slippage model: PaperBroker spread_cost = 2 * spread_pips * pip_value * units. Is 2x correct? (once for entry, once for exit)\n' +
  '5. Metric formulas: Sharpe uses daily_returns via resample(1D).last().pct_change() on intraday equity. Is sqrt(252) correct for daily resampled bars? Overnight gaps?\n' +
  '6. Drawdown: rolling_max via cummax - correct for intraday?\n' +
  '7. Fee_adjusted_equity_curve: does it subtract fees correctly?\n\n' +
  'C. WALK-FORWARD AUDIT\n' +
  '1. Window overlap: step_bars=11088, test_bars=11088. allow_overlap=false? What does this mean for 29/29 positive windows?\n' +
  '2. Gate criteria critique: mean_sharpe>0.50, mean_pf>1.30, mean_wr>0.50, mean_dd<5%, worst_dd<10%, pos_window_rate>80%. Too loose?\n' +
  '3. Model retraining inside walk-forward: data leakage from macro/COT forward-filled into test?\n' +
  '4. Random seed fixed at 42 across all walk-forward windows - problem?\n\n' +
  'D. MATH VERIFICATION\n' +
  '1. PnL: spec.pnl = delta * units * ounces_per_unit. For XAU/USD delta in USD/oz, units in ounces, ounces_per_unit=1. Verify.\n' +
  '2. Pip_value_per_unit = pip_size * ounces_per_unit = 0.01 * 1 = $0.01/pip/unit. Correct?\n' +
  '3. Position sizing: risk_amount / (sl_distance * ounces_per_unit). Verify.\n' +
  '4. Monte Carlo: resampling distribution correct?\n\n' +
  'E. FORWARD SHADOW VS BACKTEST RECONCILIATION\n' +
  '1. D4 shadow runs rules on cached data. Paper trader runs on PaperBroker. Do they agree on same data?\n' +
  '2. Paper trader entry: close > high_20 rolling, enter next bar open+slip. Shadow entry: features close > high_20.shift(1), enter next bar open+slip. Equivalent?\n' +
  '3. Shadow trades to donchian_shadow.sqlite3. Paper trader to paper_trading.sqlite3. Can these be cross-validated?\n\n' +
  'For each issue found, assign severity: CRITICAL, HIGH, MEDIUM, LOW, INFO.\n' +
  'For each issue, provide exact file:line reference.\n' +
  'End with a quantified summary of findings.',
  {
    label: 'Data/Backtest Audit',
    phase: 'Data-Features-Backtest-Math',
    schema: {
      type: 'object',
      properties: {
        findings: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              section: { type: 'string' },
              severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'] },
              file: { type: 'string' },
              line: { type: 'string' },
              title: { type: 'string' },
              detail: { type: 'string' },
              recommendation: { type: 'string' },
            },
            required: ['section', 'severity', 'file', 'title', 'detail', 'recommendation'],
          },
        },
        dataQualityScore: { type: 'integer', minimum: 1, maximum: 10 },
        backtestCredibilityScore: { type: 'integer', minimum: 1, maximum: 10 },
        mathCorrectnessScore: { type: 'integer', minimum: 1, maximum: 10 },
        passFailVerdict: { type: 'string' },
        postAuditNarrative: { type: 'string' },
      },
      required: ['findings', 'dataQualityScore', 'backtestCredibilityScore', 'mathCorrectnessScore', 'passFailVerdict', 'postAuditNarrative'],
    },
  },
)

// ============================================================
// PHASE 1b: Risk, Execution, Deployment & Ops
// ============================================================
phase('Risk-Execution-Deployment-Ops')

const riskExecDeployAudit = await agent(
  'You are a senior production engineer and risk manager auditing a trading system. Read ALL these files and produce a brutally honest audit covering exactly the sections below.\n\n' +
  'FILES TO READ:\n' +
  '- ' + ROOT + '/aurum1/risk/manager.py\n' +
  '- ' + ROOT + '/aurum1/execution/broker.py (both PaperBroker and OandaBroker)\n' +
  '- ' + ROOT + '/aurum1/execution/engine.py\n' +
  '- ' + ROOT + '/aurum1/instruments.py\n' +
  '- ' + ROOT + '/aurum1/orchestrator.py\n' +
  '- ' + ROOT + '/main.py\n' +
  '- ' + ROOT + '/aurum1/config/settings.yaml\n' +
  '- ' + ROOT + '/aurum1/data/ingestion.py (OANDA interlock checks)\n' +
  '- ' + ROOT + '/scripts/d4_paper_trader.py\n\n' +
  'Also check if .env, .gitignore, systemd files, or Docker/deploy files exist.\n\n' +
  'PRODUCE ANALYSIS FOR:\n\n' +
  'A. RISK MANAGEMENT AUDIT\n' +
  '1. Position sizing formula: risk = equity * risk_per_trade_pct * kelly_fraction. Units = risk / (sl_distance * ounces_per_unit). Verify for XAU/USD.\n' +
  '2. Kelly formula: full_kelly = win_rate - (1-win_rate)/win_loss_ratio. Check for XAU/USD where WR~37%, avg_win~$15, avg_loss~$8. What kelly fraction does this produce? Is the 0.25 cap affecting it?\n' +
  '3. Kill switches: daily_loss at -3% of equity, total_drawdown at -8% of 30d peak. Are these realistic for a buy-hold of gold?\n' +
  '4. Spread filter: max_spread_pips=3.0. Current spread is 1.5. When would this trigger for XAU/USD?\n' +
  '5. Regime conflict check: BUY+TRENDING_DOWN blocked. Is this correct for mean-reversion vs trend-following strategy?\n' +
  '6. Recovery mode: halves risk when equity < 95% of 30d peak. Is this triggered often?\n' +
  '7. AccountState fields: open_risk_pct always 0.0 in both brokers. Dead field?\n' +
  '8. Trade history for kelly: uses net_pnl from _realised_trade_pnl. Is this R-multiple or dollar? The formula expects consistent distribution.\n\n' +
  'B. EXECUTION AUDIT\n' +
  '1. PaperBroker fill realism: slippage modeled as gauss(0, 0.5pips) absolute. Does this produce realistic fills?\n' +
  '2. Spread cost: 2 * spread_pips * pip_value_per_unit * units. Is double-counting? One spread for entry, one for exit?\n' +
  '3. SL/TP rebasing: PaperBroker rebases SL/TP from intended entry to actual fill. Does this preserve original risk distance? Correct?\n' +
  '4. OandaBroker: uses LIMIT orders with stopLossOnFill/takeProfitOnFill. What happens if limit doesn\'t fill? fill_timeout handling?\n' +
  '5. ALLOW_OANDA_ORDERS and ALLOW_LIVE_TRADING interlocks: _assert_oanda_interlocks. Safe?\n' +
  '6. PaperBroker.update_prices: only processes candles passed externally. D4 paper trader doesn\'t call this - it manages SL/TP checks internally. Double risk checking? Inconsistent?\n' +
  '7. ExecutionEngine creates a temp SQLite for each backtest run. Does this accumulate?\n\n' +
  'C. DEPLOYMENT AND OPERATIONS AUDIT\n' +
  '1. systemd unit for D4 paper service: runs as aurum1 user, restart=on-failure, 30s delay. Adequate?\n' +
  '2. Logging: PYTHONUNBUFFERED=1 added. Rotating file handler? Log rotation?\n' +
  '3. Missing OANDA_API_KEY: orchestrator/AurumDataIngestor throws RuntimeError with generic message. Graceful?\n' +
  '4. SQLite single-writer: forward-shadow writes to market cache while paper trader reads it. Safe on SQLite?\n' +
  '5. Memory: paper trader uses 64MB. Forward shadow uses 188MB. Server has 1GB. OK?\n' +
  '6. paper_trading.sqlite3: schema created on init. Any risk of corruption if process killed mid-write?\n' +
  '7. Timezone: server in UTC, settings in UTC. Any local time vs UTC mismatches?\n' +
  '8. Dashboard: host=127.0.0.1 port 8501. Not publicly exposed. Good.\n\n' +
  'D. SECURITY AUDIT\n' +
  '1. API keys in environment variables, not in code. Good.\n' +
  '2. _assert_oanda_interlocks: requires ALLOW_OANDA_ORDERS=true for any OANDA access, and ALLOW_LIVE_TRADING=true for live. Safe.\n' +
  '3. Paper trader runs with ALLOW_OANDA_ORDERS=false. Cannot accidentally trade live. Good.\n' +
  '4. .gitignore: check if .env, *.sqlite3, model artifacts are ignored.\n' +
  '5. Dashboard not publicly exposed (127.0.0.1).\n\n' +
  'For each issue, assign severity: CRITICAL, HIGH, MEDIUM, LOW, INFO.\n' +
  'Provide exact file:line references.',
  {
    label: 'Risk/Execution/Deploy Audit',
    phase: 'Risk-Execution-Deployment-Ops',
    schema: {
      type: 'object',
      properties: {
        findings: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              section: { type: 'string' },
              severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'] },
              file: { type: 'string' },
              line: { type: 'string' },
              title: { type: 'string' },
              detail: { type: 'string' },
              recommendation: { type: 'string' },
            },
            required: ['section', 'severity', 'file', 'title', 'detail', 'recommendation'],
          },
        },
        riskManagementScore: { type: 'integer', minimum: 1, maximum: 10 },
        executionQualityScore: { type: 'integer', minimum: 1, maximum: 10 },
        opsReadinessScore: { type: 'integer', minimum: 1, maximum: 10 },
        securityScore: { type: 'integer', minimum: 1, maximum: 10 },
        paperTradingReadiness: { type: 'string' },
        postAuditNarrative: { type: 'string' },
      },
      required: ['findings', 'riskManagementScore', 'executionQualityScore', 'opsReadinessScore', 'securityScore', 'paperTradingReadiness', 'postAuditNarrative'],
    },
  },
)

// ============================================================
// PHASE 1c: ML Models, Signals & State Machine
// ============================================================
phase('ML-Models-Signals-StateMachine')

const mlSignalAudit = await agent(
  'You are a senior ML engineer and quant researcher auditing ML components in a trading system. Read ALL these files and produce a brutally honest audit.\n\n' +
  'FILES TO READ:\n' +
  '- ' + ROOT + '/aurum1/models/regime_classifier.py\n' +
  '- ' + ROOT + '/aurum1/models/direction_predictor.py\n' +
  '- ' + ROOT + '/aurum1/models/ensemble.py\n' +
  '- ' + ROOT + '/aurum1/models/sentiment_model.py\n' +
  '- ' + ROOT + '/aurum1/models/retrainer.py\n' +
  '- ' + ROOT + '/aurum1/models/ablation.py\n' +
  '- ' + ROOT + '/aurum1/models/utils.py\n' +
  '- ' + ROOT + '/aurum1/signals/state_machine.py\n' +
  '- ' + ROOT + '/aurum1/signals/__init__.py\n' +
  '- ' + ROOT + '/aurum1/features/engineer.py (target construction, label generation)\n' +
  '- ' + ROOT + '/scripts/train_ml_models.py\n\n' +
  'PRODUCE ANALYSIS FOR:\n\n' +
  'A. REGIME CLASSIFIER\n' +
  '1. Label generation: ADX>25 + EMA_ALIGN>=3 -> TRENDING_UP, ADX>25 + EMA_ALIGN<=-3 -> TRENDING_DOWN, else RANGING. Is this a good regime definition? What happens when ADX>25 but ema_alignment is -2 (almost trending down)? Falls to RANGING.\n' +
  '2. Feature validation: _validated_feature_names blocks label-definition columns (ADX, ema_alignment) from being model inputs. Good. But is this sufficient?\n' +
  '3. Training: LightGBM with 300 trees, 5-fold time series CV. Validation Sharpe 0.85. What does Sharpe of 0.85 on label predictions actually mean for trading outcomes?\n' +
  '4. Fallback: _CentroidClassifier when lightgbm not installed. This is a nearest-centroid classifier. Are the centroids meaningful for 7-dimensional feature space?\n' +
  '5. Rolling window: trains on last 252 days. Is this enough for gold regime detection?\n' +
  '6. Ablation: groups features and tests each group individually vs baseline. Useful but does it validate the model adds value vs no-model?\n\n' +
  'B. DIRECTION PREDICTOR\n' +
  '1. Architecture: SoftmaxSequenceModel - clarify what this does from the code. Is this a neural network? A centroid? A linear model?\n' +
  '2. Training: what labels does it use? Does it share label construction with regime classifier?\n' +
  '3. The project memory says "limited predictive power". What specific limitations?\n' +
  '4. How does it integrate with the ensemble?\n\n' +
  'C. ENSEMBLE AND MODE COMPARISON\n' +
  '1. Modes: RULE_ONLY, RULE_REGIME, RULE_REGIME_SENT, FULL_ENSEMBLE. What does each actually do?\n' +
  '2. FULL_ENSEMBLE promotion risk: what would make it pass the gate? Is the gate safe?\n' +
  '3. Sentiment model: is it real or placeholder (always returns 0.0)? What API does it use?\n\n' +
  'D. STATE MACHINE (SIGNALS)\n' +
  '1. SCANNING -> ARMED -> WINDOW_OPEN -> EXECUTE transitions: trace one trade through the full state machine. Is there any path where a trade fires without proper setup?\n' +
  '2. ADX>25 threshold: why 25? Standard convention but is it tested?\n' +
  '3. EMA alignment: requires EMA_9 > EMA_20 for BUY. With require_session_filter=True, what percentage of candles qualify for arming?\n' +
  '4. Pullback logic: min_pullback_candles=1, max_pullback_candles=4. After pullback, it opens window. What constitutes a pullback? close<open for BUY, close>open for SELL. Is this too simple? A pullback could be a single red candle on a strong trend.\n' +
  '5. Breakout buffer: atr_breakout_buffer=0.3 * ATR. So entry is 0.3*ATR above armed candle high. For gold at $2000, ATR~$15, so buffer~$4.50. Reasonable?\n' +
  '6. SL/TP: atr_sl_multiplier=2.0, atr_tp_multiplier=3.0. So fixed 1:1.5 R:R. But the D4 2R exit uses 2x ATR stop with 2R=4x ATR target. Different R:R (1:2 vs 1:1.5). Is this a conflict between state machine parameters and the D4 2R fixed exit?\n' +
  '7. Armed timeout: 20 candles (5 hours). Window expiry: 6 candles (1.5 hours). Reasonable for M15?\n' +
  '8. Direction filter: BUY only or BUY+SELL based on signal direction. Does SELL side work symmetrically?\n\n' +
  'E. RETRAINER\n' +
  '1. Weekly retraining: is model state persistent across retrains? Are previous models archived?\n' +
  '2. Retrain on all available data or rolling window? Rolling 252 days only?\n' +
  '3. If retrain fails, does the system use the previous model or crash?\n\n' +
  'F. ML USEFULNESS VERDICT\n' +
  '1. Does RULE_REGIME actually add value over RULE_ONLY? What evidence supports this?\n' +
  '2. Does the ML ensemble (FULL_ENSEMBLE) add value over RULE_REGIME? The memory says "barely changes anything".\n' +
  '3. Should RULE_ONLY be the default for robustness?\n' +
  '4. Should FULL_ENSEMBLE remain experimental?\n\n' +
  'For each issue, assign severity.\n' +
  'Provide exact file:line references.',
  {
    label: 'ML/Signals Audit',
    phase: 'ML-Models-Signals-StateMachine',
    schema: {
      type: 'object',
      properties: {
        findings: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              section: { type: 'string' },
              severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'] },
              file: { type: 'string' },
              line: { type: 'string' },
              title: { type: 'string' },
              detail: { type: 'string' },
              recommendation: { type: 'string' },
            },
            required: ['section', 'severity', 'file', 'title', 'detail', 'recommendation'],
          },
        },
        mlUsefulnessScore: { type: 'integer', minimum: 1, maximum: 10 },
        signalLogicScore: { type: 'integer', minimum: 1, maximum: 10 },
        mlVerdict: { type: 'string' },
        recommendedMode: { type: 'string', enum: ['RULE_ONLY', 'RULE_REGIME', 'RULE_REGIME_SENT', 'FULL_ENSEMBLE'] },
        postAuditNarrative: { type: 'string' },
      },
      required: ['findings', 'mlUsefulnessScore', 'signalLogicScore', 'mlVerdict', 'recommendedMode', 'postAuditNarrative'],
    },
  },
)

// ============================================================
// PHASE 2: Synthesize Final Report
// ============================================================
phase('Synthesize-Final-Report')

const finalReport = await agent(
  'You are a senior quant trading desk lead producing the FINAL synthesis of a full trading system audit. ' +
  'You have received three audit reports. Synthesize them into one comprehensive, brutally honest professional audit report.\n\n' +
  '=== PHASE 1 AUDIT RESULTS: Data, Features, Backtest & Math ===\n' +
  JSON.stringify(dataBacktestAudit, null, 2) + '\n\n' +
  '=== PHASE 2 AUDIT RESULTS: Risk, Execution, Deployment & Ops ===\n' +
  JSON.stringify(riskExecDeployAudit, null, 2) + '\n\n' +
  '=== PHASE 3 AUDIT RESULTS: ML Models, Signals & State Machine ===\n' +
  JSON.stringify(mlSignalAudit, null, 2) + '\n\n' +
  'Your job is to produce a structured Markdown audit report with these exact sections:\n\n' +
  '# AURUM-1 Full Quantitative Systems Audit\n\n' +
  '## Executive Verdict\n' +
  'Score table: Engineering quality /10, Quant research quality /10, Backtest credibility /10, Risk management quality /10, ML usefulness /10, Paper-trading readiness /10, Real-money readiness /10\n' +
  'Final status: one of NOT READY FOR PAPER TRADING | PAPER TRADING CANDIDATE WITH CONDITIONS | READY FOR PAPER TRADING | NOT SAFE FOR LIVE CAPITAL | LIVE CAPITAL CANDIDATE ONLY AFTER MORE VALIDATION\n\n' +
  '## Section 1: Architecture Audit\n' +
  '## Section 2: Data and Feature Audit\n' +
  '## Section 3: Model and ML Audit\n' +
  '## Section 4: Trading Logic and Signal Audit\n' +
  '## Section 5: Risk Management Audit\n' +
  '## Section 6: Execution Audit\n' +
  '## Section 7: Backtesting and Math Audit\n' +
  '## Section 8: Test Suite and Validation Audit\n' +
  '## Section 9: Deployment and Operations Audit\n' +
  '## Section 10: Security Audit\n' +
  '## Section 11: Improvement Roadmap (A: Critical before paper trading, B: Important during paper trading, C: Required before live capital, D: Research improvements)\n' +
  '## Section 12: Final Professional Opinion\n\n' +
  'For each section:\n' +
  '- Aggregate findings from the relevant audits\n' +
  '- Do NOT list every finding from the source reports. Synthesize: highlight the 3-5 most important issues per section, then summarize the rest.\n' +
  '- Be specific with file:line references when they exist\n' +
  '- Be blunt. Do not sugarcoat.\n' +
  '- End with: "## Immediate Action Checklist" listing the top 10 highest-priority actions with: priority (1-10), action, file/module, severity, and estimated effort (hours/days).\n' +
  'Base your verdict scores on the weighted evidence from the three audit reports. If the raw scores differ, use your judgment to produce a single final score.',
  {
    label: 'Synthesize Final Report',
    phase: 'Synthesize-Final-Report',
  },
)

log('=== AUDIT COMPLETE ===')
log('Data/Backtest score: ' + dataBacktestAudit.backtestCredibilityScore + '/10')
log('Risk score: ' + riskExecDeployAudit.riskManagementScore + '/10')
log('ML usefulness score: ' + mlSignalAudit.mlUsefulnessScore + '/10')

return finalReport
