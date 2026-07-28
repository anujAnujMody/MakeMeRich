import { render, screen } from '@testing-library/react'
import { HeroPnlCard } from '@/components/HeroPnlCard'

describe('HeroPnlCard', () => {
  it('shows a signed, rupee-formatted value for a gain', () => {
    render(<HeroPnlCard todayPnl={1240.5} dailyLossLimit={700} />)
    expect(screen.getByText(/\+₹1,240\.50/)).toBeInTheDocument()
  })

  it('shows a signed, rupee-formatted value for a loss', () => {
    render(<HeroPnlCard todayPnl={-350} dailyLossLimit={700} />)
    expect(screen.getByText(/−₹350\.00/)).toBeInTheDocument()
  })

  it('marks the card as a gain when P&L is non-negative', () => {
    const { container } = render(<HeroPnlCard todayPnl={1240.5} dailyLossLimit={700} />)
    expect(container.querySelector('[data-pnl="gain"]')).toBeInTheDocument()
  })

  it('marks the card as a loss when P&L is negative', () => {
    const { container } = render(<HeroPnlCard todayPnl={-1} dailyLossLimit={700} />)
    expect(container.querySelector('[data-pnl="loss"]')).toBeInTheDocument()
  })

  it('shows 0% of the daily loss limit used when P&L is a gain, never a nonsensical figure', () => {
    render(<HeroPnlCard todayPnl={1240.5} dailyLossLimit={700} />)
    expect(screen.getByText(/0% of.*daily loss limit used/i)).toBeInTheDocument()
  })

  it('computes the correct percent of the daily loss limit used for a loss', () => {
    render(<HeroPnlCard todayPnl={-350} dailyLossLimit={700} />)
    expect(screen.getByText(/50% of.*daily loss limit used/i)).toBeInTheDocument()
  })

  it('caps the percent used at 100% even if the loss exceeds the limit', () => {
    render(<HeroPnlCard todayPnl={-900} dailyLossLimit={700} />)
    expect(screen.getByText(/100% of.*daily loss limit used/i)).toBeInTheDocument()
  })
})
