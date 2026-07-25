import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/mocks/test-utils'
import { TradeLogTable } from '@/components/TradeLogTable'
import { server } from '@/mocks/server'
import { http, HttpResponse } from 'msw'

describe('TradeLogTable', () => {
  it('renders trade rows with symbol', async () => {
    renderWithProviders(<TradeLogTable />)
    const banknifty = await screen.findAllByText(/BANKNIFTY/)
    const nifty = await screen.findAllByText(/NIFTY/)
    expect(banknifty.length).toBeGreaterThanOrEqual(2)
    expect(nifty.length).toBeGreaterThanOrEqual(1)
  })

  it('renders transaction direction for each trade', async () => {
    renderWithProviders(<TradeLogTable />)
    const buys = await screen.findAllByText(/BUY/)
    const sells = await screen.findAllByText(/SELL/)
    expect(buys.length).toBeGreaterThanOrEqual(2)
    expect(sells.length).toBeGreaterThanOrEqual(1)
  })

  it('renders quantity and price', async () => {
    renderWithProviders(<TradeLogTable />)
    const qtys = await screen.findAllByText('15')
    expect(qtys.length).toBeGreaterThanOrEqual(2)
    expect(await screen.findByText('25')).toBeInTheDocument()
    expect(await screen.findByText('48,200')).toBeInTheDocument()
  })

  it('shows PnL with green for positive', async () => {
    renderWithProviders(<TradeLogTable />)
    const pnl = await screen.findByText('+750')
    expect(pnl).toBeInTheDocument()
    expect(pnl).toHaveClass('text-green-600')
  })

  it('shows PnL with red for negative', async () => {
    renderWithProviders(<TradeLogTable />)
    const pnl = await screen.findByText('-300')
    expect(pnl).toBeInTheDocument()
    expect(pnl).toHaveClass('text-red-600')
  })

  it('shows loading state', () => {
    renderWithProviders(<TradeLogTable />)
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('shows empty state when no trades', async () => {
    server.use(
      http.get('*/api/trades', () => HttpResponse.json([])),
    )
    renderWithProviders(<TradeLogTable />)
    expect(await screen.findByText(/no trades|empty/i)).toBeInTheDocument()
  })

  it('shows error state on API failure', async () => {
    server.use(
      http.get('*/api/trades', () => HttpResponse.error()),
    )
    renderWithProviders(<TradeLogTable />)
    expect(await screen.findByText(/error|failed/i)).toBeInTheDocument()
  })
})
