import { render, screen } from '@testing-library/react'
import { PipelineStrip } from '@/components/PipelineStrip'
import type { PipelineStageInfo } from '@/types/dashboard-snapshot'

const stages: PipelineStageInfo[] = [
  { key: 'fetch', label: 'Fetch', state: 'done', durationLabel: '0.4s' },
  { key: 'analyze', label: 'Analyze', state: 'done', durationLabel: '0.2s' },
  { key: 'risk', label: 'Risk', state: 'active' },
  { key: 'decide', label: 'Decide', state: 'pending' },
  { key: 'act', label: 'Act', state: 'pending' },
]

describe('PipelineStrip', () => {
  it('renders every stage label in order', () => {
    render(<PipelineStrip stages={stages} />)
    const labels = screen.getAllByTestId('pipeline-stage-label').map((el) => el.textContent)
    expect(labels).toEqual(['Fetch', 'Analyze', 'Risk', 'Decide', 'Act'])
  })

  it('shows the duration for completed stages', () => {
    render(<PipelineStrip stages={stages} />)
    expect(screen.getByText('0.4s')).toBeInTheDocument()
    expect(screen.getByText('0.2s')).toBeInTheDocument()
  })

  it('marks the active stage distinctly so it reads as "happening now"', () => {
    render(<PipelineStrip stages={stages} />)
    const risk = screen.getByTestId('pipeline-stage-risk')
    expect(risk.getAttribute('data-state')).toBe('active')
  })

  it('does not claim a duration for pending stages', () => {
    render(<PipelineStrip stages={stages} />)
    const decide = screen.getByTestId('pipeline-stage-decide')
    expect(decide.textContent).toMatch(/—/)
  })
})
