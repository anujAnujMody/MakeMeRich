import type { MarketData, Order, PlaceOrderPayload, PnLAnalysis, Position, Trade, TradeLogFilters } from '@/types'

// In dev (Vite HMR): default to localhost:5000
// In prod (nginx proxy): default to relative (same-origin through nginx /api/ route)
const DEV = import.meta.env.DEV
const API_BASE = import.meta.env.VITE_OPENALGO_URL ?? (DEV ? 'http://localhost:5000' : '')
const API_KEY = import.meta.env.VITE_OPENALGO_API_KEY ?? ''

function headers(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (API_KEY) h['x-api-key'] = API_KEY
  return h
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: headers() })
  if (!res.ok) throw new Error(`GET ${path}: ${res.status} ${res.statusText}`)
  return res.json()
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`POST ${path}: ${res.status} ${res.statusText}`)
  return res.json()
}

export const api = {
  baseUrl: API_BASE,

  // Market data
  marketData: {
    quotes: (symbol: string, exchange: string) => get<MarketData[]>(`/api/quotes?symbol=${symbol}&exchange=${exchange}`),
    history: (symbol: string, exchange: string, interval: string) =>
      get<MarketData[]>(`/api/history?symbol=${symbol}&exchange=${exchange}&interval=${interval}`),
  },

  // Orders
  orders: {
    list: () => get<Order[]>('/api/orders'),
    place: (payload: PlaceOrderPayload) => post<Order>('/api/placeorder', payload),
    cancel: (id: string) => post<{ success: boolean }>(`/api/cancelorder`, { id }),
  },

  // Positions
  positions: {
    list: () => get<Position[]>('/api/positions'),
    squareOff: (symbol: string, exchange: string) => post<{ success: boolean }>('/api/squareoff', { symbol, exchange }),
  },

  // Trade log
  trades: {
    list: (filters?: TradeLogFilters) => {
      const params = new URLSearchParams()
      if (filters?.dateFrom) params.set('from', filters.dateFrom)
      if (filters?.dateTo) params.set('to', filters.dateTo)
      if (filters?.strategy) params.set('strategy', filters.strategy)
      if (filters?.symbol) params.set('symbol', filters.symbol)
      const qs = params.toString()
      return get<Trade[]>(`/api/trades${qs ? `?${qs}` : ''}`)
    },
  },

  // PnL analysis
  pnl: {
    analysis: (from?: string, to?: string) => {
      const params = new URLSearchParams()
      if (from) params.set('from', from)
      if (to) params.set('to', to)
      const qs = params.toString()
      return get<PnLAnalysis>(`/api/pnl${qs ? `?${qs}` : ''}`)
    },
  },
}
