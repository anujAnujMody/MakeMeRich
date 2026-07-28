import { http, HttpResponse } from 'msw'
import type { ExecutionStatus, PaperTrade, PaperPositionCount, SkippedSignalInfo, StrategyConfig, Trade, PnLAnalysis, Order, JournalEntry, ResearchBrief, StrategyCard, DiscoveryQueueItem, DailyRecap, PatternLibraryEntry, LearningProgress, StrategiesFile } from '@/types'
import type { CycleEvaluation, DashboardSnapshot } from '@/types/dashboard-snapshot'
import type { PendingApproval } from '@/types/approval'
import type { MaturityGateStatus, ShadowComparison } from '@/types/learning'
import type { EngineHealthStatus } from '@/types/ops'
import { mockQuotes, mockPositions, mockOrders, mockDashboard, mockEquityCurve, mockDailyPnL, mockWatchlist, mockJournalEntries, mockBrokerStatus, mockRejectedOrders } from './seed'

export const mockDashboardSnapshot: DashboardSnapshot = {
  mode: 'dry-run',
  status: 'live',
  asOf: new Date().toISOString(),
  nextCheckInSeconds: 47,
  todayPnl: 1240.5,
  dailyLossLimit: 700,
  openPositionsCount: 1,
  maxPositions: 5,
  tradesToday: 1,
  maxTradesPerDay: 10,
  positions: [{ symbol: 'SENSEX 81400 CE', lots: 1, entryTime: '09:47', pnl: 186 }],
  pipeline: [
    { key: 'fetch', label: 'Fetch', state: 'done', durationLabel: '0.4s' },
    { key: 'analyze', label: 'Analyze', state: 'done', durationLabel: '0.2s' },
    { key: 'risk', label: 'Risk', state: 'active' },
    { key: 'decide', label: 'Decide', state: 'pending' },
    { key: 'act', label: 'Act', state: 'pending' },
  ],
  weekWinRatePct: 57,
  weekTrades: 14,
  weekNetPnl: 3120,
  mlStage: 'shadow',
}

export const mockTodayDecisions: CycleEvaluation[] = [
  {
    id: 'ev-3',
    timestamp: '2026-07-28T10:05:32Z',
    strategy: 'orbs',
    instrument: 'SENSEX',
    verdict: 'skipped',
    reason: 'ORB breakout — blocked by volume filter, needed 1.5x avg, got 0.82x',
    conditions: [
      { label: '5-min close beyond opening range', required: '> 81,350.0', actual: '81,362.4', passed: true, evaluated: true },
      { label: 'Range width in bounds', required: '20–200 pts', actual: '140 pts', passed: true, evaluated: true },
      { label: 'Volume above average', required: '≥ 1.5x avg', actual: '0.82x', passed: false, evaluated: true },
      { label: 'Daily loss limit not breached', required: 'not breached', actual: 'not reached', passed: false, evaluated: false },
      { label: 'Affordable strike within risk budget', required: '≤ ₹700.00 risk', actual: 'not reached', passed: false, evaluated: false },
    ],
  },
  {
    id: 'ev-2',
    timestamp: '2026-07-28T09:47:11Z',
    strategy: 'orbs',
    instrument: 'SENSEX 81400 CE',
    verdict: 'traded',
    reason: 'ORB breakout — all 5 checks passed, 1 lot at ₹95.20',
    conditions: [
      { label: '5-min close beyond opening range', required: '> 81,350.0', actual: '81,378.9', passed: true, evaluated: true },
      { label: 'Range width in bounds', required: '20–200 pts', actual: '118 pts', passed: true, evaluated: true },
      { label: 'Volume above average', required: '≥ 1.5x avg', actual: '1.9x', passed: true, evaluated: true },
      { label: 'Daily loss limit not breached', required: 'not breached', actual: '₹0.00 used', passed: true, evaluated: true },
      { label: 'Affordable strike within risk budget', required: '≤ ₹700.00 risk', actual: '₹665.00 risk, 1 lot', passed: true, evaluated: true },
    ],
  },
  {
    id: 'ev-1',
    timestamp: '2026-07-28T09:31:04Z',
    strategy: 'orbs',
    instrument: 'NIFTY',
    verdict: 'skipped',
    reason: 'ORB breakout — blocked by range-width filter, 214 pts too wide',
    conditions: [
      { label: '5-min close beyond opening range', required: '> 25,041.0', actual: '25,058.2', passed: true, evaluated: true },
      { label: 'Range width in bounds', required: '20–200 pts', actual: '214 pts', passed: false, evaluated: true },
      { label: 'Volume above average', required: '≥ 1.5x avg', actual: 'not reached', passed: false, evaluated: false },
    ],
  },
]

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

