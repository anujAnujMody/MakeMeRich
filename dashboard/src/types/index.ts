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

export interface TradeLogFilters {
  dateFrom?: string
  dateTo?: string
  strategy?: string
  symbol?: string
}

export interface EngineStats {
  total_trades: number
  win_rate: number
  avg_profit: number
  sharpe: number
  profit_factor: number
  max_drawdown: number
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

export interface DashboardData {
  dayPnl: number
  dayPnlPercent: number
  winRate: number
  totalTrades: number
  activePositions: number
  positions: Position[]
  quotes: MarketData[]
}

export interface EquityPoint {
  date: string
  value: number
}

export interface DailyPnL {
  date: string
  pnl: number
  trades: number
}

export interface MarketSession {
  status: 'open' | 'closed' | 'pre-open' | 'post-closed'
  label: string
  nextEvent: string
  currentTime: string
}

export interface WatchlistItem {
  symbol: string
  exchange: string
  ltp: number
  change: number
  changePercent: number
}

export interface JournalEntry {
  date: string
  notes: string
  emotion: string
  tags: string[]
  /** Server-derived from that day's actual closed trades — the client never
   * invents this value. Absent on a freshly-saved entry until the backend
   * computes it. */
  pnl?: number
}

export interface RejectedOrder extends Order {
  reason: string
}

export interface BrokerStatus {
  connected: boolean
  name: string
  latency: number
  lastPing: string
  lastSync: string
  ordersToday: number
  apiCalls: number
}

// TradingMode lives in @/types/dashboard-snapshot ('dry-run' | 'live') — this
// file used to export a second, conflicting 'paper' | 'live' union. Deleted;
// ModeState was dead code (modeStore.ts defines its own local interface).

/* ─── AI Agent System Types ─── */

export type AutonomyMode = 'full-auto' | 'semi-auto'

export interface AgentConfig {
  reasoningModel: string
  cheapModel: string
  fallbackModel: string
  openrouterKey: string
  researchEnabled: boolean
  researchTime: string
  autonomyMode: AutonomyMode
}

export interface ResearchBrief {
  date?: string
  timestamp?: string
  sentiment?: string
  summary: string
  confidence: string | number
  details: string
  source?: string
}

export interface StrategyCard {
  id: string
  name: string
  description: string
  active: boolean
  winRate: number
  weeklyPnl: number
  confidence: number
  confidenceTrend: 'up' | 'down' | 'stable'
  totalTrades: number
  paused: boolean
  pauseReason?: string
  lastActive: string
  rule: string
}

export interface TradeExplanation {
  tradeId: string
  strategy: string
  symbol: string
  transactionType: TransactionType
  quantity: number
  entryPrice: number
  exitPrice: number
  pnl: number
  entryTime: string
  exitTime: string
  aiConfidenceAtEntry: number
  aiReason: string
  mlLearned?: string
}

export interface DailyRecap {
  date: string
  totalPnl: number
  strategies: { name: string; pnl: number; wins: number; losses: number }[]
  mlLesson: string
}

export interface PatternLibraryEntry {
  pattern: string
  condition: string
  winRate: number
  tradesTested: number
  lastObserved: string
  status: 'working' | 'mixed' | 'no-edge'
}

export interface LearningProgress {
  winRateTrend: number[]
  totalStrategiesDiscovered: number
  totalStrategiesRetired: number
  avgProfitPerTrade: number
  dates: string[]
}

export type TrainingResults =
  | { status: 'no_training_results' }
  | {
      status?: 'ok'
      accuracy: number
      feature_importance: Record<string, number>
      walk_forward: {
        oos_sharpe: number
        profit_factor: number
        total_trades: number
      }
      total_samples: number
      win_rate_pct: number
    }

export interface DiscoveryQueueItem {
  name: string
  progress: number
  status: 'testing' | 'validating' | 'ready'
}

export type AgentName = 'research' | 'scanner' | 'discovery' | 'validator' | 'risk' | 'execution' | 'learning'

export interface AgentStatus {
  name: AgentName
  label: string
  status: 'idle' | 'running' | 'success' | 'error'
  lastRun: string | null
  message?: string
}

/* ─── Execution / Paper Trading Types ─── */

export interface ExecutionStatus {
  running: boolean
  check_interval_secs: number
  ml_threshold: number
  started_at: string | null
}

export interface PaperTrade {
  id: string
  strategy: string
  symbol: string
  direction: string
  entry_price: number
  exit_price: number | null
  quantity: number
  pnl: number | null
  outcome: string | null
  entry_time: string
  exit_time: string | null
  ml_confidence: number | null
  type: string | null
}

export interface PaperPositionCount {
  count: number
}

export interface SkippedSignalInfo {
  id: number
  strategy: string
  symbol: string
  direction: string
  entry_price: number
  ml_confidence: number
  ml_threshold: number
  reason: string
  timestamp: string
}

/* ─── Pipeline / Cycle Status Types ─── */

export type PipelineStage = 'idle' | 'fetching' | 'analyzing' | 'scoring' | 'deciding' | 'exiting'

export interface CycleStatus {
  stage: PipelineStage
  running: boolean
  cycle_start: string | null
  next_cycle_in_secs: number
  instruments_processed: number
  instruments_total: number
  signals_generated: number
  trades_placed: number
  trades_skipped: number
  last_error: string | null
  history?: CycleHistoryEntry[]
}

export interface CycleHistoryEntry {
  timestamp: string
  duration_secs: number
  instruments_processed: number
  instruments_total: number
  signals_generated: number
  trades_placed: number
  trades_skipped: number
  error: string | null
}

/* ─── Strategy Analysis Types ─── */

export interface StrategyCondition {
  label: string
  met: boolean
}

export interface StrategyComboAnalysis {
  strategy: string
  symbol: string
  current_price?: number
  error?: string
  reason?: string
  signal?: string | null
  // ORBS fields
  range_high?: number
  range_low?: number
  range_mid?: number
  buffer?: number
  breakout_level?: number
  breakdown_level?: number
  is_breakout?: boolean
  is_breakdown?: boolean
  range_pct?: number
  // VWAP fields
  vwap?: number
  deviation_pct?: number
  rsi?: number
  deviation_threshold?: number
  rsi_oversold?: number
  rsi_overbought?: number
  is_long?: boolean
  is_short?: boolean
  // Shared
  conditions?: StrategyCondition[]
}

export interface StrategyAnalysis {
  snapshot: Record<string, number>
  vix: number
  quotes: { symbol: string; ltp: number; change: number; changePercent: number }[]
  combos: StrategyComboAnalysis[]
  timestamp: string
}

export interface MLInfo {
  status: string
  accuracy: number
  threshold: number
  samples: number
  trained_on: string
  walk_forward_sharpe: number
  profit_factor: number
  indices: { symbol: string; status: string; bars: number; features: number; pos_pct: number }[]
}

/* ─── Signal Feed Types ─── */

export interface SignalFeedItem {
  id: string
  type: 'placed' | 'skipped'
  strategy: string
  symbol: string
  direction: string
  entry_price: number
  quantity?: number
  ml_confidence: number
  reason?: string
  timestamp: string
}
