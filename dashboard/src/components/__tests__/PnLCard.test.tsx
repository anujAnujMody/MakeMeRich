import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/mocks/test-utils'
import { PnLCard } from '@/components/PnLCard'
import { server } from '@/mocks/server'
import { http, HttpResponse } from 'msw'

describe('PnLCard', () => {
  it('renders total PnL with green color for positive', async () => {
    renderWithProviders(<PnLCard />)
    const total = await screen.findByText(/₹\s*450/)
    expect(total).toBeInTheDocument()
    expect(total).toHaveClass('text-gain')
  })

  it('renders win rate percentage', async () => {
    renderWithProviders(<PnLCard />)
    expect(await screen.findByText(/66\.67%/)).toBeInTheDocument()
  })

  it('renders total trade count', async () => {
    renderWithProviders(<PnLCard />)
    expect(await screen.findByText(/Total Trades:\s*3/)).toBeInTheDocument()
  })

  it('renders winning and losing trade counts', async () => {
    renderWithProviders(<PnLCard />)
    expect(await screen.findByText(/Winning:\s*2/)).toBeInTheDocument()
    expect(await screen.findByText(/Losing:\s*1/)).toBeInTheDocument()
  })

  it('renders avg win and avg loss', async () => {
    renderWithProviders(<PnLCard />)
    expect(await screen.findByText(/Avg Win:.*₹\s*750/)).toBeInTheDocument()
    expect(await screen.findByText(/Avg Loss:.*₹\s*300/)).toBeInTheDocument()
  })

  it('renders max drawdown', async () => {
    renderWithProviders(<PnLCard />)
    expect(await screen.findByText(/Max Drawdown:.*₹\s*300/)).toBeInTheDocument()
  })

  it('shows loading state initially', () => {
    renderWithProviders(<PnLCard />)
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('shows error state on API failure', async () => {
    server.use(
      http.get('*/api/pnl', () => HttpResponse.error()),
    )
    renderWithProviders(<PnLCard />)
    expect(await screen.findByText(/error|failed/i)).toBeInTheDocument()
  })

  it('shows empty state when no data', async () => {
    server.use(
      http.get('*/api/pnl', () => HttpResponse.json(null)),
    )
    renderWithProviders(<PnLCard />)
    expect(await screen.findByText(/no data|no trades|no pnl/i)).toBeInTheDocument()
  })
})
