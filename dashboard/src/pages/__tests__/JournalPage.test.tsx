import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '@/mocks/test-utils'
import { server } from '@/mocks/server'
import { JournalPage } from '@/pages/JournalPage'

describe('JournalPage', () => {
  it('renders page title', () => {
    renderWithProviders(<JournalPage />)
    expect(screen.getByText('Trade Journal')).toBeInTheDocument()
  })

  it('shows journal entries', async () => {
    renderWithProviders(<JournalPage />)
    expect(await screen.findByText(/Good momentum day/)).toBeInTheDocument()
  })

  it('links each entry to the trades closed that date', async () => {
    renderWithProviders(<JournalPage />)
    await screen.findByText(/Good momentum day/)
    expect(await screen.findByText('NIFTY')).toBeInTheDocument()
  })

  it('shows the new entry form', () => {
    renderWithProviders(<JournalPage />)
    expect(screen.getByText('New Entry')).toBeInTheDocument()
  })

  it('shows an error message instead of a blank list when the journal fails to load', async () => {
    server.use(http.get('*/api/journal', () => HttpResponse.error()))
    renderWithProviders(<JournalPage />)
    expect(await screen.findByText(/could not load journal/i)).toBeInTheDocument()
  })
})
