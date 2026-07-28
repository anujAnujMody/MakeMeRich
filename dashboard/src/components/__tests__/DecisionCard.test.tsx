import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DecisionCard } from '@/components/DecisionCard'
import type { ConditionResult } from '@/components/DecisionCard/types'

const passingConditions: ConditionResult[] = [
  {
    label: '5-min close beyond opening range',
    required: '> 81,350.0',
    actual: '81,378.9',
    passed: true,
    evaluated: true,
  },
  {
    label: 'Volume above average',
    required: '≥ 1.5x avg',
    actual: '1.9x',
    passed: true,
    evaluated: true,
  },
]

const skippedConditions: ConditionResult[] = [
  {
    label: '5-min close beyond opening range',
    required: '> 81,350.0',
    actual: '81,362.4',
    passed: true,
    evaluated: true,
  },
  {
    label: 'Volume above average',
    required: '≥ 1.5x avg',
    actual: '0.82x',
    passed: false,
    evaluated: true,
  },
  {
    label: 'Daily loss limit not breached',
    required: 'not breached',
    actual: 'not reached',
    passed: false,
    evaluated: false,
  },
]

function renderCard(
  verdict: 'traded' | 'skipped' | 'error',
  reason: string,
  conditions: ConditionResult[],
) {
  return render(
    <DecisionCard.Root verdict={verdict}>
      <DecisionCard.Summary time="10:05:32" instrument="SENSEX" text={reason} />
      <DecisionCard.Conditions items={conditions} />
    </DecisionCard.Root>,
  )
}

describe('DecisionCard', () => {
  it('shows a plain-English one-line summary with the verdict, time, and instrument', () => {
    renderCard('skipped', 'ORB breakout — blocked by volume filter, needed 1.5x avg, got 0.82x', skippedConditions)

    expect(screen.getByText(/10:05:32/)).toBeInTheDocument()
    expect(screen.getByText(/SENSEX/)).toBeInTheDocument()
    expect(screen.getByText(/blocked by volume filter/)).toBeInTheDocument()
  })

  it.each([
    ['traded', 'Traded'],
    ['skipped', 'Skipped'],
    ['error', 'Error'],
  ] as const)('renders a %s verdict badge with a text label, not color alone', (verdict, label) => {
    renderCard(verdict, 'reason text', passingConditions)
    expect(screen.getByText(new RegExp(label, 'i'))).toBeInTheDocument()
  })

  it('does not render the condition list until expanded', () => {
    renderCard('skipped', 'reason text', skippedConditions)
    expect(screen.queryByText('Volume above average')).not.toBeInTheDocument()
  })

  it('expands to show every condition with its required and actual value on click', async () => {
    const user = userEvent.setup()
    renderCard('skipped', 'reason text', skippedConditions)

    await user.click(screen.getByRole('button', { name: /reason text/i }))

    expect(screen.getByText('Volume above average')).toBeInTheDocument()
    expect(screen.getByText('≥ 1.5x avg')).toBeInTheDocument()
    expect(screen.getByText('0.82x')).toBeInTheDocument()
  })

  it('is keyboard-operable — Enter on the focused summary toggles the condition list', async () => {
    const user = userEvent.setup()
    renderCard('skipped', 'reason text', skippedConditions)

    await user.tab()
    expect(screen.getByRole('button', { name: /reason text/i })).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(screen.getByText('Volume above average')).toBeInTheDocument()
  })

  it('marks a passed condition distinctly from a failed one', async () => {
    const user = userEvent.setup()
    renderCard('skipped', 'reason text', skippedConditions)
    await user.click(screen.getByRole('button', { name: /reason text/i }))

    const passRow = screen.getByText('5-min close beyond opening range').closest('tr')
    const failRow = screen.getByText('Volume above average').closest('tr')
    expect(passRow).not.toBeNull()
    expect(failRow).not.toBeNull()
    expect(passRow?.getAttribute('data-state')).toBe('pass')
    expect(failRow?.getAttribute('data-state')).toBe('fail')
  })

  it('renders a short-circuited (not evaluated) condition as neither pass nor fail', async () => {
    const user = userEvent.setup()
    renderCard('skipped', 'reason text', skippedConditions)
    await user.click(screen.getByRole('button', { name: /reason text/i }))

    const notReachedRow = screen.getByText('Daily loss limit not breached').closest('tr')
    expect(notReachedRow?.getAttribute('data-state')).toBe('not-reached')
    expect(notReachedRow?.getAttribute('data-state')).not.toBe('pass')
    expect(notReachedRow?.getAttribute('data-state')).not.toBe('fail')
  })
})
