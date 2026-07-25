import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/mocks/test-utils'
import { StrategyInsights } from '@/pages/StrategyInsights'
import { useStrategyStore } from '@/stores/strategyStore'
import { mockStrategies } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { http, HttpResponse } from 'msw'

describe('StrategyInsights', () => {
  beforeEach(() => {
    useStrategyStore.setState({ strategies: mockStrategies, activeStrategy: null })
    server.use(
      http.get('*/api/strategies', () => HttpResponse.json(mockStrategies)),
    )
  })

  it('renders page header', () => {
    renderWithProviders(<StrategyInsights />)
    expect(screen.getByText('Strategy Insights')).toBeInTheDocument()
  })

  it('renders StrategyCard for each strategy', () => {
    renderWithProviders(<StrategyInsights />)
    expect(screen.getByText('ORBS')).toBeInTheDocument()
    expect(screen.getByText('MACROSS')).toBeInTheDocument()
  })

  it('renders PnL summary section', async () => {
    renderWithProviders(<StrategyInsights />)
    expect(await screen.findByText('PnL Summary')).toBeInTheDocument()
  })

  it('renders trade log table with headers', async () => {
    renderWithProviders(<StrategyInsights />)
    expect(await screen.findByText('Symbol')).toBeInTheDocument()
    expect(await screen.findByText('Direction')).toBeInTheDocument()
    expect(await screen.findByText('PnL')).toBeInTheDocument()
  })

  it('has strategy filter dropdown', async () => {
    renderWithProviders(<StrategyInsights />)
    expect(await screen.findByLabelText(/filter by strategy|strategy/i)).toBeInTheDocument()
  })

  it('has date range filter inputs', async () => {
    renderWithProviders(<StrategyInsights />)
    expect(screen.getByLabelText(/from|start/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/to|end/i)).toBeInTheDocument()
  })
})
