---
name: algo-trading-project
description: Project-specific knowledge for the Algo Trading Tool. Contains broker config, instrument list, strategy registry, risk rules, and architecture conventions. Use when implementing strategies, configuring instruments, or making architecture decisions.
---

# Algo Trading Project

Project-specific knowledge for the Bank Nifty options trading tool.

## Broker Setup

- **Primary:** Angel One SmartAPI via OpenAlgo
- **Switch:** Change `BROKER` env var in OpenAlgo config. Zero code changes to our dashboard.
- **OpenAlgo REST API:** All communication goes through OpenAlgo's 57 REST endpoints. No direct broker API calls.
- **Rate limits:** 9 req/sec (Angel), 10 req/sec (Zerodha)

## Supported Brokers (via OpenAlgo)

AngelOne, Zerodha, Dhan, Fyers, Upstox, Shoonya, 5paisa, AliceBlue, HDFC Sky, Kotak Neo, Motilal Oswal, +22 more

## Strategy Registry

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

## Risk Rules

- Max loss/day: ₹300 (3% of ₹10K capital)
- Max loss/trade: ₹150 (1.5%)
- Max trades/day: 2 per instrument
- No positions past 11:00 AM IST
- No Fridays near monthly expiry
- No news-event days (budget, RBI policy)

## Architecture

```
UI → Store (Zustand) → Hooks (TanStack Query) → API (OpenAlgo REST)
```

### Layer Rules

```
dashboard/src/
├── pages/        # Compose. Connect stores + hooks. Route-level components.
├── components/   # Pure UI. Read from stores, call hook actions. NO business logic.
├── hooks/        # Orchestrate. Read store → call api → write store.
├── stores/       # Zustand. Hold all client state + setters. NO async.
└── lib/api.ts    # Thin fetch wrappers. Parameterized by base URL. NO domain logic.
```

### Layer: `pages/`
- Import from stores, hooks, and components
- Define which hooks to call and how to compose child components
- Hold tab/filter/layout state only (which tab is active, what filter is selected)
- **NOT** for business logic (parsing, aggregation, calculation)

### Layer: `components/`
- Receive data from store selectors or props
- Call actions exposed by hooks (e.g. `runOptimization()`)
- **MUST NOT** use `useMutation` or `useQuery` directly
- **MUST NOT** have business logic (parsing, calculating, filtering, aggregation)
- **MUST NOT** use `useState` for API-related state (put it in store)
- **MUST NOT** call `fetch` directly
- **CAN** use `useState` for pure UI state (dropdown open, tooltip visible)

### Layer: `hooks/`
- Read from stores, call API, write results to stores
- Encapsulate orchestration logic
- Thin pass-through hooks are acceptable for reads (`return useQuery(...)`)
- Write operations **must** orchestrate: read store → parse/transform → call API → write store
- Handle loading/error/success states in the store through the hook

### Layer: `stores/`
- Zustand stores hold all mutable client state
- Include state for async operations: `isLoading`, `isError`, `error`, results
- **NO async logic** (no promises, no fetch calls)
- **NO side effects** (no localStorage writes, no navigation)

### Layer: `lib/api.ts`
- Thin fetch wrappers — one `get()`, one `post()` with optional `baseUrl` parameter
- **MUST NOT** have domain-specific functions duplicated (no `engineGet`/`enginePost`)
- Domain-specific paths go in the `api` object structure

### Data Flow

```
User action → Component calls hook action
  → Hook reads store state
    → Hook transforms data (parse, aggregate)
    → Hook calls API via api.ts
      → API returns response
    → Hook writes results to store
  → Component re-renders from store subscription
```

### Anti-Patterns (Blocking)

| Pattern | Why | Fix |
|---------|-----|-----|
| Component has `parseParamGrid`, `calculateScore` etc | Business logic in UI | Move to hook |
| Component uses `useMutation`/`useQuery` directly | Skips orchestration layer | Wrap in hook |
| Component uses `useState` for API data | State lost on remount, no centralized access | Move to store |
| Hook is a pass-through `return useMutation(...)` | No orchestration, caller must handle lifecycle | Add store read/write |
| Store missing async state fields | Caller has no way to track loading/error | Add `isLoading`, `error`, results |
| `engineGet`/`enginePost` duplication | 2 functions doing same thing with different base | Use parameterized `get(baseUrl)` |
| Page has inline business logic | Violates separation of concerns | Extract to hook |
| Direct `fetch()` in component | Bypasses API layer, error handling duplicated | Use api.ts |

### Architecture Review Checklist

For each file in the diff, identify its layer and verify:

- [ ] File belongs in correct directory (pages vs components vs hooks vs stores)
- [ ] Component: no `useMutation`, `useQuery`, business logic, or `useState` for API state
- [ ] Hook: orchestrates store→api→store (not a pass-through for mutations)
- [ ] Store: has all needed async state fields, no async logic
- [ ] API: no duplicated fetch wrappers, `baseUrl` parameter used correctly
- [ ] Data flows in one direction (UI → Store → Hooks → API)

### Reference Implementation

Good pattern — `usePnLAnalysis` (thin read hook):
```ts
export function usePnLAnalysis(from?: string, to?: string) {
  return useQuery({ queryKey: ['pnl-analysis', from, to], queryFn: () => api.pnl.analysis(from, to) })
}
```

Good pattern — `useParamOptimization` (orchestrated write hook):
```ts
export function useParamOptimization() {
  const selectedStrategy = useLearningStore(s => s.selectedStrategy)
  const paramGrid = useLearningStore(s => s.paramGrid)
  const setResults = useLearningStore(s => s.setOptimizationResults)
  const setIsOptimizing = useLearningStore(s => s.setIsOptimizing)
  const setError = useLearningStore(s => s.setOptimizationError)

  const mutation = useMutation({ mutationFn: (p) => api.learning.optimize(p) })

  const runOptimization = async () => {
    if (!selectedStrategy) return
    const grid = parseParamGrid(paramGrid)  // business logic lives here
    setResults(null); setError(null); setIsOptimizing(true)
    try {
      const data = await mutation.mutateAsync({ strategy: selectedStrategy, param_grid: grid })
      setResults(data.results)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setIsOptimizing(false)
    }
  }
  return { runOptimization }
}
```

- `dashboard/` — React 19 + shadcn/ui + Zustand + TanStack Query
- `strategies/` — Python strategy engine (runs inside OpenAlgo strategy host)
- `engine/` — Python executor + scheduler + self-learning ML
