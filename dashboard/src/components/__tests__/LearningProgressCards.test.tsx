import { render, screen } from '@testing-library/react'
import { LearningProgressCards } from '@/components/LearningProgressCards'
import type { LearningProgress } from '@/types'

const progress: LearningProgress = {
  winRateTrend: [58, 62, 60, 63, 65, 68, 72],
  totalStrategiesDiscovered: 8,
  totalStrategiesRetired: 3,
  avgProfitPerTrade: 245,
  dates: ['Jul 20', 'Jul 21', 'Jul 22', 'Jul 23', 'Jul 24', 'Jul 25', 'Jul 26'],
}

describe('LearningProgressCards', () => {
  it('shows the latest win rate, strategies found/retired, and avg profit', () => {
    render(<LearningProgressCards progress={progress} />)
    expect(screen.getByText('72%')).toBeInTheDocument()
    expect(screen.getByText('8')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('+₹245.00')).toBeInTheDocument()
  })

  it('shows the win rate trend chart', () => {
    render(<LearningProgressCards progress={progress} />)
    expect(screen.getByText('Win Rate Trend (7 days)')).toBeInTheDocument()
  })

  it('shows a dash instead of "undefined%" when there is no win rate history yet', () => {
    render(<LearningProgressCards progress={{ ...progress, winRateTrend: [] }} />)
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument()
  })
})
