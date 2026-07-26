import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/mocks/test-utils'
import { SelfLearning } from '@/pages/SelfLearning'
import { server } from '@/mocks/server'
import { http, HttpResponse } from 'msw'

describe('SelfLearning', () => {
  it('renders page header', () => {
    renderWithProviders(<SelfLearning />)
    expect(screen.getByText('Self-Learning')).toBeInTheDocument()
  })

  it('renders tab navigation', () => {
    renderWithProviders(<SelfLearning />)
    expect(screen.getByText('Overview')).toBeInTheDocument()
    expect(screen.getByText('By Strategy')).toBeInTheDocument()
    expect(screen.getByText('Optimizer')).toBeInTheDocument()
  })

  it('shows loading state initially', () => {
    renderWithProviders(<SelfLearning />)
    expect(screen.getByText(/loading stats/i)).toBeInTheDocument()
  })

  it('shows strategy filter dropdown', () => {
    renderWithProviders(<SelfLearning />)
    expect(screen.getByLabelText(/strategy/i)).toBeInTheDocument()
  })

  it('renders stat cards when data loads', async () => {
    renderWithProviders(<SelfLearning />)
    expect(await screen.findByText('Total Trades')).toBeInTheDocument()
    expect(await screen.findByText('Win Rate')).toBeInTheDocument()
    expect(await screen.findByText('Total P&L')).toBeInTheDocument()
    expect(await screen.findByText('Sharpe Ratio')).toBeInTheDocument()
  })

  it('renders by-hour breakdown', async () => {
    renderWithProviders(<SelfLearning />)
    expect(await screen.findByText('By Hour')).toBeInTheDocument()
    expect(await screen.findByText('9:00')).toBeInTheDocument()
    expect(await screen.findByText('10:00')).toBeInTheDocument()
  })

  it('renders by-day breakdown', async () => {
    renderWithProviders(<SelfLearning />)
    expect(await screen.findByText('By Day')).toBeInTheDocument()
  })

  it('switches to by-strategy tab', async () => {
    renderWithProviders(<SelfLearning />)
    const btn = await screen.findByText('By Strategy')
    btn.click()
    expect(await screen.findByText('orbs')).toBeInTheDocument()
  })

  it('switches to optimizer tab', async () => {
    renderWithProviders(<SelfLearning />)
    const btn = await screen.findByText('Optimizer')
    btn.click()
    expect(await screen.findByText('Parameter Optimizer')).toBeInTheDocument()
  })

  it('shows error state on API failure', async () => {
    server.use(
      http.get('*/api/learning/stats', () => HttpResponse.error()),
    )
    renderWithProviders(<SelfLearning />)
    expect(await screen.findByText(/error loading stats/i)).toBeInTheDocument()
  })
})
