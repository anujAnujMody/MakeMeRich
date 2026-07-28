import { render, screen } from '@testing-library/react'
import { ShadowComparisonList } from '@/components/ShadowComparisonList'
import type { ShadowComparison } from '@/types/learning'

const comparisons: ShadowComparison[] = [
  { id: 'sc-1', timestamp: '2026-07-28T09:47:11Z', instrument: 'SENSEX 81400 CE', actualVerdict: 'traded', modelVerdict: 'would trade', agreed: true },
  { id: 'sc-2', timestamp: '2026-07-28T10:05:32Z', instrument: 'NIFTY 24200 PE', actualVerdict: 'skipped', modelVerdict: 'would trade', agreed: false },
]

describe('ShadowComparisonList', () => {
  it('shows what the bot actually did next to what the model would have decided', () => {
    render(<ShadowComparisonList comparisons={comparisons} />)
    expect(screen.getByText('SENSEX 81400 CE')).toBeInTheDocument()
    expect(screen.getByText('NIFTY 24200 PE')).toBeInTheDocument()
    expect(screen.getAllByText(/would trade/i).length).toBe(2)
  })

  it('flags disagreements between the model and what actually happened', () => {
    render(<ShadowComparisonList comparisons={comparisons} />)
    const row = screen.getByText('NIFTY 24200 PE').closest('[data-agreed]')
    expect(row).toHaveAttribute('data-agreed', 'false')
  })

  it('shows an empty-state message when there is no shadow history yet', () => {
    render(<ShadowComparisonList comparisons={[]} />)
    expect(screen.getByText(/no shadow.*yet/i)).toBeInTheDocument()
  })
})
