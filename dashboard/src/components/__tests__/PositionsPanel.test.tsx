import { render, screen } from '@testing-library/react'
import { PositionsPanel } from '@/components/PositionsPanel'
import type { OpenPosition } from '@/types/dashboard-snapshot'

describe('PositionsPanel', () => {
  it('shows an empty-state message when there are no open positions', () => {
    render(<PositionsPanel positions={[]} />)
    expect(screen.getByText(/no open positions/i)).toBeInTheDocument()
  })

  it('renders each position with symbol, lots, entry time, and signed P&L', () => {
    const positions: OpenPosition[] = [
      { symbol: 'SENSEX 81400 CE', lots: 1, entryTime: '09:47', pnl: 186 },
    ]
    render(<PositionsPanel positions={positions} />)

    expect(screen.getByText('SENSEX 81400 CE')).toBeInTheDocument()
    expect(screen.getByText(/1 lot/)).toBeInTheDocument()
    expect(screen.getByText(/09:47/)).toBeInTheDocument()
    expect(screen.getByText(/\+₹186\.00/)).toBeInTheDocument()
  })

  it('pluralizes lots correctly', () => {
    const positions: OpenPosition[] = [
      { symbol: 'NIFTY 25600 CE', lots: 2, entryTime: '10:00', pnl: -40 },
    ]
    render(<PositionsPanel positions={positions} />)
    expect(screen.getByText(/2 lots/)).toBeInTheDocument()
  })
})
