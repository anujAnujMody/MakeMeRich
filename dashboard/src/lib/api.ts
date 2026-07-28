import type { CycleStatus, DashboardData, DailyPnL, DiscoveryQueueItem, EngineStats, EquityPoint, ExecutionStatus, JournalEntry, LearningProgress, MarketData, MarketSession, Order, OptimizeRequest, OptimizeResponse, PaperTrade, PaperPositionCount, PnLAnalysis, PlaceOrderPayload, Position, RejectedOrder, SignalFeedItem, SkippedSignalInfo, StrategiesFile, StrategyCard, StrategyConfig, StrategyAnalysis, MLInfo, Trade, TradeLogFilters, TrainingResults, WatchlistItem, BrokerStatus, ResearchBrief, DailyRecap, PatternLibraryEntry } from '@/types'
import type { CycleEvaluation, DashboardSnapshot, TradingMode } from '@/types/dashboard-snapshot'
import type { PendingApproval } from '@/types/approval'
import type { MaturityGateStatus, ShadowComparison } from '@/types/learning'
import type { EngineHealthStatus } from '@/types/ops'

function headers(): Record<string, string> {
  return { 'Content-Type': 'application/json' }
}

/** Builds a `?a=1&b=2`-style suffix (empty string if nothing is set),
 * percent-encoding every value via URLSearchParams. */
function qs(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value)
  }
  const s = search.toString()
  return s ? `?${s}` : ''
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: headers() })
  if (!res.ok) throw new Error(`GET ${path}: ${res.status} ${res.statusText}`)
  return res.json()
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`POST ${path}: ${res.status} ${res.statusText}`)
  return res.json()
}

/** Shape actually seen from `/api/agents/strategies` — a partial
 * `StrategyCard` plus a couple of legacy/alternate field names the real
 * engine may still send (`accuracy`, `total_trades`). */
type RawStrategyCard = Partial<StrategyCard> & {
  accuracy?: number
  total_trades?: number
}

function normalizeStrategyCard(c: RawStrategyCard, defaults: { active: boolean; paused: boolean }): StrategyCard {
  const derivedPct = c.confidence ?? (c.accuracy != null ? Math.round(c.accuracy * 100) : 0)
  return {
    id: c.id ?? c.name ?? '',
    name: c.name ?? '',
    description: c.description ?? '',
    active: c.active ?? defaults.active,
    winRate: c.winRate ?? derivedPct,
    weeklyPnl: c.weeklyPnl ?? 0,
    confidence: derivedPct,
    confidenceTrend: c.confidenceTrend ?? 'stable',
    // `totalTrades` is the current field name; `total_trades` is kept as a
    // fallback in case a future real-engine response still sends snake_case.
    totalTrades: c.totalTrades ?? c.total_trades ?? 0,
    paused: c.paused ?? defaults.paused,
    pauseReason: c.pauseReason,
    lastActive: c.lastActive ?? '',
    rule: c.rule ?? c.description ?? '',
  }
}

