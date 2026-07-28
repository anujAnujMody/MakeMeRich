import type { MarketData, Position, Order, DashboardData, EquityPoint, DailyPnL, WatchlistItem, JournalEntry, BrokerStatus, RejectedOrder } from '@/types'

export const mockQuotes: MarketData[] = [
  { symbol: 'BANKNIFTY', exchange: 'NFO', ltp: 48200, open: 48000, high: 48350, low: 47900, close: 47950, volume: 125000, change: 250, changePercent: 0.52, timestamp: new Date().toISOString() },
  { symbol: 'NIFTY', exchange: 'NFO', ltp: 24100, open: 24000, high: 24150, low: 23950, close: 24000, volume: 98000, change: 100, changePercent: 0.42, timestamp: new Date().toISOString() },
  { symbol: 'FINNIFTY', exchange: 'NFO', ltp: 18500, open: 18400, high: 18550, low: 18380, close: 18400, volume: 45000, change: 100, changePercent: 0.54, timestamp: new Date().toISOString() },
  { symbol: 'SENSEX', exchange: 'BFO', ltp: 62500, open: 62000, high: 62600, low: 61900, close: 62000, volume: 35000, change: 500, changePercent: 0.81, timestamp: new Date().toISOString() },
]

export const mockPositions: Position[] = [
  { symbol: 'BANKNIFTY', exchange: 'NFO', quantity: 15, buyAvg: 48200, sellAvg: 0, netQty: 15, netAvg: 48200, m2m: 0, unrealisedPnl: 750, realisedPnl: 0, ltp: 48350 },
  { symbol: 'NIFTY', exchange: 'NFO', quantity: -25, buyAvg: 0, sellAvg: 24100, netQty: -25, netAvg: 24100, m2m: 0, unrealisedPnl: -300, realisedPnl: 0, ltp: 24000 },
]

export const mockOrders: Order[] = [
  { id: 'o1', symbol: 'BANKNIFTY', exchange: 'NFO', transactionType: 'BUY', quantity: 15, price: 48200, triggerPrice: 0, status: 'COMPLETE', orderType: 'LIMIT', productType: 'MIS', filledQty: 15, averagePrice: 48200, createdAt: '2026-07-26T09:15:00Z', updatedAt: '2026-07-26T09:15:05Z', strategy: 'ORBS' },
  { id: 'o2', symbol: 'BANKNIFTY', exchange: 'NFO', transactionType: 'SELL', quantity: 15, price: 48350, triggerPrice: 0, status: 'COMPLETE', orderType: 'LIMIT', productType: 'MIS', filledQty: 15, averagePrice: 48350, createdAt: '2026-07-26T09:45:00Z', updatedAt: '2026-07-26T09:45:06Z', strategy: 'ORBS' },
  { id: 'o3', symbol: 'NIFTY', exchange: 'NFO', transactionType: 'BUY', quantity: 25, price: 24100, triggerPrice: 0, status: 'OPEN', orderType: 'MARKET', productType: 'MIS', filledQty: 0, averagePrice: 0, createdAt: '2026-07-26T10:00:00Z', updatedAt: '2026-07-26T10:00:00Z', strategy: 'ORBS' },
  { id: 'o4', symbol: 'FINNIFTY', exchange: 'NFO', transactionType: 'SELL', quantity: 25, price: 18550, triggerPrice: 0, status: 'PENDING', orderType: 'LIMIT', productType: 'MIS', filledQty: 0, averagePrice: 0, createdAt: '2026-07-26T10:30:00Z', updatedAt: '2026-07-26T10:30:00Z' },
  { id: 'o5', symbol: 'SENSEX', exchange: 'BFO', transactionType: 'BUY', quantity: 10, price: 62100, triggerPrice: 62000, status: 'REJECTED', orderType: 'SL', productType: 'NRML', filledQty: 0, averagePrice: 0, createdAt: '2026-07-25T14:00:00Z', updatedAt: '2026-07-25T14:00:02Z' },
]

export const mockDashboard: DashboardData = {
  dayPnl: 1250,
  dayPnlPercent: 2.3,
  winRate: 66.7,
  totalTrades: 3,
  activePositions: 2,
  positions: mockPositions,
  quotes: mockQuotes,
}

export const mockEquityCurve: EquityPoint[] = Array.from({ length: 30 }).map((_, i) => ({
  date: new Date(2026, 6, 1 + i).toISOString().split('T')[0],
  value: 100000 + Math.sin(i * 0.5) * 5000 + i * 400 + Math.round(Math.random() * 2000 - 1000),
}))

export const mockDailyPnL: DailyPnL[] = Array.from({ length: 30 }).map((_, i) => ({
  date: new Date(2026, 6, 1 + i).toISOString().split('T')[0],
  pnl: Math.round(Math.random() * 2000 - 800),
  trades: Math.floor(Math.random() * 5) + 1,
}))

export const mockWatchlist: WatchlistItem[] = [
  { symbol: 'BANKNIFTY', exchange: 'NFO', ltp: 48200, change: 250, changePercent: 0.52 },
  { symbol: 'NIFTY', exchange: 'NFO', ltp: 24100, change: 100, changePercent: 0.42 },
  { symbol: 'FINNIFTY', exchange: 'NFO', ltp: 18500, change: 100, changePercent: 0.54 },
  { symbol: 'SENSEX', exchange: 'BFO', ltp: 62500, change: 500, changePercent: 0.81 },
]

export const mockJournalEntries: JournalEntry[] = [
  { date: '2026-07-25', notes: 'Good momentum day, followed ORBS signals well', emotion: '😀 Confident', tags: ['obedient', 'good-setup'], pnl: 1250 },
  { date: '2026-07-24', notes: 'Took one extra trade, broke max rule', emotion: '😐 Neutral', tags: ['rule-break', 'overtrade'], pnl: -300 },
  { date: '2026-07-23', notes: 'Clean execution, early exit cost some profit', emotion: '🙂 Calm', tags: ['early-exit'], pnl: 750 },
]

export const mockBrokerStatus: BrokerStatus = {
  connected: true,
  name: 'Angel One',
  latency: 45,
  lastPing: new Date().toISOString(),
  lastSync: new Date().toISOString(),
  ordersToday: 12,
  apiCalls: 847,
}

export const mockRejectedOrders: RejectedOrder[] = [
  { ...mockOrders[4], reason: 'Insufficient margin' },
]
