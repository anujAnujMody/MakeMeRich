import { render, screen } from '@testing-library/react'
import { ProvenanceBadge } from '@/components/ProvenanceBadge'

describe('ProvenanceBadge', () => {
  it('shows the source and sample size for a paper metric', () => {
    render(<ProvenanceBadge source="paper" sampleSize={112} />)
    expect(screen.getByText(/paper/i)).toBeInTheDocument()
    expect(screen.getByText(/n=112/i)).toBeInTheDocument()
  })

  it('renders without a sample size when none is given', () => {
    render(<ProvenanceBadge source="live" />)
    expect(screen.getByText(/live/i)).toBeInTheDocument()
    expect(screen.queryByText(/n=/i)).not.toBeInTheDocument()
  })

  it('renders a distinct high-contrast warning for in-sample numbers', () => {
    render(<ProvenanceBadge source="in-sample" />)
    expect(screen.getByText(/in-sample.*not tradeable/i)).toBeInTheDocument()
  })

  it('carries a data-source attribute for styling and testing', () => {
    const { container } = render(<ProvenanceBadge source="backtest" />)
    expect(container.querySelector('[data-source="backtest"]')).toBeInTheDocument()
  })
})
