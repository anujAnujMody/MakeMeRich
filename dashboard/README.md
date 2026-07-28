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

Copy `.env.example` to `.env` and configure:

```env
VITE_OPENALGO_URL=http://localhost:5000
VITE_OPENALGO_API_KEY=your-key
VITE_ENGINE_URL=http://localhost:8000
```

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
