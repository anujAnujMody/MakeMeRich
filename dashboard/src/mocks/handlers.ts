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

  http.get('*/api/learning/stats', () => HttpResponse.json({
    overall: { total_trades: 45, win_rate: 62.22, total_pnl: 12500, avg_win: 850, avg_loss: -420, profit_factor: 1.85, max_drawdown: -3200, sharpe: 1.45, winning_trades: 28, losing_trades: 17 },
    by_strategy: { orbs: { total_trades: 30, win_rate: 66.67, total_pnl: 9800, avg_win: 750, avg_loss: -380, profit_factor: 1.92, max_drawdown: -2100, sharpe: 1.52, winning_trades: 20, losing_trades: 10 } },
    by_hour: { 9: { total_trades: 25, win_rate: 68.0, total_pnl: 7200, avg_win: 800, avg_loss: -350, profit_factor: 2.1, max_drawdown: -1500, sharpe: 1.6, winning_trades: 17, losing_trades: 8 }, 10: { total_trades: 15, win_rate: 53.33, total_pnl: 3800, avg_win: 700, avg_loss: -450, profit_factor: 1.5, max_drawdown: -1200, sharpe: 1.1, winning_trades: 8, losing_trades: 7 } },
    by_day: { Monday: { total_trades: 10, win_rate: 60.0, total_pnl: 2800, avg_win: 800, avg_loss: -400, profit_factor: 1.6, max_drawdown: -800, sharpe: 1.3, winning_trades: 6, losing_trades: 4 }, Tuesday: { total_trades: 12, win_rate: 66.67, total_pnl: 4200, avg_win: 900, avg_loss: -380, profit_factor: 2.0, max_drawdown: -900, sharpe: 1.6, winning_trades: 8, losing_trades: 4 } },
  })),

  http.post('*/api/learning/optimize', () => HttpResponse.json({
    results: [
      { params: { target_rr: 1.5, sl_rr: 1.0 }, score: 85.4, win_rate: 66.67, total_pnl: 9800, sharpe: 1.52, total_trades: 30 },
      { params: { target_rr: 1.0, sl_rr: 1.0 }, score: 72.1, win_rate: 60.0, total_pnl: 7200, sharpe: 1.3, total_trades: 30 },
    ],
  })),
]
