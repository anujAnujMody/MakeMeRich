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

## Development Methodology

**TDD (Test-Driven Development) is mandatory for every phase.** No exceptions.

### RED → GREEN → REFACTOR cycle per feature

1. **RED** — Write failing test first. Watch it fail. Confirm failure reason is correct.
2. **GREEN** — Write minimal production code to pass. No extra features.
3. **REFACTOR** — Clean up while keeping tests green.

### Iron Law

> No production code without a failing test first.

Code written before tests? Delete it. Start over. No "adapting" existing code.

### Tools

| Scope | Tool | Config | Purpose |
|-------|------|--------|---------|
| Python | pytest | `pyproject.toml` `[tool.pytest.ini_options]` | Test runner, fixtures, parametrize |
| Python | mypy | `--strict`, `disallow_subclassing_any = false` | Type safety |
| Python | ruff | target-version py311 | Lint + format |
| Frontend | vitest | `dashboard/vite.config.ts` | Fast unit + integration test runner |
| Frontend | @testing-library/react | `dashboard/src/test-setup.ts` | Component render + user-event testing |
| Frontend | @testing-library/jest-dom | `dashboard/src/test-setup.ts` | DOM matchers (toBeInTheDocument, etc.) |
| Frontend | msw | `dashboard/src/mocks/` | API mocking for hook/integration tests |

### Conventions

- **Python tests**: `tests/test_*.py` mirroring `strategies/` structure. `tests/conftest.py` for shared fixtures.
- **Frontend tests**: `dashboard/src/<module>/__tests__/*.test.tsx` co-located with source. `dashboard/src/test-setup.ts` setup.
- One behavior per test. No "and" in test names.
- Mocks only for unavoidable external deps (yfinance, OpenAlgo, broker API).
- Every test must fail first before implementation sees it pass.
- **Python backend + frontend + engine — all code follows TDD. No exceptions.**

### ML TDD

ML models (Phase 7) test **data contracts + interfaces**, not accuracy:

| What TDD tests | How |
|----------------|-----|
| Feature engineering | Given raw OHLCV → features shape/correctness, no NaN after warmup |
| Model interface | `train(features, labels)` succeeds, `predict(features)` returns valid shape |
| Determinism | Same input + fixed seed → same output on re-run |
| Pipeline | `config → fetch → features → train → predict` runs error-free |
| Instrument selection | `suggest_instruments()` returns valid symbols from config |

What TDD explicitly does NOT cover (validation, not spec):
- Win rate / accuracy (data-dependent, flaky)
- Hyperparameter optimality (experimental)

Example pattern:
```python
# RED: test written first
def test_feature_shape(self) -> None:
    ohlcv = sample_bars()
    feats = compute_features(ohlcv)
    assert feats.shape == (len(ohlcv) - 5, 10)

# GREEN: minimal compute_features implementation
```


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

Each branch: 1. Load `test-driven-development` skill
             2. RED: write failing tests → watch fail (vitest for frontend, pytest for backend)
             3. GREEN: implement minimal code → watch pass
             4. REFACTOR: clean up, typecheck, lint, all tests green
             5. invoke `git/commit` skill → approval board → commit → push

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
            {"symbol": "BANKNIFTY", "exchange": "NFO", "range_min": 15, "max_trades": 2, "lot_size": 15},
            {"symbol": "NIFTY",    "exchange": "NFO", "range_min": 15, "max_trades": 2, "lot_size": 25},
            {"symbol": "FINNIFTY", "exchange": "NFO", "range_min": 20, "max_trades": 1, "lot_size": 25},
            {"symbol": "SENSEX",   "exchange": "BFO", "range_min": 15, "max_trades": 2, "lot_size": 10},
        ],
        "params": {
            "target_rr": 1.5,           # 1.5× range width (was 2.0)
            "sl_rr": 1.0,               # SL = opposite end of range
            "time_exit_hour": 15,        # 3:15 PM IST (was 105 min hold)
            "time_exit_minute": 15,
            "min_range_pts": 20,         # Skip if range < 20 pts
            "max_range_pts": 200,        # Skip if range > 200 pts
            "volume_multiplier": 1.5,    # Breakout vol ≥ 1.5× avg range vol
            "max_per_direction": 1,      # One trade per direction per day
        },
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
├── pyproject.toml           # pytest + mypy --strict + ruff config
├── tests/
│   ├── conftest.py           # Shared fixtures (OHLCV bars, IST time)
│   ├── test_base.py          # Position sizing, risk gates, trade sim
│   ├── test_config.py        # Config loading, instrument lookup
│   ├── test_registry.py      # Register/get/list strategies
│   └── test_orbs.py          # ORB range calc, breakout, exits, filters
├── strategies/
│   ├── base.py               # [TDD] StrategyBase ABC
│   ├── registry.py           # [TDD] Strategy registry
│   ├── config.py             # [TDD] Multi-instrument config
│   └── orbs/
│       ├── __init__.py       # [TDD] Package export
│       └── orbs.py           # [TDD] ORBStrategy (close breakout)
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

| Phase | Branch | TDD | Deliverable | Commits |
|-------|--------|-----|-------------|---------|
| **1** | `feat/scaffolding` | No | `.opencode/` + monorepo + dirs + configs | 3-4 |
| **2** | `feat/dashboard-hooks` | No | Zustand stores + TanStack hooks + API client | 2-3 |
| **3** | `feat/strategy-orbs` | **Yes** | ORB Python (close-confirmed breakout, 3 exits, volume/width filters) + multi-instrument config + tests | 3 |
| **4** | `feat/strategy-insights` | **Yes** | React page (signals, PnL, trade log) + tests | 3-4 |
| **5** | `feat/executor` | **Yes** | Engine loop + scheduler + deploy to VPS + tests | 3 |
| **6** | `feat/self-learning` | **Yes** | Stats page + param optimizer + tests | 3 |
| **7** | `feat/ml-engine` | **Yes** | RF model + instrument suggestion + tests | 3-4 |

## Cost Breakdown

| Item | Monthly |
|------|---------|
| CloudPe VPS (Mumbai, 2GB) | ₹400 |
| Angel One API | Free |
| Domain (optional) | ~₹42 |
| Brokerage (~40 orders/day) | ~₹800 |
| **Total** | **~₹1,242/mo** |
