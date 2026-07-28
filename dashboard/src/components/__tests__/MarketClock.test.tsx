import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/mocks/test-utils'
import { MarketClock } from '@/components/MarketClock'

describe('MarketClock', () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }))
  afterEach(() => vi.useRealTimers())

  it('updates the displayed time as the clock ticks, not frozen at first render', async () => {
    renderWithProviders(<MarketClock />)
    const initialTime = screen.getAllByText(/IST/)[0].textContent

    await vi.advanceTimersByTimeAsync(61_000)

    const laterTime = screen.getAllByText(/IST/)[0].textContent
    expect(laterTime).not.toBe(initialTime)
  })
})
