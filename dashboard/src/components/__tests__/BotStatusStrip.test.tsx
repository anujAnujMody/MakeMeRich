import { act, render, screen } from '@testing-library/react'
import { BotStatusStrip } from '@/components/BotStatusStrip'

describe('BotStatusStrip', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-28T10:42:07.000Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it.each([
    ['live', 'Live'],
    ['stale', 'Stale'],
    ['paused', 'Paused'],
  ] as const)('shows the exact status word "%s" for status=%s', (status, word) => {
    render(<BotStatusStrip status={status} asOf={new Date().toISOString()} nextCheckInSeconds={47} />)
    expect(screen.getByText(word)).toBeInTheDocument()
  })

  it('shows "just now" immediately after a fresh update', () => {
    render(<BotStatusStrip status="live" asOf={new Date().toISOString()} nextCheckInSeconds={47} />)
    expect(screen.getByText(/just now/)).toBeInTheDocument()
  })

  it('ticks the elapsed time upward every second without new props', () => {
    const asOf = new Date().toISOString()
    render(<BotStatusStrip status="live" asOf={asOf} nextCheckInSeconds={47} />)

    act(() => {
      vi.advanceTimersByTime(5000)
    })

    expect(screen.getByText(/5s ago/)).toBeInTheDocument()
  })

  it('counts the next-check timer down every second', () => {
    render(<BotStatusStrip status="live" asOf={new Date().toISOString()} nextCheckInSeconds={47} />)
    expect(screen.getByText(/47s/)).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(3000)
    })

    expect(screen.getByText(/44s/)).toBeInTheDocument()
  })

  it('never shows a negative countdown', () => {
    render(<BotStatusStrip status="live" asOf={new Date().toISOString()} nextCheckInSeconds={2} />)

    act(() => {
      vi.advanceTimersByTime(10_000)
    })

    expect(screen.queryByText(/-/)).not.toBeInTheDocument()
    expect(screen.getByText(/now/)).toBeInTheDocument()
  })
})
