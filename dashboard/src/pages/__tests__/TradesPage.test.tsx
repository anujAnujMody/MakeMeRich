import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '@/mocks/test-utils'
import { server } from '@/mocks/server'
import { TradesPage } from '@/pages/TradesPage'

describe('TradesPage', () => {
  it('shows open positions with a square-off action', async () => {
    renderWithProviders(<TradesPage />)
    expect(await screen.findByText('BANKNIFTY')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /square off/i }).length).toBeGreaterThan(0)
  })

  it("shows today's orders including their status", async () => {
    renderWithProviders(<TradesPage />)
    expect(await screen.findByText('FINNIFTY')).toBeInTheDocument()
    expect(screen.getByText('PENDING')).toBeInTheDocument()
  })

  it('shows rejected orders with the reason they were rejected', async () => {
    renderWithProviders(<TradesPage />)
    expect(await screen.findByText(/insufficient margin/i)).toBeInTheDocument()
  })

  it('shows closed trades', async () => {
    renderWithProviders(<TradesPage />)
    expect(await screen.findByText('Closed trades')).toBeInTheDocument()
  })

  it('asks for confirmation before squaring off a position', async () => {
    const user = userEvent.setup()
    renderWithProviders(<TradesPage />)
    const buttons = await screen.findAllByRole('button', { name: /square off/i })
    await user.click(buttons[0])
    expect(await screen.findByText(/are you sure/i)).toBeInTheDocument()
  })

  it('shows an empty-state message when there are no open positions', async () => {
    server.use(http.get('*/api/positions', () => HttpResponse.json([])))
    renderWithProviders(<TradesPage />)
    expect(await screen.findByText('Open positions')).toBeInTheDocument()
    expect(await screen.findByText(/no open positions/i)).toBeInTheDocument()
  })

  it('keeps the dialog open and shows an error when square-off fails, instead of silently closing', async () => {
    server.use(http.post('*/api/positions/squareoff', () => HttpResponse.error()))
    const user = userEvent.setup()
    renderWithProviders(<TradesPage />)

    const buttons = await screen.findAllByRole('button', { name: /square off/i })
    await user.click(buttons[0])
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: /^square off$/i }))

    expect(await screen.findByText(/square-off failed/i)).toBeInTheDocument()
    expect(screen.getByText(/are you sure/i)).toBeInTheDocument()
  })

  it('shows an error when cancelling an order fails', async () => {
    server.use(http.post('*/api/orders/cancel', () => HttpResponse.error()))
    const user = userEvent.setup()
    renderWithProviders(<TradesPage />)

    const cancelButtons = await screen.findAllByRole('button', { name: /^cancel$/i })
    await user.click(cancelButtons[0])
    expect(await screen.findByText(/could not cancel/i)).toBeInTheDocument()
  })

  it('does not label a flat (zero net quantity) position as Short', async () => {
    server.use(
      http.get('*/api/positions', () =>
        HttpResponse.json([
          { symbol: 'NIFTY', exchange: 'NFO', netQty: 0, netAvg: 100, ltp: 100, unrealisedPnl: 0 },
        ]),
      ),
    )
    renderWithProviders(<TradesPage />)
    await screen.findByText('NIFTY')
    expect(screen.queryByText('S')).not.toBeInTheDocument()
    expect(screen.queryByText('L')).not.toBeInTheDocument()
  })
})