// Handlers below mutate this state (placing orders, deciding approvals,
// pausing the engine, ...). Each is behind an `initial*` factory so
// `resetMockState()` can restore a clean slate between tests — without it,
// one test's mutation (e.g. approving ap-1) permanently leaks into every
// test that runs after it in the same file.
function initialOrders(): Order[] {
  return [...mockOrders]
}

function initialStrategiesConfig(): StrategiesFile {
  return {
    check_interval_secs: 60,
    ml_threshold: 0.55,
    max_trades_per_day: 10,
    risk_per_trade_pct: 1.0,
    max_daily_loss_pct: 3.0,
    max_drawdown_pct: 15.0,
    max_concurrent_positions: 5,
    instruments: [
      { symbol: 'NIFTY', exchange: 'NSE', ticker: '^NSEI', active: true, lot_size: 65 },
      { symbol: 'BANKNIFTY', exchange: 'NSE', ticker: '^NSEBANK', active: true, lot_size: 30 },
      { symbol: 'SENSEX', exchange: 'BFO', ticker: 'BSE:SENSEX', active: true, lot_size: 20 },
    ],
    strategies: [
      { name: 'orbs', active: true, instruments: ['NIFTY', 'BANKNIFTY', 'SENSEX'], params: { opening_minutes: 15 } },
      { name: 'vwap_reversion', active: true, instruments: ['NIFTY'], params: {} },
    ],
  }
}

function initialEngineHealth(): EngineHealthStatus {
  return {
    runState: 'running',
    dailyLossState: 'normal',
    drawdownBreakerTripped: false,
    currentDrawdownPct: 3.2,
    maxDrawdownLimitPct: 15,
    lastSuccessfulPollSecondsAgo: 12,
  }
}

function initialAgentStrategies(): { active: StrategyCard[]; inactive: StrategyCard[]; queue: DiscoveryQueueItem[] } {
  return {
    active: [
      { id: 'str-1', name: 'BankNifty Put Selling', description: 'Sell OTM puts when VIX is elevated and RSI oversold.', active: true, winRate: 72, weeklyPnl: 780, confidence: 78, confidenceTrend: 'up', totalTrades: 18, paused: false, lastActive: new Date().toISOString(), rule: 'Sell puts when VIX > 14 & RSI < 35' },
      { id: 'str-2', name: 'Nifty ORBS Breakout', description: 'Momentum trades on opening range breakout.', active: true, winRate: 65, weeklyPnl: 320, confidence: 62, confidenceTrend: 'stable', totalTrades: 12, paused: false, lastActive: new Date().toISOString(), rule: 'Buy when price > 15-min high + volume spike' },
      { id: 'str-3', name: 'FinNifty Credit Spread', description: 'Put credit spreads when IV rank > 50.', active: true, winRate: 80, weeklyPnl: 180, confidence: 55, confidenceTrend: 'up', totalTrades: 5, paused: false, lastActive: new Date(Date.now() - 86400000).toISOString(), rule: 'Sell 1% OTM put spread when IVR > 50' },
    ],
    inactive: [
      { id: 'str-4', name: 'Nifty Call Selling', description: 'Sell OTM calls on RSI overbought.', active: false, winRate: 45, weeklyPnl: -120, confidence: 38, confidenceTrend: 'down', totalTrades: 8, paused: true, pauseReason: 'Confidence dropped below 40%. Regime changed to range-bound.', lastActive: new Date(Date.now() - 3 * 86400000).toISOString(), rule: 'Sell calls when hourly RSI > 70' },
    ],
    queue: [
      { name: 'BankNifty Iron Condor', progress: 80, status: 'testing' },
      { name: 'Nifty Short Straddle (weekly)', progress: 45, status: 'testing' },
      { name: 'Sensex Put Ratio Spread', progress: 20, status: 'validating' },
    ],
  }
}

