import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CycleTimeline } from '@/components/CycleTimeline'
import type { CycleEvaluation } from '@/types/dashboard-snapshot'

const evaluations: CycleEvaluation[] = [
  {
    id: 'ev-2',
    timestamp: '2026-07-28T09:47:11Z',
    strategy: 'orbs',
    instrument: 'SENSEX 81400 CE',
    verdict: 'traded',
    reason: 'all checks passed',
    conditions: [{ label: 'x', required: 'y', actual: 'y', passed: true, evaluated: true }],
  },
  {
    id: 'ev-1',
    timestamp: '2026-07-28T09:31:04Z',
    strategy: 'orbs',
    instrument: 'NIFTY',
    verdict: 'skipped',
    reason: 'range too wide',
    conditions: [{ label: 'x', required: 'y', actual: 'z', passed: false, evaluated: true }],
  },
]

describe('CycleTimeline', () => {
  it('shows a one-line summary of how many signals were evaluated, traded, and skipped', () => {
    render(<CycleTimeline evaluations={evaluations} />)
    const summary = screen.getByText(/signals evaluated/i).closest('p')!
    expect(summary).toHaveTextContent('2 signals evaluated')
    expect(summary).toHaveTextContent('1 traded')
    expect(summary).toHaveTextContent('1 skipped')
    // No error in the fixture — the count is 0 and the "errors" clause is
    // conditionally rendered, so it must not appear at all.
    expect(summary).not.toHaveTextContent(/error/i)
  })

  it('shows the error count only when at least one evaluation errored', () => {
    render(<CycleTimeline evaluations={[...evaluations, { ...evaluations[0], id: 'ev-3', verdict: 'error', reason: 'broker timeout' }]} />)
    const summary = screen.getByText(/signals evaluated/i).closest('p')!
    expect(summary).toHaveTextContent('1 errors')
  })

  it('renders one DecisionCard per evaluation', () => {
    render(<CycleTimeline evaluations={evaluations} />)
    expect(screen.getByText(/all checks passed/)).toBeInTheDocument()
    expect(screen.getByText(/range too wide/)).toBeInTheDocument()
  })

  it('filters to only traded cards when the Traded chip is selected', async () => {
    const user = userEvent.setup()
    render(<CycleTimeline evaluations={evaluations} />)

    await user.click(screen.getByRole('button', { name: /^Traded$/ }))

    expect(screen.getByText(/all checks passed/)).toBeInTheDocument()
    expect(screen.queryByText(/range too wide/)).not.toBeInTheDocument()
  })

  it('filters to only skipped cards when the Skipped chip is selected', async () => {
    const user = userEvent.setup()
    render(<CycleTimeline evaluations={evaluations} />)

    await user.click(screen.getByRole('button', { name: /^Skipped$/ }))

    expect(screen.queryByText(/all checks passed/)).not.toBeInTheDocument()
    expect(screen.getByText(/range too wide/)).toBeInTheDocument()
  })

  it('shows an empty-state message when there are no evaluations yet', () => {
    render(<CycleTimeline evaluations={[]} />)
    expect(screen.getByText(/no signals evaluated yet/i)).toBeInTheDocument()
  })

  it('shows a "nothing matches" message when a filter has no results, not a blank list', async () => {
    const user = userEvent.setup()
    render(<CycleTimeline evaluations={evaluations} />)

    await user.click(screen.getByRole('button', { name: /^Errors$/ }))

    expect(screen.getByText(/nothing matches this filter/i)).toBeInTheDocument()
  })
})
