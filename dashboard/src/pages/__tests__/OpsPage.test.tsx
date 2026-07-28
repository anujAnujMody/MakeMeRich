import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '@/mocks/test-utils'
import { server } from '@/mocks/server'
import { OpsPage } from '@/pages/OpsPage'

describe('OpsPage', () => {
  it('renders page title', () => {
    renderWithProviders(<OpsPage />)
    expect(screen.getByText('Operations')).toBeInTheDocument()
  })

  it('shows broker connection status', async () => {
    renderWithProviders(<OpsPage />)
    expect(await screen.findByText('Connected')).toBeInTheDocument()
  })

  it('shows engine health: daily loss state and last poll freshness', async () => {
    renderWithProviders(<OpsPage />)
    expect(await screen.findByText(/normal/i)).toBeInTheDocument()
    expect(await screen.findByText(/12s ago/)).toBeInTheDocument()
  })

  it('shows the rejected orders log', async () => {
    renderWithProviders(<OpsPage />)
    expect(await screen.findByText('Rejected Orders Log')).toBeInTheDocument()
    expect(await screen.findByText('Insufficient margin')).toBeInTheDocument()
  })

  it('shows an error instead of a false "Down" while broker status is still loading or fails', async () => {
    server.use(http.get('*/api/broker-status', () => HttpResponse.error()))
    renderWithProviders(<OpsPage />)
    expect(await screen.findByText(/could not load broker status/i)).toBeInTheDocument()
    expect(screen.queryByText('Down')).not.toBeInTheDocument()
  })

  it('shows an error instead of a blank list when rejected orders fail to load', async () => {
    server.use(http.get('*/api/rejected-orders', () => HttpResponse.error()))
    renderWithProviders(<OpsPage />)
    expect(await screen.findByText(/could not load rejected orders/i)).toBeInTheDocument()
  })
})
