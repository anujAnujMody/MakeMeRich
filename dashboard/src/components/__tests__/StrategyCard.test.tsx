import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/mocks/test-utils'
import { StrategyCard } from '@/components/StrategyCard'
import type { StrategyConfig } from '@/types'
import { useStrategyStore } from '@/stores/strategyStore'

const strategy: StrategyConfig = {
  name: 'ORBS',
  enabled: true,
  instruments: [
    { symbol: 'BANKNIFTY', exchange: 'NFO', rangeMin: 15, maxTrades: 2 },
    { symbol: 'NIFTY', exchange: 'NFO', rangeMin: 15, maxTrades: 2 },
  ],
  params: { target_rr: 1.5 },
}

describe('StrategyCard', () => {
  beforeEach(() => {
    useStrategyStore.setState({ strategies: [strategy], activeStrategy: null })
  })

  it('renders strategy name', () => {
    renderWithProviders(<StrategyCard name="ORBS" />)
    expect(screen.getByText('ORBS')).toBeInTheDocument()
  })

  it('shows enabled badge when strategy is enabled', () => {
    renderWithProviders(<StrategyCard name="ORBS" />)
    expect(screen.getByText('Active')).toBeInTheDocument()
  })

  it('shows disabled badge when strategy is disabled', () => {
    useStrategyStore.setState({
      strategies: [{ ...strategy, enabled: false }],
    })
    renderWithProviders(<StrategyCard name="ORBS" />)
    expect(screen.getByText('Disabled')).toBeInTheDocument()
  })

  it('displays instrument count', () => {
    renderWithProviders(<StrategyCard name="ORBS" />)
    expect(screen.getByText(/2 instruments?/i)).toBeInTheDocument()
  })

  it('toggles strategy on button click', async () => {
    const user = userEvent.setup()
    renderWithProviders(<StrategyCard name="ORBS" />)

    const toggle = screen.getByRole('button', { name: /toggle|disable|enable/i })
    await user.click(toggle)

    const stored = useStrategyStore.getState().strategies.find((s) => s.name === 'ORBS')
    expect(stored?.enabled).toBe(false)
  })
})
