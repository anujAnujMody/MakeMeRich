import { render, screen } from '@testing-library/react'
import { WeekSummaryCard } from '@/components/WeekSummaryCard'

describe('WeekSummaryCard', () => {
  it('shows win rate, trade count, net P&L, and the ML maturity stage', () => {
    render(<WeekSummaryCard winRatePct={57} trades={14} netPnl={3120} mlStage="shadow" />)

    expect(screen.getByText('57%')).toBeInTheDocument()
    expect(screen.getByText('14')).toBeInTheDocument()
    expect(screen.getByText(/\+₹3,120\.00/)).toBeInTheDocument()
    expect(screen.getAllByText(/shadow/i).length).toBeGreaterThanOrEqual(1)
  })

  it('never implies the ML model has power it has not earned', () => {
    render(<WeekSummaryCard winRatePct={57} trades={14} netPnl={3120} mlStage="shadow" />)
    expect(screen.getByText(/watching only|no influence|logging only/i)).toBeInTheDocument()
  })
})
