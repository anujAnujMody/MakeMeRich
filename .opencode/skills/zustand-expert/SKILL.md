---
name: zustand-expert
description: Zustand 5 state management patterns for React. Use when creating stores, writing selectors, implementing middleware (persist, devtools, immer), or combining Zustand with TanStack Query. Covers store structure, selector performance, TypeScript patterns, and testing.
---

# Zustand Expert

Zustand 5 state management patterns for React applications.

## When to Use

- Creating new Zustand stores
- Writing selectors with performance in mind
- Implementing middleware (persist, devtools, immer)
- Combining Zustand with TanStack Query (client vs server state separation)
- Migrating from Context API or Redux

## Core Principles

1. **Client state only** — Zustand manages UI state (theme, sidebar, selected filters). Server data goes in TanStack Query.
2. **Small, focused stores** — One concern per store. Don't create a single global store.
3. **Selector functions** — Always use `useStore(s => s.field)` not `useStore()` to avoid full-store re-renders.
4. **Actions in the store** — Encapsulate state + logic together. Don't scatter `set()` calls in components.
5. **Persist middleware** — For state that survives refresh (preferences, recent selections).

## Store Pattern

```typescript
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface InstrumentState {
  selectedSymbol: string
  selectedExchange: string
  timeRange: '1D' | '1W' | '1M'
  setSymbol: (symbol: string, exchange: string) => void
  setTimeRange: (range: '1D' | '1W' | '1M') => void
}

export const useInstrumentStore = create<InstrumentState>()(
  persist(
    (set) => ({
      selectedSymbol: 'BANKNIFTY',
      selectedExchange: 'NFO',
      timeRange: '1D',
      setSymbol: (symbol, exchange) => set({ selectedSymbol: symbol, selectedExchange: exchange }),
      setTimeRange: (timeRange) => set({ timeRange }),
    }),
    {
      name: 'instrument-store',
      partialize: (state) => ({ timeRange: state.timeRange }),
    }
  )
)
```

## Selector Performance

```typescript
// ❌ Bad — re-renders on any state change
const state = useInstrumentStore()

// ✅ Good — only re-renders when selectedSymbol changes
const symbol = useInstrumentStore((s) => s.selectedSymbol)

// ✅ Multiple selectors with shallow comparison
import { shallow } from 'zustand/shallow'
const { selectedSymbol, selectedExchange } = useInstrumentStore(
  (s) => ({ selectedSymbol: s.selectedSymbol, selectedExchange: s.selectedExchange }),
  shallow
)
```

## Zustand + TanStack Query Separation

| State Type | Tool | Examples |
|------------|------|---------|
| UI state | Zustand | Theme, sidebar, selected instrument, time range |
| Server data | TanStack Query | Market data, orders, positions, trade history |
| Derived/real-time | Zustand + Query | Current PnL (queried data + local calculation) |

Never copy server data into Zustand. Read from query cache with `queryClient.getQueryData()` if needed.

## Testing

```typescript
import { create } from 'zustand'
import { vi, describe, it, expect } from 'vitest'

function createTestStore(initial?: Partial<InstrumentState>) {
  return create<InstrumentState>()((set) => ({
    selectedSymbol: 'BANKNIFTY',
    selectedExchange: 'NFO',
    timeRange: '1D',
    setSymbol: (symbol, exchange) => set({ selectedSymbol: symbol, selectedExchange: exchange }),
    setTimeRange: (timeRange) => set({ timeRange }),
    ...initial,
  }))
}
```
