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

/** `GET`/`PUT /api/engine/guardrails` — a NEW endpoint (not part of the
 * original 53-endpoint contract), added by the "Dashboard<->engine wiring
 * remediation" plan section so this actually reaches the live engine
 * instead of living only in `stores/settingsStore.ts`'s localStorage. */
export interface AccountGuardrails {
  capitalRupees: number
  maxDailyLossRupees: number
  maxPositionSizePct: number
  maxDrawdownPct: number
  maxTradesPerDay: number
  maxConcurrentPositions: number
  riskPerTradePct: number
}

export interface InstrumentSelection {
  symbol: string
  exchange: string
  lotSize: number
  active: boolean
}

export interface InstrumentSelections {
  instruments: InstrumentSelection[]
}
