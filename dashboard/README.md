# Algo Trader Dashboard

AI-driven trading platform with 7-agent system for Indian F&O markets. Supports paper + live trading via OpenAlgo.

## Quick Start

```bash
# Install
yarn install

# Dev (with MSW mocks, no backend needed)
yarn dev

# Test
yarn test

# Build
yarn build
```

Open http://localhost:5173 — login with any API key (mocks active in dev).

## Backend Setup

The dashboard has no build-time env vars of its own — it talks to the engine
purely through relative `/api/*` calls, proxied by nginx in Docker (or MSW
mocks in `yarn dev`). Backend configuration lives at the repo root: copy
`W:\Trading\.env.example` to `W:\Trading\.env` and fill it in — see that
file's comments for what's required.

## Docker

```bash
docker compose up -d
```

Runs on http://localhost:8080 with nginx proxying API calls.

## Architecture

- **Frontend**: React 19, TypeScript, Tailwind CSS 4, Recharts
- **State**: Zustand 5 (persisted stores), TanStack Query 5 (server state)
- **Mocks**: MSW 2 (dev only, auto-disabled in production)
- **Auth**: API-key gate (localStorage, no backend dependency)
- **AI Agents**: 7-agent system (Research → Scanner → Discovery → Validator → Risk → Execution → Learning)

## Project Structure

```
src/
├── agents/       # AI agent implementations
├── components/   # UI components (shadcn/ui style)
├── hooks/        # TanStack Query wrappers + business logic
├── lib/          # API client, utilities
├── mocks/        # MSW handlers + test utilities
├── pages/        # Route pages
├── stores/       # Zustand stores
└── types/        # TypeScript types
```

## Scripts

| Command | Description |
|---------|-------------|
| `yarn dev` | Dev server with MSW mocks |
| `yarn build` | TypeScript check + Vite build |
| `yarn test` | Run tests |
| `yarn lint` | Oxlint |
| `yarn preview` | Preview production build |
