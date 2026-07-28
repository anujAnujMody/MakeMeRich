import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/mocks/test-utils'
import { StrategiesPage } from '@/pages/StrategiesPage'

describe('StrategiesPage', () => {
  it('renders page title', () => {
    renderWithProviders(<StrategiesPage />)
    expect(screen.getByText('Strategies')).toBeInTheDocument()
  })

  it('shows subtitle', () => {
    renderWithProviders(<StrategiesPage />)
    expect(screen.getByText(/AI-discovered strategies/)).toBeInTheDocument()
  })

  it('shows loading state initially', () => {
    renderWithProviders(<StrategiesPage />)
    const skeletons = document.querySelectorAll('.animate-pulse')
    expect(skeletons.length).toBeGreaterThanOrEqual(1)
  })

  it('renders strategy cards for active strategies', async () => {
    renderWithProviders(<StrategiesPage />)
    expect(await screen.findByText('BankNifty Put Selling')).toBeInTheDocument()
    expect(await screen.findByText('Nifty ORBS Breakout')).toBeInTheDocument()
  })

  it("states each strategy's rule in plain English", async () => {
    renderWithProviders(<StrategiesPage />)
    expect(await screen.findByText(/Sell puts when VIX > 14 & RSI < 35/)).toBeInTheDocument()
  })

  it('shows a provenance badge with sample size on every strategy', async () => {
    renderWithProviders(<StrategiesPage />)
    await screen.findByText('BankNifty Put Selling')
    expect(screen.getAllByText(/n=/i).length).toBeGreaterThan(0)
  })

  it('renders auto-discover badge', async () => {
    renderWithProviders(<StrategiesPage />)
    expect(await screen.findByText('Auto-discover ON')).toBeInTheDocument()
  })

  it('shows discovery queue', async () => {
    renderWithProviders(<StrategiesPage />)
    expect(await screen.findByText('Discovery Queue')).toBeInTheDocument()
    expect(await screen.findByText('BankNifty Iron Condor')).toBeInTheDocument()
    expect(await screen.findByText('Nifty Short Straddle (weekly)')).toBeInTheDocument()
  })

  it('shows inactive strategies', async () => {
    renderWithProviders(<StrategiesPage />)
    expect(await screen.findByText('Nifty Call Selling')).toBeInTheDocument()
  })

  it('shows the backtest panel as not-available-yet rather than fabricating numbers', async () => {
    renderWithProviders(<StrategiesPage />)
    expect(await screen.findByText(/backtest results/i)).toBeInTheDocument()
    expect(await screen.findByText(/not available yet/i)).toBeInTheDocument()
  })

  it('pausing a strategy persists — it stays paused after the list refetches, not just local UI state', async () => {
    const user = userEvent.setup()
    renderWithProviders(<StrategiesPage />)
    await screen.findByText('BankNifty Put Selling')

    expect(await screen.findByText(/3 active, 0 paused, 1 inactive/)).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: /pause strategy/i })[0])
    expect(await screen.findByText(/2 active, 1 paused, 1 inactive/)).toBeInTheDocument()
    // 2 "Resume strategy" buttons now: the one just paused, plus the
    // already-inactive-and-paused strategy that started that way.
    expect(await screen.findAllByRole('button', { name: /resume strategy/i })).toHaveLength(2)

    // A fresh fetch (e.g. Refresh) must still show it paused — proves the
    // pause reached the backend, not just a client-only Set.
    await user.click(screen.getByRole('button', { name: /refresh/i }))
    expect(await screen.findByText(/2 active, 1 paused, 1 inactive/)).toBeInTheDocument()
  })
})
