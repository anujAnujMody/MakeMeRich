export type DailyLossState = 'normal' | 'reduced' | 'halted_day'
export type EngineRunState = 'running' | 'paused'

export interface EngineHealthStatus {
  runState: EngineRunState
  dailyLossState: DailyLossState
  drawdownBreakerTripped: boolean
  currentDrawdownPct: number
  maxDrawdownLimitPct: number
  lastSuccessfulPollSecondsAgo: number
}
