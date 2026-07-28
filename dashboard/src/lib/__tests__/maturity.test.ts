import { MATURITY_STAGES } from '@/lib/maturity'
import type { MaturityGateStatus } from '@/types/learning'

const baseStatus: MaturityGateStatus = {
  currentStage: 'shadow',
  closedPaperTrades: 0,
  dsr: null,
  filteredEdgePositiveSessions: 0,
  consecutiveGatingSessionsOnPaper: 0,
}

describe('MATURITY_STAGES', () => {
  it('defines all four stages in order', () => {
    expect(MATURITY_STAGES.map((s) => s.key)).toEqual(['shadow', 'advisory', 'gating', 'live-gating'])
  })

  it('shadow is always cleared', () => {
    const shadow = MATURITY_STAGES.find((s) => s.key === 'shadow')!
    expect(shadow.gate(baseStatus).cleared).toBe(true)
  })

  it('advisory clears only at >=100 trades AND DSR >= 0.95', () => {
    const advisory = MATURITY_STAGES.find((s) => s.key === 'advisory')!
    expect(advisory.gate({ ...baseStatus, closedPaperTrades: 100, dsr: 0.95 }).cleared).toBe(true)
    expect(advisory.gate({ ...baseStatus, closedPaperTrades: 99, dsr: 0.95 }).cleared).toBe(false)
    expect(advisory.gate({ ...baseStatus, closedPaperTrades: 100, dsr: 0.94 }).cleared).toBe(false)
    expect(advisory.gate({ ...baseStatus, closedPaperTrades: 100, dsr: null }).cleared).toBe(false)
  })

  it('gating clears only at >=250 trades AND >=60 positive-edge sessions', () => {
    const gating = MATURITY_STAGES.find((s) => s.key === 'gating')!
    expect(gating.gate({ ...baseStatus, closedPaperTrades: 250, filteredEdgePositiveSessions: 60 }).cleared).toBe(true)
    expect(gating.gate({ ...baseStatus, closedPaperTrades: 249, filteredEdgePositiveSessions: 60 }).cleared).toBe(false)
  })

  it('live-gating clears only at >=30 consecutive clean sessions', () => {
    const liveGating = MATURITY_STAGES.find((s) => s.key === 'live-gating')!
    expect(liveGating.gate({ ...baseStatus, consecutiveGatingSessionsOnPaper: 30 }).cleared).toBe(true)
    expect(liveGating.gate({ ...baseStatus, consecutiveGatingSessionsOnPaper: 29 }).cleared).toBe(false)
  })
})