function initialApprovals(): PendingApproval[] {
  return [
    {
      id: 'ap-1',
      createdAt: new Date(Date.now() - 5_000).toISOString(),
      expiresAt: new Date(Date.now() + 55_000).toISOString(),
      instrument: 'SENSEX 81400 CE',
      side: 'BUY',
      lots: 1,
      premium: 95.2,
      stopLoss: 61.88,
      target: 152.32,
      estimatedCost: 70.5,
      modelVerdict: 'favorable',
      status: 'pending',
    },
  ]
}

let orders = initialOrders()
let executionRunning = false
let currentMode: 'dry-run' | 'live' = 'dry-run'
let strategiesConfig: StrategiesFile = initialStrategiesConfig()
let engineHealth: EngineHealthStatus = initialEngineHealth()
let approvals: PendingApproval[] = initialApprovals()
let agentStrategies = initialAgentStrategies()

/** Restores every mutable mock to its initial value — call from a test
 * suite's `afterEach` so mutations in one test (order placed, approval
 * decided, engine paused, ...) never leak into the next. */
export function resetMockState(): void {
  orders = initialOrders()
  executionRunning = false
  currentMode = 'dry-run'
  strategiesConfig = initialStrategiesConfig()
  engineHealth = initialEngineHealth()
  approvals = initialApprovals()
  agentStrategies = initialAgentStrategies()
}

