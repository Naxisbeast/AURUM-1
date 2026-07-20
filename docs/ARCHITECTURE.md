# AURUM-1 System Architecture

## Overview

AURUM-1 follows a layered architecture with strict dependency rules:
each layer only calls the layer below it.

### Layered Architecture

```mermaid
graph TD
    O[Orchestrator Layer<br/>Coordinates data->features->models->signals->risk->execution<br/>Manages threads, health endpoint, retraining scheduler]

    O --> E[Execution Layer]

    subgraph E[Execution Layer]
        EE[ExecutionEngine<br/>Routes orders, logs trades]
        BB[BrokerBase]
        PB[PaperBroker]
        OB[OandaBroker<br/>OANDA v20]
        BB --- PB
        BB --- OB
    end

    E --> R[Risk Layer]

    subgraph R[Risk Layer]
        RM[RiskManager]
        K[Kelly optimal sizing]
        PL[Portfolio risk limits - max 3%]
        DL[Daily loss kill switch - -3%]
        TD[Total drawdown kill switch - -8%]
        SF[Spread filters - >3 pips reject]
        REC[Recovery mode - halve risk<br/>during drawdown]
        RC[Regime-conflict detection]
    end

    R --> S[Signal Layer]

    subgraph S[Signal Layer]
        SM[StateMachine]
        SC[SCANNING]
        AR[ARMED]
        WO[WINDOW_OPEN]
        TR[TRADE]
        SC --> AR --> WO --> TR
    end

    S --> M[Model / Signal Layer]

    subgraph M[Model / Signal Layer]
        RC2[Regime Classifier]
        DP[Direction Predictor]
        SS[Sentiment Scorer]
        ENS[Ensemble Signal<br/>direction x regime x sentiment]
        RC2 --> ENS
        DP --> ENS
        SS --> ENS
    end

    M --> F[Feature Layer]

    subgraph F[Feature Layer]
        FE[FeatureEngineer]
        OHLCV[OHLCV <br/>ATR, ADX, EMA, BB, MACD, RSI]
        MACRO[Macro <br/>DXY, VIX, Real Yield, CPI]
        COT[COT <br/>Net Long/Short, Open Interest]
        TIME[Time <br/>Session labels, Cyclical encoding]
    end

    F --> D[Data Ingestion Layer]

    subgraph D[Data Ingestion Layer]
        DI[AurumDataIngestor]
        OANDA[OANDA API <br/>OHLCV M5, M15, H1, H4, D1]
        FRED[FRED API <br/>Macro DGS10, CPI]
        YH[Yahoo Finance <br/>DXY, VIX]
        AV[Alpha Vantage <br/>News headlines]
        CFTC[CFTC <br/>COT data]
    end

    style O fill:#1a1a2e,stroke:#e94560,color:#fff
    style E fill:#16213e,stroke:#0f3460,color:#fff
    style R fill:#16213e,stroke:#0f3460,color:#fff
    style S fill:#16213e,stroke:#0f3460,color:#fff
    style M fill:#16213e,stroke:#0f3460,color:#fff
    style F fill:#16213e,stroke:#0f3460,color:#fff
    style D fill:#16213e,stroke:#0f3460,color:#fff
```

---

## Research Layer (Parallel System)

The research system is independent of the main trading pipeline:

```mermaid
graph LR
    OA[OANDA API] --> MC[Market Cache]
    MC --> FSD[forward_shadow_donchian.py]
    FSD --> DS[donchian_shadow.sqlite3<br/>signals, trades, equity curve]
    DS --> PR[Phase Reports S1-S5]
    PR --> RF[Research Findings]

    PR --> RD2[Raw Donchian 2R<br/>continuous service]
    PR --> D1[D1 1R+filter<br/>15-min timer]
    PR --> D2[D2 1R+filter<br/>15-min timer]

    style OA fill:#e94560,color:#fff
    style FSD fill:#0f3460,color:#fff
    style DS fill:#1a1a2e,color:#fff
    style RF fill:#533483,color:#fff
```

---

## Storage Architecture

```mermaid
graph TD
    subgraph data[aurum1/data/]
        A1[aurum1.sqlite3<br/>Main trade log & performance]
        FMC[forward_shadow_market_cache.sqlite3<br/>OANDA market data for shadow]
        BMC[backtest_market_cache.sqlite3<br/>OANDA market data for backtest]
    end

    subgraph reports[reports/]
        subgraph fs[forward_shadow/]
            DS2[donchian_shadow.sqlite3<br/>Shadow ledger - 97 signals, 34 trades]
            D2S[donchian_d2_shadow.sqlite3<br/>D2 shadow ledger]
            CSV[phase_s*.csv<br/>Phase research CSVs]
            JSON[phase_s*.json<br/>Phase research summaries]
            WK[donchian_shadow_weekly_*.json<br/>Weekly performance reports]
        end
        BE[backtest_execution_*.sqlite3<br/>Isolated backtest DBs]
        RES[research/<br/>Research JSON/CSV outputs]
    end

    subgraph backups[backups/forward_shadow/]
        BAK[Daily SQLite backups<br/>28 days retained]
    end

    style data fill:#16213e,color:#fff
    style reports fill:#1a1a2e,color:#fff
    style backups fill:#0f3460,color:#fff
```

---

## Safety Architecture

```mermaid
graph TD
    AO[ALLOW_OANDA_ORDERS=false] --> AL[ALLOW_LIVE_TRADING=false]
    AL --> OE[OANDA_ENV=practice]
    OE --> FC[Forward shadow: fails closed<br/>if incorrectly configured]
    FC --> RM2[Risk Manager: kill switches<br/>daily loss, drawdown]
    RM2 --> PB2[PaperBroker: in-memory only<br/>no external connectivity]

    style AO fill:#e94560,color:#fff
    style AL fill:#e94560,color:#fff
    style OE fill:#e94560,color:#fff
    style PB2 fill:#533483,color:#fff
```

---

## Threading Model

The orchestrator runs on the main thread with three background threads:

| Thread | Purpose | Interval |
|--------|---------|----------|
| Main | M15 candle processing loop | Every 15 min (aligned to candle close) |
| macro-refresh | Fetch macro data | Every 60 min |
| sentiment-refresh | Fetch news | Every 30 min |
| retraining | Check if weekly retraining due | Every 60 sec |
| health | Flask HTTP health endpoint | Continuous |

> **Note**: The orchestrator and ML models depicted above are legacy (stopped since May 27, 2026).
> The current live system is the D4 Paper Trader which uses a simplified direct path:
> market cache -> features -> Donchian signal -> RiskManager -> PaperBroker.
> See [TRUTH_MAP.md](system/TRUTH_MAP.md) for the actual runtime architecture.
