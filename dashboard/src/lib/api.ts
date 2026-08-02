import type { CycleStatus, DashboardData, DailyPnL, DiscoveryQueueItem, EngineStats, EquityPoint, ExecutionStatus, JournalEntry, LearningProgress, MarketData, MarketSession, Order, OptimizeRequest, OptimizeResponse, PaperTrade, PaperPositionCount, PnLAnalysis, PlaceOrderPayload, Position, RejectedOrder, SignalFeedItem, SkippedSignalInfo, StrategyCard, StrategyConfig, StrategyAnalysis, MLInfo, Trade, TradeLogFilters, TrainingResults, WatchlistItem, BrokerStatus, ResearchBrief, DailyRecap, PatternLibraryEntry } from '@/types'
import type { CycleEvaluation, DashboardSnapshot, TradingMode } from '@/types/dashboard-snapshot'
import type { PendingApproval } from '@/types/approval'
import type { MaturityGateStatus, ShadowComparison } from '@/types/learning'
import type { AccountGuardrails, EngineHealthStatus, InstrumentSelections } from '@/types/ops'

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
  const data = await res.json()
  // The backend sends an HONEST `success: false` / `status: "not_ready"`
  // body on a 200 for actions it can't actually perform yet (e.g.
  // `/api/positions/squareoff` when there's no recent price to close at,
  // `/api/learning/retrain` when there's no training pipeline yet) — this
  // used to be silently ignored by every caller, so a real failure looked
  // like success in the UI. Surface it the same way a non-2xx does: throw,
  // so TanStack Query's `onError` actually fires instead of `onSuccess`.
  // The real reason (when there is one) travels in `X-TE-Not-Ready-Reason`,
  // never the JSON body — the body's shape is a frozen contract type.
  const notReadyReason = res.headers.get('X-TE-Not-Ready-Reason')
  if (data && typeof data === 'object') {
    if ('success' in data && data.success === false) {
      throw new Error(notReadyReason || `${path}: action did not succeed`)
    }
    if ('status' in data && data.status === 'not_ready') {
      throw new Error(notReadyReason || `${path}: not ready yet`)
    }
  }
  return data
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'PUT',
    headers: headers(),
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`PUT ${path}: ${res.status} ${res.statusText}`)
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
    // NOT `?? derivedPct`. Win rate and confidence are different quantities:
    // one is the share of past trades that made money, the other is a model's
    // self-reported certainty. Falling back to confidence printed a number
    // under a "Win Rate" label that no trade had ever earned. 0 with
    // `totalTrades: 0` beside it is the honest zero-state.
    winRate: c.winRate ?? 0,
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
    guardrails: () => get<AccountGuardrails>('/api/engine/guardrails'),
    saveGuardrails: (payload: AccountGuardrails) => put<AccountGuardrails>('/api/engine/guardrails', payload),
    instruments: () => get<InstrumentSelections>('/api/engine/instruments'),
    saveInstruments: (payload: InstrumentSelections) => put<InstrumentSelections>('/api/engine/instruments', payload),
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
}
