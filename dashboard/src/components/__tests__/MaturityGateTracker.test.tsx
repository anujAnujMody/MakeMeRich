import { render, screen } from '@testing-library/react'
import { MaturityGateTracker } from '@/components/MaturityGateTracker'
import type { MaturityGateStatus } from '@/types/learning'

const status: MaturityGateStatus = {
  currentStage: 'shadow',
  closedPaperTrades: 42,
  dsr: 0.71,
  filteredEdgePositiveSessions: 0,
  consecutiveGatingSessionsOnPaper: 0,
}

describe('MaturityGateTracker', () => {
  it('shows all four stages', () => {
    render(<MaturityGateTracker status={status} />)
    expect(screen.getByText('Shadow')).toBeInTheDocument()
    expect(screen.getByText('Advisory')).toBeInTheDocument()
    expect(screen.getByText('Gating')).toBeInTheDocument()
    expect(screen.getByText('Live-gating')).toBeInTheDocument()
  })

  it('marks the current stage', () => {
    render(<MaturityGateTracker status={status} />)
    expect(screen.getByText('Current stage')).toBeInTheDocument()
  })

  it("shows live progress against the next gate's criteria, not just the label", () => {
    render(<MaturityGateTracker status={status} />)
    expect(screen.getByText(/42 \/ 100 closed trades/)).toBeInTheDocument()
    expect(screen.getByText(/DSR 0\.71 \(needs ≥ 0\.95\)/)).toBeInTheDocument()
  })

  it('never implies the model can act beyond what the current stage allows', () => {
    render(<MaturityGateTracker status={status} />)
    expect(screen.getByText(/predicts and logs.*zero influence/i)).toBeInTheDocument()
  })

  it('shows the gating stage as reached once its gate is cleared', () => {
    render(
      <MaturityGateTracker
        status={{ currentStage: 'gating', closedPaperTrades: 260, dsr: 0.97, filteredEdgePositiveSessions: 62, consecutiveGatingSessionsOnPaper: 4 }}
      />,
    )
    expect(screen.getAllByText(/cleared/i).length).toBeGreaterThanOrEqual(2)
  })
})
