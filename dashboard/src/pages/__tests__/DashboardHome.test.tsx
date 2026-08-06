import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '@/mocks/test-utils'
import { server } from '@/mocks/server'
import { DashboardHome } from '@/pages/DashboardHome'

describe('DashboardHome', () => {
  it('shows the mode banner, bot status, and today\'s P&L once loaded', async () => {
    renderWithProviders(<DashboardHome />)

    expect(await screen.findByText(/DRY RUN/)).toBeInTheDocument()
    expect(screen.getByText('Live')).toBeInTheDocument()
    expect(screen.getByText(/\+₹1,240\.50/)).toBeInTheDocument()
  })

  it('shows the decision timeline with today\'s evaluations', async () => {
    renderWithProviders(<DashboardHome />)
    expect(await screen.findByText(/What the bot did today/)).toBeInTheDocument()
    expect(await screen.findByText(/blocked by volume filter/)).toBeInTheDocument()
  })

  it('shows open positions', async () => {
    renderWithProviders(<DashboardHome />)
    expect(await screen.findByText('Open positions')).toBeInTheDocument()
    expect((await screen.findAllByText('SENSEX 81400 CE')).length).toBeGreaterThanOrEqual(1)
    expect(await screen.findByText(/\+₹186\.00/)).toBeInTheDocument()
  })

  it('shows a loading state before the snapshot arrives', () => {
    renderWithProviders(<DashboardHome />)
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('shows an error state if the snapshot fails to load', async () => {
    server.use(http.get('*/api/dashboard/snapshot', () => HttpResponse.error()))
    renderWithProviders(<DashboardHome />)
    expect(await screen.findByText(/could not load/i)).toBeInTheDocument()
  })

  it('shows an empty-state message when no signals have been evaluated yet', async () => {
    server.use(http.get('*/api/decisions/today', () => HttpResponse.json([])))
    renderWithProviders(<DashboardHome />)
    expect(await screen.findByText(/no signals evaluated yet/i)).toBeInTheDocument()
  })
})
