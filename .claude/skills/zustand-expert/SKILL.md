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

## Core Principles

1. **Client state only** — Zustand manages UI state (theme, sidebar, selected filters). Server data
   goes in TanStack Query. Never copy server data into Zustand.
2. **Small, focused stores** — One concern per store. Don't create a single global store.
3. **Selector functions** — Always use `useStore(s => s.field)` not `useStore()` to avoid
   full-store re-renders.
4. **Actions in the store** — Encapsulate state + logic together. Don't scatter `set()` calls in
   components.
5. **Persist middleware** — For state that survives refresh (preferences, recent selections).

## Store Pattern

```typescript
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface UIState {
  sidebarCollapsed: boolean
  theme: 'light' | 'dark'
  setSidebarCollapsed: (collapsed: boolean) => void
  setTheme: (theme: 'light' | 'dark') => void
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      theme: 'dark',
      setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
      setTheme: (theme) => set({ theme }),
    }),
    {
      name: 'ui-store',
      partialize: (state) => ({ theme: state.theme }),
    }
  )
)
```

## Selector Performance

```typescript
// ❌ Bad — re-renders on any state change
const state = useUIStore()

// ✅ Good — only re-renders when sidebarCollapsed changes
const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed)

// ✅ Multiple selectors — Zustand v5 removed the create-hook's second-arg equality-fn
// overload. Use `useShallow` from zustand/react/shallow, not the old `zustand/shallow`
// `shallow` comparator passed as a second argument — that v4 pattern throws in v5.
import { useShallow } from 'zustand/react/shallow'

const { sidebarCollapsed, theme } = useUIStore(
  useShallow((s) => ({ sidebarCollapsed: s.sidebarCollapsed, theme: s.theme }))
)
```

## Middleware

```typescript
import { create } from 'zustand'
import { devtools, persist } from 'zustand/middleware'
import { immer } from 'zustand/middleware/immer'

interface PositionState {
  positions: Record<string, { qty: number; pnl: number }>
  updatePnl: (symbol: string, pnl: number) => void
}

// devtools: exposes the store to Redux DevTools for time-travel debugging
// immer: lets you write "mutating" update logic that's actually immutable under the hood
export const usePositionStore = create<PositionState>()(
  devtools(
    immer((set) => ({
      positions: {},
      updatePnl: (symbol, pnl) =>
        set((state) => {
          if (state.positions[symbol]) state.positions[symbol].pnl = pnl
        }),
    })),
    { name: 'position-store' }
  )
)
```

Order matters when stacking middleware: `devtools(persist(immer(...)))` is the conventional outer-
to-inner order — devtools outermost so it sees the final store shape, immer innermost so it wraps
the raw `set`.

## Zustand + TanStack Query Separation

| State Type | Tool | Examples |
|------------|------|---------|
| UI state | Zustand | Theme, sidebar, selected instrument, time range, form drafts |
| Server data | TanStack Query | Positions, orders, trade history, account balance |
| Derived/real-time | Zustand + Query | Rarely needed — prefer deriving during render from Query data |

Never copy server data into Zustand. Read from the query cache with `queryClient.getQueryData()` if
a non-component context needs it.

## Testing

```typescript
import { create } from 'zustand'
import { vi, describe, it, expect } from 'vitest'

function createTestStore(initial?: Partial<UIState>) {
  return create<UIState>()((set) => ({
    sidebarCollapsed: false,
    theme: 'dark',
    setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
    setTheme: (theme) => set({ theme }),
    ...initial,
  }))
}
```
