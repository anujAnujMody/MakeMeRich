import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/mocks/test-utils'
import { WinRateChart } from '@/components/WinRateChart'

describe('WinRateChart', () => {
  it('renders win rate percentage', () => {
    renderWithProviders(<WinRateChart winRate={75} totalTrades={20} />)
    expect(screen.getByText('75%')).toBeInTheDocument()
  })

  it('renders total trade count', () => {
    renderWithProviders(<WinRateChart winRate={50} totalTrades={10} />)
    expect(screen.getByText('10 trades')).toBeInTheDocument()
  })

  it('handles singular trade label', () => {
    renderWithProviders(<WinRateChart winRate={100} totalTrades={1} />)
    expect(screen.getByText('1 trade')).toBeInTheDocument()
  })

  it('renders at sm size', () => {
    renderWithProviders(<WinRateChart winRate={60} totalTrades={5} size="sm" />)
    expect(screen.getByText('60%')).toBeInTheDocument()
  })
})
