import { http, HttpResponse } from 'msw'
import type { StrategyConfig, Trade, PnLAnalysis } from '@/types'

export const mockStrategies: StrategyConfig[] = [
  {
    name: 'ORBS',
    enabled: true,
    instruments: [
      { symbol: 'BANKNIFTY', exchange: 'NFO', rangeMin: 15, maxTrades: 2 },
      { symbol: 'NIFTY', exchange: 'NFO', rangeMin: 15, maxTrades: 2 },
    ],
    params: { target_rr: 1.5, sl_rr: 1.0, time_exit_hour: 15, time_exit_minute: 15, min_range_pts: 20, max_range_pts: 200, volume_multiplier: 1.5, max_per_direction: 1 },
  },
  {
    name: 'MACROSS',
    enabled: false,
    instruments: [
      { symbol: 'BANKNIFTY', exchange: 'NFO', rangeMin: 10, maxTrades: 3 },
    ],
    params: { fast: 10, slow: 30 },
  },
]

export const mockTrades: Trade[] = [
  { id: 't1', symbol: 'BANKNIFTY', exchange: 'NFO', transactionType: 'BUY', quantity: 15, price: 48200, timestamp: '2026-07-25T09:20:00Z', strategy: 'ORBS', pnl: 750, orderId: 'o1' },
  { id: 't2', symbol: 'BANKNIFTY', exchange: 'NFO', transactionType: 'SELL', quantity: 15, price: 48350, timestamp: '2026-07-25T09:45:00Z', strategy: 'ORBS', pnl: 0, orderId: 'o2' },
  { id: 't3', symbol: 'NIFTY', exchange: 'NFO', transactionType: 'BUY', quantity: 25, price: 24100, timestamp: '2026-07-25T10:00:00Z', strategy: 'ORBS', pnl: -300, orderId: 'o3' },
]

export const mockPnL: PnLAnalysis = {
  totalPnl: 450,
  winRate: 66.67,
  totalTrades: 3,
  winningTrades: 2,
  losingTrades: 1,
  avgWin: 750,
  avgLoss: 300,
  maxDrawdown: 300,
  sharpe: 1.2,
  period: { from: '2026-07-01', to: '2026-07-25' },
}

export const handlers = [
  http.get('*/api/strategies', () => HttpResponse.json(mockStrategies)),

  http.get('*/api/trades', ({ request }) => {
    const url = new URL(request.url)
    const strategy = url.searchParams.get('strategy')
    const symbol = url.searchParams.get('symbol')
    let filtered = [...mockTrades]
    if (strategy) filtered = filtered.filter((t) => t.strategy === strategy)
    if (symbol) filtered = filtered.filter((t) => t.symbol === symbol)
    return HttpResponse.json(filtered)
  }),

  http.get('*/api/pnl', () => HttpResponse.json(mockPnL)),
]
