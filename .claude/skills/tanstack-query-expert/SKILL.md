---
name: tanstack-query-expert
description: "Expert in TanStack Query (React Query) — asynchronous state management. Covers data fetching, stale time configuration, mutations, optimistic updates."
risk: safe
source: community
date_added: "2026-03-07"
---

# TanStack Query Expert

You are a production-grade TanStack Query (formerly React Query) expert. You help build robust,
performant asynchronous state management in a React 19 + Vite SPA. Master declarative data
fetching, cache invalidation, optimistic UI updates, and background syncing.

> This project is a Vite SPA (react-router, no SSR) — Next.js App Router / Server Component
> hydration patterns do not apply here and are omitted.

## When to Use This Skill

- Use when setting up or refactoring data fetching logic (replacing `useEffect` + `useState`)
- Use when designing query keys (Array-based, strictly typed keys)
- Use when configuring global or query-specific `staleTime`, `gcTime`, and `retry` behavior
- Use when writing `useMutation` hooks for POST/PUT/DELETE requests
- Use when invalidating the cache (`queryClient.invalidateQueries`) after a mutation
- Use when implementing Optimistic Updates for instant UX feedback
- Use when wiring a WebSocket feed into the Query cache via `setQueryData`

## Core Concepts

### Why TanStack Query?

TanStack Query is not just for fetching data; it's an **asynchronous state manager**. It handles
caching, background updates, deduplication of multiple requests for the same data, and
loading/error states out of the box.

**Rule of Thumb:** Never use `useEffect` to fetch data if TanStack Query is available in the stack.

## Query Definition Patterns

### The Custom Hook Pattern (Best Practice)

Always abstract `useQuery` calls into custom hooks to encapsulate the fetching logic, TypeScript
types, and query keys — per this project's layer rules (see `algo-trading-project`), pages and
components never call `useQuery` directly.

```typescript
import { useQuery } from '@tanstack/react-query';

type Position = { symbol: string; qty: number; pnl: number };

const fetchPositions = async (): Promise<Position[]> => {
  const res = await fetch('/api/positions');
  if (!res.ok) throw new Error('Failed to fetch positions');
  return res.json();
};

export const usePositions = () => {
  return useQuery({
    queryKey: ['positions'],
    queryFn: fetchPositions,
    staleTime: 1000 * 5, // 5s — this is live trading data, keep it short
  });
};
```

### Advanced Query Keys

Query keys uniquely identify the cache. They must be arrays, and order matters.

```typescript
// Filtering / Sorting
useQuery({
  queryKey: ['trades', { from: '2026-07-01', to: '2026-07-28' }],
  queryFn: () => fetchTrades({ from: '2026-07-01', to: '2026-07-28' })
});

// Factory pattern for query keys (recommended for a dashboard this size)
export const tradeKeys = {
  all: ['trades'] as const,
  lists: () => [...tradeKeys.all, 'list'] as const,
  list: (filters: string) => [...tradeKeys.lists(), { filters }] as const,
  details: () => [...tradeKeys.all, 'detail'] as const,
  detail: (id: string) => [...tradeKeys.details(), id] as const,
};
```

## Mutations & Cache Invalidation

### Basic Mutation with Invalidation

```typescript
import { useMutation, useQueryClient } from '@tanstack/react-query';

export const useSquareOffPosition = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (positionId: string) => {
      const res = await fetch(`/api/positions/${positionId}/squareoff`, { method: 'POST' });
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['positions'] });
      queryClient.invalidateQueries({ queryKey: ['trades'] });
    },
  });
};
```

### Optimistic Updates

Give the user instant feedback, and roll back if the request fails. Use sparingly on this project —
prefer showing a pending/confirming state for anything that places or cancels a real order, since a
falsely-optimistic "order placed" is worse here than a half-second of loading state.

```typescript
export const usePauseStrategy = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: pauseStrategyFn,

    onMutate: async (strategyId) => {
      await queryClient.cancelQueries({ queryKey: ['strategies'] });
      const previous = queryClient.getQueryData(['strategies']);
      queryClient.setQueryData(['strategies'], (old: Strategy[]) =>
        old.map((s) => (s.id === strategyId ? { ...s, paused: true } : s))
      );
      return { previous };
    },

    onError: (_err, _strategyId, context) => {
      queryClient.setQueryData(['strategies'], context?.previous);
    },

    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['strategies'] });
    },
  });
};
```

## Real-time: WebSocket → Query cache

No first-class subscription API exists in TanStack Query. The established pattern:

1. `useQuery` fetches the initial snapshot (loading/error/retry for free).
2. A single app-level WebSocket provider owns the connection.
3. On each message, call `queryClient.setQueryData(key, updater)` for surgical updates (ticks,
   position deltas) — instant re-render of every subscribed component.
4. Use `invalidateQueries` only for coarse events you don't trust as a delta (e.g. "orders changed").
5. Set `staleTime: Infinity` on socket-fed queries so polling doesn't fight the socket.
6. Use React 19's `useEffectEvent` for the message handler so the socket effect doesn't tear down
   and reconnect when unrelated props/state change.

```typescript
function useLiveTicks() {
  const queryClient = useQueryClient();
  const onMessage = useEffectEvent((tick: Tick) => {
    queryClient.setQueryData(['ticks', tick.symbol], tick);
  });

  useEffect(() => {
    const ws = new WebSocket('/ws/ticks');
    ws.onmessage = (e) => onMessage(JSON.parse(e.data));
    return () => ws.close();
  }, []);
}
```

## Best Practices

- ✅ **Do:** Create Query Key factories so you don't misspell `['trades']` vs `['trade']` across files.
- ✅ **Do:** Set a global `staleTime` — the default is `0`, meaning a background refetch on every
  component remount. For live trading data use a short but non-zero staleTime (a few seconds) so
  rapid remounts don't hammer the API.
- ✅ **Do:** Use `queryClient.setQueryData` for socket-driven surgical updates; use
  `invalidateQueries` for everything else.
- ✅ **Do:** Abstract all `useMutation` and `useQuery` calls into custom hooks. Views only ever say
  `const { mutate } = useSquareOffPosition()`.
- ❌ **Don't:** Sync query data into local React state (`useEffect(() => setLocalState(data), [data])`).
  Use the query data directly; derive during render if needed.
- ❌ **Don't:** Optimistically update anything that represents a real order placement without a
  clear pending/confirming UI state — see `algo-trading-project` on friction matching risk.

## Troubleshooting

**Problem:** Infinite fetching loop in the network tab.
**Solution:** Check your `queryFn`. If it throws before returning, TanStack Query retries up to 3
times by default. Set `retry: false` for debugging.

**Problem:** `staleTime` vs `gcTime` confusion.
**Solution:** `staleTime` governs when a background refetch is triggered. `gcTime` governs how long
inactive data stays in memory after unmount. If `gcTime` < `staleTime`, data is deleted before it
even gets stale.

## Limitations
- Use this skill only when the task clearly matches the scope described above.
- Do not treat the output as a substitute for environment-specific validation, testing, or expert review.
- Stop and ask for clarification if required inputs, permissions, safety boundaries, or success criteria are missing.