export const handlers = [
  http.get('*/api/mode', () => HttpResponse.json({ mode: currentMode })),

  http.post('*/api/mode', async ({ request }) => {
    const body = (await request.json()) as { mode: 'dry-run' | 'live' }
    currentMode = body.mode
    return HttpResponse.json({ mode: currentMode })
  }),

  http.get('*/api/approvals', () => HttpResponse.json(approvals.filter((a) => a.status === 'pending'))),

  http.post('*/api/approvals/decide', async ({ request }) => {
    const body = (await request.json()) as { id: string; decision: 'approve' | 'reject' }
    const approval = approvals.find((a) => a.id === body.id)
    if (approval) approval.status = body.decision === 'approve' ? 'approved' : 'rejected'
    return HttpResponse.json(approval ?? null)
  }),

  http.get('*/api/dashboard/snapshot', () => HttpResponse.json(mockDashboardSnapshot)),

  http.get('*/api/decisions/today', () => HttpResponse.json(mockTodayDecisions)),

  http.get('*/api/dashboard', () => HttpResponse.json(mockDashboard)),

  http.get('*/api/quotes', () => HttpResponse.json(mockQuotes)),

  http.get('*/api/positions', () => HttpResponse.json(mockPositions)),

  http.get('*/api/orders', () => HttpResponse.json(orders)),

  http.post('*/api/orders/place', async ({ request }) => {
    const body = await request.json() as Partial<Order>
    const newOrder: Order = {
      id: `o${Date.now()}`,
      symbol: body.symbol ?? '',
      exchange: body.exchange ?? 'NFO',
      transactionType: body.transactionType ?? 'BUY',
      quantity: body.quantity ?? 0,
      price: body.price ?? 0,
      triggerPrice: body.triggerPrice ?? 0,
      status: 'OPEN',
      orderType: body.orderType ?? 'MARKET',
      productType: body.productType ?? 'MIS',
      filledQty: 0,
      averagePrice: 0,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    }
    orders.unshift(newOrder)
    return HttpResponse.json(newOrder, { status: 201 })
  }),

  http.post('*/api/orders/cancel', async ({ request }) => {
    const { id } = await request.json() as { id: string }
    orders = orders.map((o) => o.id === id ? { ...o, status: 'CANCELLED' as const } : o)
    return HttpResponse.json({ success: true })
  }),

  http.post('*/api/positions/squareoff', async ({ request }) => {
    const { symbol, exchange } = await request.json() as { symbol: string; exchange: string }
    return HttpResponse.json({ success: true, symbol, exchange })
  }),

  http.get('*/api/strategies', () => HttpResponse.json(mockStrategies)),

  http.get('*/api/trades', ({ request }) => {
    const url = new URL(request.url)
    const strategy = url.searchParams.get('strategy')
    const symbol = url.searchParams.get('symbol')
    const from = url.searchParams.get('from')
    const to = url.searchParams.get('to')
    let filtered = [...mockTrades]
    if (strategy) filtered = filtered.filter((t) => t.strategy === strategy)
    if (symbol) filtered = filtered.filter((t) => t.symbol === symbol)
    if (from) filtered = filtered.filter((t) => t.timestamp.slice(0, 10) >= from)
    if (to) filtered = filtered.filter((t) => t.timestamp.slice(0, 10) <= to)
    return HttpResponse.json(filtered)
  }),

  http.get('*/api/pnl', () => HttpResponse.json(mockPnL)),



  http.get('*/api/equity-curve', () => HttpResponse.json(mockEquityCurve)),

  http.get('*/api/daily-pnl', () => HttpResponse.json(mockDailyPnL)),

  http.get('*/api/watchlist', () => HttpResponse.json(mockWatchlist)),

  http.get('*/api/market-status', () => HttpResponse.json({
    status: 'open',
    label: 'Market Open',
    nextEvent: 'Closing at 3:30 PM IST',
    currentTime: new Date().toISOString(),
  })),

  http.get('*/api/journal', ({ request }) => {
    const url = new URL(request.url)
    const tag = url.searchParams.get('tag')
    let entries = [...mockJournalEntries]
    if (tag) entries = entries.filter((e) => e.tags.includes(tag))
    return HttpResponse.json(entries)
  }),

  http.post('*/api/journal/save', async ({ request }) => {
    const body = await request.json() as JournalEntry
    return HttpResponse.json(body, { status: 201 })
  }),

  http.get('*/api/broker-status', () => HttpResponse.json(mockBrokerStatus)),

  http.get('*/api/engine/health', () => HttpResponse.json(engineHealth)),

  http.post('*/api/engine/pause', () => {
    engineHealth = { ...engineHealth, runState: 'paused' }
    return HttpResponse.json(engineHealth)
  }),

  http.post('*/api/engine/resume', () => {
    engineHealth = { ...engineHealth, runState: 'running' }
    return HttpResponse.json(engineHealth)
  }),

  http.post('*/api/engine/reset-drawdown-breaker', () => {
    engineHealth = { ...engineHealth, drawdownBreakerTripped: false, currentDrawdownPct: 0 }
    return HttpResponse.json(engineHealth)
  }),

  http.get('*/api/rejected-orders', () => HttpResponse.json(mockRejectedOrders)),

  http.get('*/api/agents/research', () => HttpResponse.json({
    date: new Date().toISOString().split('T')[0],
    timestamp: new Date().toISOString(),
    sentiment: 'profit',
    summary: 'Premiums are high and market is calm — good conditions for our strategies.',
    confidence: 72,
    details: 'US markets were flat overnight. Asian markets mildly positive. Indian VIX at 14 (normal). Option premiums elevated ~12% vs last week — favorable for selling strategies.',
  } satisfies ResearchBrief)),

  http.get('*/api/agents/strategies', () => HttpResponse.json(agentStrategies)),

  http.post('*/api/agents/strategies/pause', async ({ request }) => {
    const body = (await request.json()) as { id: string; paused: boolean }
    const card = [...agentStrategies.active, ...agentStrategies.inactive].find((s) => s.id === body.id)
    if (card) card.paused = body.paused
    return HttpResponse.json(card ?? null)
  }),

  http.get('*/api/agents/recap', ({ request }) => {
    const url = new URL(request.url)
    const date = url.searchParams.get('date') ?? new Date().toISOString().split('T')[0]
    return HttpResponse.json({
      date,
      totalPnl: 320,
      strategies: [
        { name: 'BankNifty Put Selling', pnl: 520, wins: 2, losses: 0 },
        { name: 'Nifty ORBS Breakout', pnl: -200, wins: 1, losses: 1 },
      ],
      mlLesson: 'The ORBS loss was caused by early exit. Price hit target 8 minutes after close. Adjusting trailing exit logic.',
    } satisfies DailyRecap)
  }),

  http.get('*/api/agents/learning', () => HttpResponse.json({
    winRateTrend: [58, 62, 60, 63, 65, 68, 72],
    totalStrategiesDiscovered: 8,
    totalStrategiesRetired: 3,
    avgProfitPerTrade: 245,
    dates: ['Jul 20', 'Jul 21', 'Jul 22', 'Jul 23', 'Jul 24', 'Jul 25', 'Jul 26'],
  } satisfies LearningProgress)),

  http.get('*/api/agents/patterns', () => HttpResponse.json([
    { pattern: 'High VIX + Low PCR', condition: 'VIX > 14 & PCR < 0.85', winRate: 68, tradesTested: 23, lastObserved: new Date().toISOString(), status: 'working' } satisfies PatternLibraryEntry,
    { pattern: 'ORBS Momentum', condition: 'Price > 15-min high + volume spike', winRate: 62, tradesTested: 15, lastObserved: new Date(Date.now() - 86400000).toISOString(), status: 'working' } satisfies PatternLibraryEntry,
    { pattern: 'Low VIX + High PCR', condition: 'VIX < 12 & PCR > 1.0', winRate: 35, tradesTested: 8, lastObserved: new Date(Date.now() - 5 * 86400000).toISOString(), status: 'no-edge' } satisfies PatternLibraryEntry,
    { pattern: 'Premium Selling (Elevated IV)', condition: 'IVR > 50 & premium > 2-week avg', winRate: 75, tradesTested: 12, lastObserved: new Date(Date.now() - 2 * 86400000).toISOString(), status: 'working' } satisfies PatternLibraryEntry,
  ])),

  http.post('*/api/learning/optimize', () => HttpResponse.json({
    results: [
      { params: { target_rr: 1.5, sl_rr: 1.0 }, score: 85.4, win_rate: 66.67, total_pnl: 9800, sharpe: 1.52, total_trades: 30 },
      { params: { target_rr: 1.0, sl_rr: 1.0 }, score: 72.1, win_rate: 60.0, total_pnl: 7200, sharpe: 1.3, total_trades: 30 },
    ],
  })),

  /* ─── Execution / Paper Trading Mocks ─── */

  http.get('*/api/execution/status', () => HttpResponse.json({
    running: executionRunning,
    check_interval_secs: 60,
    ml_threshold: 0.55,
    started_at: executionRunning ? new Date().toISOString() : null,
  } satisfies ExecutionStatus)),

  http.get('*/api/execution/trades', () => HttpResponse.json([
    {
      id: 'paper_20260726_091500_BUY',
      strategy: 'ORBS',
      symbol: 'NIFTY',
      direction: 'BUY',
      entry_price: 24100,
      exit_price: 24150,
      quantity: 25,
      pnl: 1250,
      outcome: 'win',
      entry_time: '2026-07-26T09:15:00Z',
      exit_time: '2026-07-26T09:45:00Z',
      ml_confidence: 0.72,
      type: 'paper',
    },
    {
      id: 'paper_20260726_100000_SELL',
      strategy: 'ORBS',
      symbol: 'NIFTY',
      direction: 'SELL',
      entry_price: 24150,
      exit_price: null,
      quantity: 25,
      pnl: null,
      outcome: 'open',
      entry_time: '2026-07-26T10:00:00Z',
      exit_time: null,
      ml_confidence: 0.68,
      type: 'paper',
    },
  ] satisfies PaperTrade[])),

  http.get('*/api/execution/positions', () => HttpResponse.json({ count: 1 } satisfies PaperPositionCount)),

  http.get('*/api/execution/skipped', () => HttpResponse.json([
    {
      id: 1,
      strategy: 'ORBS',
      symbol: 'NIFTY',
      direction: 'BUY',
      entry_price: 24180,
      ml_confidence: 0.42,
      ml_threshold: 0.55,
      reason: 'Below ML confidence threshold (0.42 < 0.55)',
      timestamp: '2026-07-26T10:05:00Z',
    },
  ] satisfies SkippedSignalInfo[])),

  http.post('*/api/execution/start', () => {
    executionRunning = true
    return HttpResponse.json({ status: 'started' })
  }),

  http.post('*/api/execution/stop', () => {
    executionRunning = false
    return HttpResponse.json({ status: 'stopped' })
  }),

  /* ─── Learning / Training Mocks ─── */

  http.get('*/api/learning/training-results', () => HttpResponse.json({
    accuracy: 0.72,
    feature_importance: { vix_rank: 0.25, adx: 0.18, pcr: 0.15, prev_ret_5d: 0.12 },
    walk_forward: { oos_sharpe: 1.2, profit_factor: 1.8, total_trades: 45 },
    total_samples: 200,
    win_rate_pct: 68.5,
  })),

  http.get('*/api/learning/maturity-gate', () => HttpResponse.json({
    currentStage: 'shadow',
    closedPaperTrades: 42,
    dsr: 0.71,
    filteredEdgePositiveSessions: 0,
    consecutiveGatingSessionsOnPaper: 0,
  } satisfies MaturityGateStatus)),

  http.get('*/api/learning/shadow-comparisons', () => HttpResponse.json([
    { id: 'sc-1', timestamp: '2026-07-28T09:47:11Z', instrument: 'SENSEX 81400 CE', actualVerdict: 'traded', modelVerdict: 'would trade', agreed: true },
    { id: 'sc-2', timestamp: '2026-07-28T10:05:32Z', instrument: 'NIFTY 24200 PE', actualVerdict: 'skipped', modelVerdict: 'would trade', agreed: false },
    { id: 'sc-3', timestamp: '2026-07-27T11:12:03Z', instrument: 'BANKNIFTY 51200 PE', actualVerdict: 'skipped', modelVerdict: 'would skip', agreed: true },
  ] satisfies ShadowComparison[])),

  http.get('*/api/learning/stats', () => HttpResponse.json({
    total_trades: 45,
    win_rate: 0.68,
    avg_profit: 245,
    sharpe: 1.2,
    profit_factor: 1.8,
    max_drawdown: 0.15,
  })),

  http.post('*/api/learning/retrain', () => HttpResponse.json({
    status: 'ok',
    trades_used: 200,
    accuracy: 0.72,
    top_features: [0.25, 0.18, 0.15],
  })),

  /* ─── Execution Cycle / Signal Feed Mocks ─── */

  http.get('*/api/execution/cycle', () => HttpResponse.json({
    stage: 'idle',
    running: executionRunning,
    cycle_start: executionRunning ? new Date().toISOString() : null,
    next_cycle_in_secs: 60,
    instruments_processed: 0,
    instruments_total: 2,
    signals_generated: 0,
    trades_placed: 0,
    trades_skipped: 0,
    last_error: null,
  })),

  http.get('*/api/execution/signal-feed', () => HttpResponse.json([])),

  /* ─── Strategies Config Mocks ─── */

  http.get('*/api/strategies/config', () => HttpResponse.json(strategiesConfig)),

  http.post('*/api/strategies/config', async ({ request }) => {
    const body = (await request.json()) as StrategiesFile
    strategiesConfig = body
    return HttpResponse.json(strategiesConfig)
  }),
]
