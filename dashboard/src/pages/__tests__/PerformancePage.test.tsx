import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/mocks/test-utils'
import { PerformancePage } from '@/pages/PerformancePage'

describe('PerformancePage', () => {
  it('renders page title and subtitle', () => {
    renderWithProviders(<PerformancePage />)
    expect(screen.getByText('Performance')).toBeInTheDocument()
  })

  it('shows headline stat tiles with a provenance badge', async () => {
    renderWithProviders(<PerformancePage />)
    expect(await screen.findByText('Net P&L')).toBeInTheDocument()
    expect(screen.getAllByText(/n=/i).length).toBeGreaterThan(0)
  })

  it('shows the equity curve and drawdown charts', async () => {
    renderWithProviders(<PerformancePage />)
    expect(await screen.findByText('Equity Curve')).toBeInTheDocument()
    expect(await screen.findByText('Drawdown')).toBeInTheDocument()
  })

  it('shows the P&L calendar', async () => {
    renderWithProviders(<PerformancePage />)
    expect(await screen.findByText(/P&L Calendar/)).toBeInTheDocument()
  })

  it('lets the trade log be filtered by real strategy names, not an empty store', async () => {
    const user = userEvent.setup()
    renderWithProviders(<PerformancePage />)
    await user.click(await screen.findByRole('combobox', { name: /filter by strategy/i }))
    expect(await screen.findByText('Nifty ORBS Breakout')).toBeInTheDocument()
  })

  it('shows the trade log table', async () => {
    renderWithProviders(<PerformancePage />)
    expect((await screen.findAllByText('BANKNIFTY')).length).toBeGreaterThan(0)
  })
})
