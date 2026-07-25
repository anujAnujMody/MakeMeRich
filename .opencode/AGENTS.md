# Algo Trading Tool — Project Context

## Hard Gate: Skill Invocation First

Before any action — including clarifying questions, research, or planning:

1. **Scan** available_skills list. Does any match current task? Even 1%?
2. **Load** matching skill via `skill` tool
3. **Follow** skill instructions exactly. No shortcuts.
4. **Only then** proceed.

**Must load when relevant:** git, react-expert, react-best-practices, shadcn, tanstack-query-expert, typescript-advanced, zustand-expert, python, algo-strategy, algo-options, agiprolabs-trading, stock-market-pro, data-science-python, machine-learning

## Skill Discipline

Loaded skill ≠ followed skill. Reference during implementation, not from memory.

### DO
- Re-read skill's relevant section before writing code it governs
- Run `grep` for skill-banned patterns on files before editing

### DON'T
- Implement from memory after loading a skill
- Skip re-reading because "I already know this"

### Rationalizations (Stop. These are traps.)

| Thought | Truth |
|---------|-------|
| "I just loaded it, I remember" | Memory drifts. Re-read the section. |
| "This is a small edit, no need" | Small edits create the pattern. Re-read. |
| "I know what forwardRef looks like" | You literally proved otherwise. Re-read. |

## Stack

| Layer | Tech | Notes |
|-------|------|-------|
| **Frontend** | React 19 + Vite SPA | `dashboard/` |
| **UI Components** | shadcn/ui + Tailwind v4 | Semantic colors, shadcn conventions |
| **State: Client** | Zustand 5 | Selected instrument, strategy params, UI state |
| **State: Server** | TanStack Query 5 | OpenAlgo API data (positions, orders, market data) |
| **Backend** | OpenAlgo Flask (unmodified) | 57 REST endpoints, 34 broker plugins |
| **Strategies** | Python 3.12+ | ORB, MA Cross, Straddle — run via OpenAlgo strategy host |
| **Engine** | Python 3.12+ | Executor + scheduler + self-learning ML |
| **Monorepo** | Yarn workspaces | `dashboard/` (React) + Python dirs outside yarn |
| **Language** | TypeScript strict | Path alias: `@/` → `dashboard/src/` |
| **Broker** | Angel One SmartAPI (via OpenAlgo) | Switch broker = change `BROKER` env var. Zero code changes. |

## Architecture: UI → Store → Hooks → API

```
Page/Component
  → Zustand store (client state: selected instrument, UI filters)
  → TanStack Query hook (server state: market data, orders, positions)
    → api/client.ts (OpenAlgo REST API wrapper)
```

- **No business logic in components.** Pages read from hooks, call mutations.
- **Zustand for client state only.** Selected symbol, timeframe, sidebar state.
- **TanStack Query for server state.** Market data, orders, positions, trade history.
- **WebSocket for real-time ticks.** Updates TanStack Query cache directly.

## Broker Abstraction

OpenAlgo abstracts all 34 brokers. Our code talks ONLY to OpenAlgo REST API.
Switching Angel One → Zerodha = change `BROKER` env var. Our dashboard + strategies = **zero changes**.

## Multi-Instrument (Day 1)

Configured in `strategies/config.py`. Phase 3 ML: predicts win probability per instrument, auto-selects top 1-2 daily.

## Branches

| Branch | What |
|--------|------|
| `feat/scaffolding` | .opencode/ + root monorepo + dirs |
| `feat/dashboard-hooks` | Zustand stores + TanStack hooks + API client |
| `feat/strategy-orbs` | ORB Python + multi-instrument config |
| `feat/strategy-insights` | React page: signals, PnL, trade log |
| `feat/executor` | Engine loop + scheduler + VPS deploy |
| `feat/self-learning` | Stats page + param optimizer |
| `feat/ml-engine` | RF model + instrument suggestion |

## Commands

```bash
# Dashboard (from W:\Trading)
cd dashboard && yarn dev        # Vite dev server
cd dashboard && yarn build      # Production build
yarn lint                       # Biome lint
yarn typecheck                  # tsc --noEmit

# Python strategies
python strategies/orbs/orbs.py  # Run ORB (test/paper mode)

# Engine
python engine/executor.py       # Main execution loop
```

## Coding Conventions

- **Named exports** preferred over default exports
- **File naming**: `camelCase.ts` for utilities, `PascalCase.tsx` for components
- **Imports**: `@/` alias for dashboard src
- **Styling**: shadcn conventions — `gap-*` not `space-y-*`, semantic colors, `cn()`
- **No inline styles**, **No `any` types**, **No secrets in code**
- **No business logic in UI** — components read hooks, call mutations

## See Plan

Full architecture, phase breakdown, and file tree: `docs/plan.md`
