import { render, screen } from '@testing-library/react'
import { TradeStatsCard } from '@/components/TradeStatsCard'
import type { EngineStats } from '@/types'

const stats: EngineStats = {
  total_trades: 42,
  win_rate: 55.5,
  avg_profit: 120,
  sharpe: 1.3,
  profit_factor: 1.4,
  max_drawdown: -900,
}

describe('TradeStatsCard', () => {
  it('shows total trades, win rate, sharpe, profit factor, drawdown, and avg profit', () => {
    render(<TradeStatsCard stats={stats} />)
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(screen.getByText('55.5%')).toBeInTheDocument()
    expect(screen.getByText('1.30')).toBeInTheDocument()
    expect(screen.getByText('1.40')).toBeInTheDocument()
    expect(screen.getByText('₹900')).toBeInTheDocument()
    expect(screen.getByText('₹120')).toBeInTheDocument()
  })
})
