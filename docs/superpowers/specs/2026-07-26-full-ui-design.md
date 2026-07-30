# Phase 7: Full UI — Design Spec

## Overview

Replace all placeholder routes in the Algo Trader dashboard with real, data-driven pages using MSW mock data. Establish a professional dark-mode fintech design system and implement 4 new pages: DashboardHome, StrategiesPage, OrdersPage, PositionsPage.

## Design System

### Colors (Tailwind v4 oklch)

| Token | Hex | Usage |
|-------|-----|-------|
| `--bg-primary` | #0f1118 | Page background |
| `--bg-surface` | #1a1d27 | Cards, dialogs, sidebar |
| `--bg-surface-hover` | #23273a | Hover states |
| `--border-subtle` | #2a2e3f | Dividers, borders |
| `--text-primary` | #f1f5f9 | Body text |
| `--text-secondary` | #94a3b8 | Secondary text |
| `--text-muted` | #64748b | Placeholder, disabled |
| `--accent-blue` | #3b82f6 | Primary actions, links |
| `--accent-emerald` | #22c55e | Profit, buy, positive |
| `--accent-red` | #ef4444 | Loss, sell, negative |
| `--accent-amber` | #f59e0b | Warnings, pending |

### Typography

- Body: Inter (system sans-serif), 14px base
- Monospace: JetBrains Mono (prices, qty, P&L)
- Scale: 12/14/16/18/20/24/30/36

### Effects

- Cards: `shadow-lg` + `ring-1 ring-white/5`
- Hover transitions: 150ms ease-out
- Badges: pill-shaped, semantic color
- Tabular figures on all numeric data

## Layout

Sidebar: 60px collapsed icons, 220px expanded on hover. Top bar with breadcrumb, dark/light toggle, connection status. Body scrolls independently.

Responsive: <768px → sidebar becomes bottom tab bar.

## Pages

### DashboardHome (`/`)
- Summary strip (4 StatCards: Day P&L, Win Rate, Trades, Active Pos)
- Market Overview table (symbols, LTP, change%)
- Active Positions compact list
- P&L area chart (last 30 days)

### StrategiesPage (`/strategies`)
- Strategy cards with enable/disable toggle
- Expandable instrument table + params
- Edit config dialog per strategy

### OrdersPage (`/orders`)
- Order form: symbol, exchange, type, BUY/SELL toggle, qty, price
- Order history table with status badges (OPEN/COMPLETE/CANCELLED/REJECTED)
- Cancel button on open orders
- Form validation + toast feedback

### PositionsPage (`/positions`)
- Summary bar (open count, net P&L)
- Position table with monospaced numbers, colored P&L
- Square off per row + Square Off All
- Confirmation dialog before square off

## Architecture

Pages/Components → Hooks (TanStack Query) → API client → OpenAlgo REST

- Business logic: ZERO in UI layer
- Mock data: MSW handlers, seeded realistic data
- State: Zustand for UI state only (sidebar, theme, filters)
- Data fetching: TanStack Query with polling where appropriate

## TDD

Every page: render test, mock data test, empty state, error state, navigation.
Per plan.md: MSW mock data, `screen.findByText`, `screen.getByText`.

## Implementation Order

1. CSS foundation (design tokens)
2. Layout redesign (sidebar + topbar)
3. Mock data + useDashboard hook
4. DashboardHome
5. StrategiesPage
6. OrdersPage
7. PositionsPage
8. Route replacement in App.tsx
