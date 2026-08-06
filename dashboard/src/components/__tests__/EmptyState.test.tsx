import { render, screen } from '@testing-library/react'
import { EmptyState } from '@/components/EmptyState'

describe('EmptyState', () => {
  it('shows the title, what the panel will show, and what is needed to enable it', () => {
    render(
      <EmptyState
        title="Backtest results"
        what="A full history of simulated trades with costs charged"
        needs="the backtest engine (Phase 5)"
      />,
    )
    expect(screen.getByText('Backtest results')).toBeInTheDocument()
    expect(screen.getByText(/simulated trades with costs charged/)).toBeInTheDocument()
    expect(screen.getByText(/needs the backtest engine \(phase 5\)/i)).toBeInTheDocument()
  })

  it('never renders a fabricated number — only says what is missing', () => {
    render(<EmptyState title="Live P&L" what="Real-time P&L from the broker" needs="a broker connection" />)
    expect(screen.getByText(/not available yet/i)).toBeInTheDocument()
  })

  it('is marked so it can be styled and queried distinctly from real data panels', () => {
    const { container } = render(<EmptyState title="X" what="Y" needs="Z" />)
    expect(container.querySelector('[data-state="empty"]')).toBeInTheDocument()
  })
})
