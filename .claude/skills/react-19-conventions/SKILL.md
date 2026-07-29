---
name: react-19-conventions
description: React 19 conventions for this codebase — React Compiler is enabled, so default to plain code with no manual memoization; only reach for useMemo/useCallback/React.memo after profiling shows a real hotspot. Also covers ref-as-prop (no forwardRef) and the use() API, and flags which React 19 APIs don't apply here. Use when writing or reviewing components/hooks in dashboard/src.
license: Project-internal
---

# React 19 Conventions

This project runs React 19.2 with the **React Compiler enabled** —
`dashboard/vite.config.ts`: `babel({ presets: [reactCompilerPreset()] })`, via
`babel-plugin-react-compiler`. `react/rules-of-hooks` is enforced as an
oxlint error (`.oxlintrc.json`), which is what keeps the compiler's
optimizations safe — there's no separate `eslint-plugin-react-compiler` here,
oxlint's rule covers the same ground.

## Default: no manual memoization

The compiler auto-memoizes every component and hook it can see into. Per the
official docs:

> "React Compiler automatically memoizes values and functions, reducing the
> need for manual useMemo/useCallback calls." — react.dev/reference/react/useMemo
>
> "You should only rely on useMemo as a performance optimization. If your
> code doesn't work without it, find the underlying problem and fix it
> first." — react.dev/reference/react/useMemo

Write plain code. Do not wrap components in `React.memo`, computations in
`useMemo`, or callbacks in `useCallback` by default — the compiler already
does this at build time. Adding them anyway is not neutral: it's dead
weight that makes the diff harder to read for no measured benefit.

## The three cases where manual memoization is still legitimate

Per the official docs, even with the compiler on:

1. **Expensive computation the compiler can't see into** — the compiler only
   memoizes component and hook bodies, not arbitrary functions. A heavy
   calculation called from a plain (non-component, non-hook) utility isn't
   covered.
2. **A value crossing into un-compiled code** — a third-party component the
   compiler doesn't process, or a component explicitly opted out with
   `"use no memo"`.
3. **A confirmed hotspot** — measured with the React DevTools Profiler
   ("Why did this render?"), where the actual fix is memoization rather than
   an architectural issue (state lifted too high, an Effect that shouldn't
   exist, a non-memoized JSX child passed down).

If none of these apply — the common case in this codebase — don't add it.
This was gotten wrong once already this session: `useMemo` was added to
`PerformancePage.tsx` and `useStrategiesWithConfig.ts` during a cleanup pass
and had to be reverted for exactly this reason.

## ref as a prop — no forwardRef

React 19 accepts `ref` as a plain prop; `forwardRef` is deprecated. Already
the convention throughout `src/components/ui/*` (shadcn) — no `forwardRef`
anywhere in this codebase. Keep it that way:

```tsx
function Input({ ref, ...props }: React.ComponentProps<'input'>) {
  return <input ref={ref} {...props} />
}
```

## use() and Context-as-provider

`use()` reads a Promise or Context during render. Unlike a real Hook, it can
be called conditionally or inside a loop. Requirements: a `<Suspense>`
boundary above it for Promises, and the Promise must be a stable/cached
reference — one created fresh in the render body re-triggers the Suspense
fallback on every render instead of resolving.

Already used correctly for the Context case:
`src/components/DecisionCard/DecisionCard.tsx` reads its compound-component
context with `use(DecisionCardContext)` instead of `useContext`, and pairs it
with React 19's Context-as-provider syntax — `<DecisionCardContext
value={...}>`, not `<DecisionCardContext.Provider value={...}>`. It's the
only `createContext` in the app; keep new context providers on this pattern,
no `.Provider`.

The Promise-reading half of `use()` isn't used anywhere yet (all data
fetching goes through TanStack Query — see `tanstack-query-expert`), but
it's the right tool if a future feature needs to unwrap a Promise directly
during render.

## Not applicable to this project

React 19 also shipped `useActionState`, `useFormStatus`, `useOptimistic`,
Server Components, and Server Actions — all built around server-rendered
forms and mutations (Next.js App Router or similar). This is a Vite SPA with
no server-rendering layer; every mutation here goes through TanStack Query's
`useMutation` (see `tanstack-query-expert`), not React 19 Actions. Don't
reach for these APIs in this codebase.
