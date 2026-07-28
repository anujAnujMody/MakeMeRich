import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/mocks/test-utils'
import { JournalEntryTrades } from '@/components/JournalEntryTrades'

describe('JournalEntryTrades', () => {
  it('shows the trades closed on this date', async () => {
    renderWithProviders(<JournalEntryTrades date="2026-07-25" />)
    expect(await screen.findByText('NIFTY')).toBeInTheDocument()
  })

  it('shows nothing to link when no trades closed that day', async () => {
    renderWithProviders(<JournalEntryTrades date="2026-01-01" />)
    expect(await screen.findByText(/no trades closed this day/i)).toBeInTheDocument()
  })
})
