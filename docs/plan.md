# Algo Trading Tool — Implementation Plan

## `.opencode/` — Local Skills (15 total)

| # | Skill | Source | Purpose |
|---|-------|--------|---------|
| 1 | `git/` (commit/push/pr) | Port from RFID | Interactive commit with approval gate |
| 2 | `react-expert` | Port from RFID / farmage | React 19 patterns, hooks, TSX |
| 3 | `react-best-practices` | Vercel Labs (official) | React/Next.js performance rules |
| 4 | `shadcn` | Port from RFID / oakoss | shadcn/ui component composition |
| 5 | `tanstack-query-expert` | FrancoStino collection | TanStack Query v5 patterns |
| 6 | `typescript-advanced` | Port from RFID | Type-safe code |
| 7 | `zustand-expert` | Write custom | Zustand 5 store patterns |
| 8 | `python` | full-stack-skills / farmage | Python strategy + engine code |
| 9 | `algo-strategy` | marketcalls (OpenAlgo creator) | ORB strategy Python code gen |
| 10 | `algo-options` | marketcalls | Options templates |
| 11 | `agiprolabs-trading` | wshobson/agents (241⭐) | Backtesting, risk, ML trading |
| 12 | `stock-market-pro` | sundial-org (633⭐) | Market data + charts |
| 13 | `data-science-python` | probabl-ai (official scikit-learn) | Random Forest, pandas |
| 14 | `machine-learning` | mindrally (197⭐) | Generic ML for self-learning |
| 15 | `algo-trading-project` | Custom | Project-specific: broker config, instrument list, strategy registry |

**Install method:** RFID skills = copy directories. `npx skills add` skills = install to global → copy into `.opencode/skills/`. Custom skills = write directly.

## Git Branch Strategy

```
feat/scaffolding           → .opencode/ + root monorepo + dirs
feat/dashboard-hooks        → Zustand stores + TanStack hooks + API client
feat/strategy-orbs          → ORB Python engine + multi-instrument config
feat/strategy-insights      → React page (signals, PnL, trade log)
feat/executor               → Engine loop + scheduler on VPS
feat/self-learning          → Stats page + param optimizer
feat/ml-engine              → Random Forest + instrument suggestion
```

Each branch: implement → invoke `git/commit` skill → approval board → commit → push.

## Architecture Pattern: UI → Store → Hooks → API

