# Algo Trading Tool

Multi-instrument algo trading dashboard with ORB strategy, self-learning ML engine, and broker-agnostic OpenAlgo integration.

## Stack

- **Frontend:** React 19 + TypeScript + shadcn/ui + Zustand + TanStack Query
- **Backend:** OpenAlgo REST API (broker abstraction layer)
- **Engine:** Python (strategies, executor, ML)
- **Monorepo:** Yarn workspaces

## Quick Start

```bash
# Install deps
yarn install

# Start dashboard
yarn workspace dashboard dev

# Start OpenAlgo
docker compose up -d
```
