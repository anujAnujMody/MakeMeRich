import { render, screen } from '@testing-library/react'
import { PatternLibraryCard } from '@/components/PatternLibraryCard'
import type { PatternLibraryEntry } from '@/types'

const patterns: PatternLibraryEntry[] = [
  { pattern: 'High VIX + Low PCR', condition: 'VIX > 14 & PCR < 0.85', winRate: 68, tradesTested: 23, lastObserved: '2026-07-28', status: 'working' },
]

describe('PatternLibraryCard', () => {
  it('shows discovered patterns with win rate and trades tested', () => {
    render(<PatternLibraryCard patterns={patterns} isLoading={false} />)
    expect(screen.getByText('High VIX + Low PCR')).toBeInTheDocument()
    expect(screen.getByText('68%')).toBeInTheDocument()
    expect(screen.getByText(/23 trades/)).toBeInTheDocument()
  })

  it('shows an empty-state message when nothing has been discovered', () => {
    render(<PatternLibraryCard patterns={[]} isLoading={false} />)
    expect(screen.getByText(/no patterns discovered yet/i)).toBeInTheDocument()
  })
})