```
┌─────────────────────────────────────────────────┐
│              React Dashboard                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│  │  Pages   │  │  Comps   │  │  Layouts     │  │
│  └────┬─────┘  └────┬─────┘  └──────────────┘  │
│       │             │                           │
│  ┌────▼─────────────▼──────────────────────┐    │
│  │  Hooks (TanStack Query)                │    │
│  │  useMarketData │ useOrders             │    │
│  │  usePositions  │ useTradeLog           │    │
│  │  useStrategyConfig │ usePnLAnalysis    │    │
│  └────┬─────────────┬──────────────────────┘    │
│       │             │                           │
│  ┌────▼────┐  ┌────▼──────────────┐            │
│  │ Zustand │  │ api/client.ts     │             │
│  │ Stores  │  │ REST + WebSocket  │             │
│  └─────────┘  └────┬──────────────┘             │
└─────────────────────┼───────────────────────────┘
                      │ HTTP / WS
┌─────────────────────┼───────────────────────────┐
│           OpenAlgo Flask Backend                 │
│  ┌──────────────────▼────────────────────────┐  │
│  │    Broker Plugin Layer (34 brokers)       │  │
│  │  AngelOne │ Zerodha │ Dhan │ Fyers...   │  │
│  └───────────────────────────────────────────┘  │
│  ┌──────────────────▼────────────────────────┐  │
│  │    Strategy Executor (our addition)       │  │
│  │  ORB │ MA Cross │ Straddle │ Custom...   │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

**Broker switching = zero code changes.** OpenAlgo abstracts all 34 brokers. Our code talks only to OpenAlgo REST API.

## Multi-Instrument Config (Day 1)

```python
STRATEGIES = {
    "orbs": {
        "instruments": [
            {"symbol": "BANKNIFTY", "exchange": "NFO", "range_min": 15, "max_trades": 2},
            {"symbol": "NIFTY",    "exchange": "NFO", "range_min": 15, "max_trades": 2},
            {"symbol": "FINNIFTY", "exchange": "NFO", "range_min": 20, "max_trades": 1},
            {"symbol": "SENSEX",   "exchange": "BFO", "range_min": 15, "max_trades": 2},
        ],
        "params": {"target_rr": 2.0, "sl_rr": 1.0, "max_hold_min": 105},
    }
}
```

Future AI: predicts win probability per instrument pre-market, auto-selects top 1-2.

## Full File Tree

```
W:\Trading\
├── .opencode/
│   ├── opencode.json
│   ├── AGENTS.md
│   ├── .gitignore
│   └── skills/        (15 skills)
│       ├── git/{commit,push,pr}/
│       ├── react-expert/
│       ├── react-best-practices/
│       ├── shadcn/
│       ├── tanstack-query-expert/
│       ├── typescript-advanced/
│       ├── zustand-expert/
│       ├── python/
│       ├── algo-strategy/
│       ├── algo-options/
│       ├── agiprolabs-trading/
│       ├── stock-market-pro/
│       ├── data-science-python/
│       ├── machine-learning/
│       └── algo-trading-project/
├── dashboard/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── StrategyInsights.tsx
│   │   │   └── SelfLearning.tsx
│   │   ├── components/
│   │   │   ├── InstrumentSelector.tsx
│   │   │   ├── StrategyCard.tsx
│   │   │   ├── PnLCard.tsx
│   │   │   ├── TradeLogTable.tsx
│   │   │   ├── WinRateChart.tsx
│   │   │   ├── ParamOptimizer.tsx
│   │   │   └── FeatureImportance.tsx
│   │   ├── hooks/
│   │   │   ├── useMarketData.ts
│   │   │   ├── useOrders.ts
│   │   │   ├── usePositions.ts
│   │   │   ├── useTradeLog.ts
│   │   │   ├── usePnLAnalysis.ts
│   │   │   └── useWebSocket.ts
│   │   ├── stores/
│   │   │   ├── instrumentStore.ts
│   │   │   ├── strategyStore.ts
│   │   │   ├── uiStore.ts
│   │   │   └── tradeStore.ts
│   │   ├── api/
│   │   │   └── client.ts
│   │   ├── types/
│   │   │   └── index.ts
│   │   ├── lib/
│   │   │   └── utils.ts
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── tailwind.config.ts
├── strategies/
│   ├── base.py
│   ├── registry.py
│   ├── config.py
│   └── orbs/
│       ├── __init__.py
│       └── orbs.py
├── engine/
│   ├── executor.py
│   ├── scheduler.py
│   ├── self_learning.py
│   ├── ml_model.py
│   └── config.py
├── data/
│   ├── .gitkeep
│   └── trades.db
├── docker-compose.yml
├── .env
└── README.md
```

## Phase Execution Order

| Phase | Branch | Deliverable | Est. Commits |
|-------|--------|-------------|-------------|
| **1** | `feat/scaffolding` | `.opencode/` + monorepo + dirs + configs | 3-4 |
| **2** | `feat/dashboard-hooks` | Zustand stores + TanStack hooks + API client | 2-3 |
| **3** | `feat/strategy-orbs` | ORB Python + multi-instrument config | 2 |
| **4** | `feat/strategy-insights` | React page: signals, PnL, trade log | 2-3 |
| **5** | `feat/executor` | Engine loop + scheduler + deploy to VPS | 2 |
| **6** | `feat/self-learning` | Stats page + param optimizer | 2 |
| **7** | `feat/ml-engine` | RF model + instrument suggestion | 2-3 |

## Cost Breakdown

| Item | Monthly |
|------|---------|
| CloudPe VPS (Mumbai, 2GB) | ₹400 |
| Angel One API | Free |
| Domain (optional) | ~₹42 |
| Brokerage (~40 orders/day) | ~₹800 |
| **Total** | **~₹1,242/mo** |
