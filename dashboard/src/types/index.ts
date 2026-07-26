export interface Instrument {
  symbol: string
  exchange: string
  token?: string
  expiry?: string
  strike?: number
  optionType?: string
}

export interface Position {
  symbol: string
  exchange: string
  quantity: number
  buyAvg: number
  sellAvg: number
  netQty: number
  netAvg: number
  m2m: number
  unrealisedPnl: number
  realisedPnl: number
  ltp: number
}

export type OrderStatus = 'OPEN' | 'PENDING' | 'COMPLETE' | 'CANCELLED' | 'REJECTED'
export type OrderType = 'MARKET' | 'LIMIT' | 'SL' | 'SL-M'
export type ProductType = 'MIS' | 'NRML' | 'CNC'
export type TransactionType = 'BUY' | 'SELL'

export interface Order {
  id: string
  symbol: string
  exchange: string
  transactionType: TransactionType
  quantity: number
  price: number
  triggerPrice: number
  status: OrderStatus
  orderType: OrderType
  productType: ProductType
  filledQty: number
  averagePrice: number
  createdAt: string
  updatedAt: string
  strategy?: string
}

export interface PlaceOrderPayload {
  symbol: string
  exchange: string
  transactionType: TransactionType
  quantity: number
  price: number
  triggerPrice?: number
  orderType: OrderType
  productType: ProductType
}

export interface Trade {
  id: string
  symbol: string
  exchange: string
  transactionType: TransactionType
  quantity: number
  price: number
  timestamp: string
  strategy: string
  pnl: number
  orderId: string
}

export interface MarketData {
  symbol: string
  exchange: string
  ltp: number
  open: number
  high: number
  low: number
  close: number
  volume: number
  change: number
  changePercent: number
  timestamp: string
}

export interface StrategyInstrument {
  symbol: string
  exchange: string
  rangeMin: number
  maxTrades: number
}

export interface StrategyConfig {
  name: string
  enabled: boolean
  instruments: StrategyInstrument[]
  params: Record<string, unknown>
}

export interface PnLAnalysis {
  totalPnl: number
  winRate: number
  totalTrades: number
  winningTrades: number
  losingTrades: number
  avgWin: number
  avgLoss: number
  maxDrawdown: number
  sharpe?: number
  period: {
    from: string
    to: string
  }
}

export interface Tick {
  symbol: string
  exchange: string
  ltp: number
  volume: number
  timestamp: string
  open: number
  high: number
  low: number
  close: number
}

export interface TradeLogFilters {
  dateFrom?: string
  dateTo?: string
  strategy?: string
  symbol?: string
}

export interface LearningStats {
  total_trades: number
  win_rate: number
  total_pnl: number
  avg_win: number
  avg_loss: number
  profit_factor: number
  max_drawdown: number
  sharpe: number
  winning_trades: number
  losing_trades: number
}

export interface LearningStatsResponse {
  overall: LearningStats
  by_strategy: Record<string, LearningStats>
  by_hour: Record<string, LearningStats>
  by_day: Record<string, LearningStats>
}

export interface ParamSuggestion {
  params: Record<string, number>
  score: number
  win_rate: number
  total_pnl: number
  sharpe: number
  total_trades: number
}

export interface OptimizeResponse {
  results: ParamSuggestion[]
}

export interface OptimizeRequest {
  strategy: string
  param_grid: Record<string, number[]>
}
