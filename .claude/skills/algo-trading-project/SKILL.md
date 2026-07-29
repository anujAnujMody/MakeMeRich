---
name: algo-trading-project
description: Project-specific knowledge for the Algo Trading Tool. Contains broker config, instrument list, strategy registry, risk rules, and architecture conventions. Use when implementing strategies, configuring instruments, or making architecture decisions.
---

# Algo Trading Project

Project-specific knowledge for the Indian index-options trading tool.

> **Note on the numbers below:** the strategy registry, lot sizes, and risk-rupee figures in this
> file predate the current rewrite plan and are stale (lot sizes especially — NSE revises them
> roughly every six months, do not hardcode them anywhere, read from the broker at runtime). Treat
> the **layer rules and architecture sections below as current and load-bearing** — those are
> unaffected by the numeric drift. For current facts (lot sizes, instruments, risk model), see the
> plan file referenced in project memory, not this section.

## Broker Setup

- **Primary:** Angel One SmartAPI via OpenAlgo (self-hosted)
- **Switch:** Change `BROKER` env var in OpenAlgo config. Zero code changes to our dashboard.
- **OpenAlgo REST API:** All communication goes through OpenAlgo's REST endpoints, auth via `apikey`
  in the JSON body (POST) / query param (GET) — never a bearer header.
- **Rate limits:** 10 req/sec order APIs, 50 req/sec data APIs (platform-wide OpenAlgo limits).

## Instruments (indicative — verify against the plan file / live registry)

All three of NIFTY, BANKNIFTY, SENSEX are tradeable; the engine picks per-signal based on which
affords a sane strike at current capital. BANKNIFTY has monthly-only expiry (SEBI, Nov 2024); NIFTY
and SENSEX have weeklies. Lot sizes are read from the broker at runtime, never hardcoded.

## Architecture

```
UI → Store (Zustand) → Hooks (TanStack Query) → API (OpenAlgo REST, via the engine)
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
- `engine/` — Python FastAPI engine (see the project plan for current architecture)

## WebSocket guidance for when the real engine lands (Phase 2 — not built yet)

No WebSocket exists anywhere in the dashboard today — it's entirely MSW-mocked over plain
HTTP polling. When Phase 2 (data layer) wires up the real OpenAlgo WS feed, apply these rules to
avoid the standard TanStack-Query-vs-WebSocket pitfalls, researched ahead of time so they're not
re-discovered under pressure:

- **`staleTime: Infinity`** on any query whose data arrives via WS push (live quotes, order-update
  stream). The default `staleTime: 0` fights the socket — TanStack Query refetches on its own
  schedule while the socket is also pushing fresh data, doubling network calls for no benefit.
- **Prefer invalidation over full-payload pushes.** When a WS event means "this data changed", call
  `queryClient.invalidateQueries({ queryKey })` and let the existing query refetch — don't push the
  full payload through the socket and `setQueryData` it directly unless the data is high-frequency
  (see next point). Invalidation is a no-op if nothing is currently observing that key.
  - **Use `setQueryData` directly only for high-frequency streams** (tick-level quotes) — and
    **throttle** it above roughly 100 updates/sec, or a rapidly re-rendering table becomes the
    bottleneck, not the network.
- **Reconnect must re-authenticate and re-subscribe**, not just reopen the socket — OpenAlgo's WS
  requires both steps after any drop (see the OpenAlgo API notes elsewhere in this skill).
- **MSW does not mock WebSockets the same way it mocks REST** — the mocked layer today is the least
  representative of what the real integration will look like. Before wiring the real engine, add
  artificial latency and out-of-order/duplicate-tick simulation to the WS mock (once one exists) so
  the UI is exercised against realistic conditions before it ever touches a live feed.