export const api = {
  mode: {
    get: () => get<{ mode: TradingMode }>('/api/mode'),
    set: (mode: TradingMode) => post<{ mode: TradingMode }>('/api/mode', { mode }),
  },

  strategies: {
    list: () => get<StrategyConfig[]>('/api/strategies'),
  },

  dashboard: {
    data: () => get<DashboardData>('/api/dashboard'),
    snapshot: () => get<DashboardSnapshot>('/api/dashboard/snapshot'),
  },

  decisions: {
    today: () => get<CycleEvaluation[]>('/api/decisions/today'),
  },

  approvals: {
    list: () => get<PendingApproval[]>('/api/approvals'),
    decide: (id: string, decision: 'approve' | 'reject') =>
      post<PendingApproval>('/api/approvals/decide', { id, decision }),
  },

  marketData: {
    quotes: (symbol: string, exchange: string) => get<MarketData[]>(`/api/quotes${qs({ symbol, exchange })}`),
    history: (symbol: string, exchange: string, interval: string) =>
      get<MarketData[]>(`/api/history${qs({ symbol, exchange, interval })}`),
  },

  orders: {
    list: () => get<Order[]>('/api/orders'),
    place: (payload: PlaceOrderPayload) => post<Order>('/api/orders/place', payload),
    cancel: (id: string) => post<{ success: boolean }>('/api/orders/cancel', { id }),
  },

  positions: {
    list: () => get<Position[]>('/api/positions'),
    squareOff: (symbol: string, exchange: string) => post<{ success: boolean }>('/api/positions/squareoff', { symbol, exchange }),
  },

  trades: {
    list: (filters?: TradeLogFilters) =>
      get<Trade[]>(`/api/trades${qs({ from: filters?.dateFrom, to: filters?.dateTo, strategy: filters?.strategy, symbol: filters?.symbol })}`),
  },

  pnl: {
    analysis: (from?: string, to?: string) => get<PnLAnalysis>(`/api/pnl${qs({ from, to })}`),
  },

  equityCurve: {
    list: (from?: string, to?: string) => get<EquityPoint[]>(`/api/equity-curve${qs({ from, to })}`),
  },

  dailyPnL: {
    list: (month?: string) => get<DailyPnL[]>(`/api/daily-pnl${qs({ month })}`),
  },

  watchlist: {
    list: () => get<WatchlistItem[]>('/api/watchlist'),
  },

  marketStatus: {
    get: () => get<MarketSession>('/api/market-status'),
  },

  journal: {
    list: (tag?: string) => get<JournalEntry[]>(`/api/journal${qs({ tag })}`),
    save: (entry: JournalEntry) => post<JournalEntry>('/api/journal/save', entry),
  },

  broker: {
    status: () => get<BrokerStatus>('/api/broker-status'),
  },

  engine: {
    health: () => get<EngineHealthStatus>('/api/engine/health'),
    pause: () => post<EngineHealthStatus>('/api/engine/pause', {}),
    resume: () => post<EngineHealthStatus>('/api/engine/resume', {}),
    resetDrawdownBreaker: () => post<EngineHealthStatus>('/api/engine/reset-drawdown-breaker', {}),
  },

  rejectedOrders: {
    list: () => get<RejectedOrder[]>('/api/rejected-orders'),
  },

  agents: {
    research: () => get<ResearchBrief>('/api/agents/research'),
    strategies: async () => {
      const raw = await get<{ active: RawStrategyCard[]; inactive: RawStrategyCard[]; queue: DiscoveryQueueItem[] }>('/api/agents/strategies')
      return {
        active: (raw.active ?? []).map((c) => normalizeStrategyCard(c, { active: true, paused: false })),
        inactive: (raw.inactive ?? []).map((c) => normalizeStrategyCard(c, { active: false, paused: true })),
        queue: raw.queue ?? [],
      }
    },
    recap: (date?: string) => get<DailyRecap>(`/api/agents/recap${qs({ date })}`),
    learning: () => get<LearningProgress>('/api/agents/learning'),
    patterns: () => get<PatternLibraryEntry[]>('/api/agents/patterns'),
    setPaused: (id: string, paused: boolean) =>
      post<StrategyCard | null>('/api/agents/strategies/pause', { id, paused }),
  },

  learning: {
    stats: (strategy?: string, symbol?: string) => get<EngineStats>(`/api/learning/stats${qs({ strategy, symbol })}`),
    trainingResults: () => get<TrainingResults>('/api/learning/training-results'),
    maturityGate: () => get<MaturityGateStatus>('/api/learning/maturity-gate'),
    shadowComparisons: () => get<ShadowComparison[]>('/api/learning/shadow-comparisons'),
    retrain: () => post<{ status: string; trades_used: number; accuracy: number; top_features: number[] }>('/api/learning/retrain', {}),
    optimize: (payload: OptimizeRequest) =>
      post<OptimizeResponse>('/api/learning/optimize', payload),
  },

  execution: {
    status: () => get<ExecutionStatus>('/api/execution/status'),
    cycle: () => get<CycleStatus>('/api/execution/cycle'),
    trades: (limit = 100) => get<PaperTrade[]>(`/api/execution/trades?limit=${limit}`),
    positions: () => get<PaperPositionCount>('/api/execution/positions'),
    skipped: (limit = 100) => get<SkippedSignalInfo[]>(`/api/execution/skipped?limit=${limit}`),
    start: () => post<{ status: string }>('/api/execution/start', {}),
    stop: () => post<{ status: string }>('/api/execution/stop', {}),
    signalFeed: (limit = 50) => get<SignalFeedItem[]>(`/api/execution/signal-feed?limit=${limit}`),
    analysis: () => get<StrategyAnalysis>('/api/execution/analysis'),
    mlInfo: () => get<MLInfo>('/api/execution/ml-info'),
  },

  strategiesConfig: {
    get: () => get<StrategiesFile>('/api/strategies/config'),
    put: (config: StrategiesFile) => post<StrategiesFile>('/api/strategies/config', config),
  },
}
