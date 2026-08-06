import type { ConditionResult, DecisionVerdict } from '@/components/DecisionCard/types'

export type TradingMode = 'dry-run' | 'live'
export type BotStatus = 'live' | 'stale' | 'paused'

export type PipelineStageKey = 'fetch' | 'analyze' | 'risk' | 'decide' | 'act'
export type PipelineStageState = 'done' | 'active' | 'pending'

export interface PipelineStageInfo {
  key: PipelineStageKey
  label: string
  state: PipelineStageState
  durationLabel?: string
}

export interface OpenPosition {
  symbol: string
  lots: number
  entryTime: string
  pnl: number
}

export interface CycleEvaluation {
  id: string
  timestamp: string
  strategy: string
  instrument: string
  verdict: DecisionVerdict
  reason: string
  conditions: ConditionResult[]
}

export interface DashboardSnapshot {
  mode: TradingMode
  status: BotStatus
  asOf: string
  nextCheckInSeconds: number

  todayPnl: number
  dailyLossLimit: number

  openPositionsCount: number
  maxPositions: number
  tradesToday: number
  maxTradesPerDay: number

  positions: OpenPosition[]
  pipeline: PipelineStageInfo[]

  weekWinRatePct: number
  weekTrades: number
  weekNetPnl: number
  mlStage: 'shadow' | 'advisory' | 'gating' | 'live-gating'
}
