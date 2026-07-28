export type MaturityStage = 'shadow' | 'advisory' | 'gating' | 'live-gating'

export interface MaturityGateStatus {
  currentStage: MaturityStage
  closedPaperTrades: number
  /** Deflated Sharpe Ratio on purged CV — null until enough trades exist to compute it. */
  dsr: number | null
  filteredEdgePositiveSessions: number
  consecutiveGatingSessionsOnPaper: number
}

export interface ShadowComparison {
  id: string
  timestamp: string
  instrument: string
  actualVerdict: 'traded' | 'skipped'
  modelVerdict: 'would trade' | 'would skip'
  agreed: boolean
}
