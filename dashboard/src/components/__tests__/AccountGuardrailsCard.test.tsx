import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AccountGuardrailsCard } from '@/components/AccountGuardrailsCard'
import { useSettingsStore } from '@/stores/settingsStore'

function resetStore() {
  useSettingsStore.setState({
    capitalRupees: 10000,
    maxDailyLoss: 500,
    maxPositionSizePct: 40,
    maxDrawdownPct: 15,
    maxTradesPerDay: 10,
  })
}

describe('AccountGuardrailsCard', () => {
  beforeEach(resetStore)

  it('shows the capital field', () => {
    render(<AccountGuardrailsCard />)
    expect(screen.getByLabelText(/capital/i)).toHaveValue(10000)
  })

  it('shows a live rupee equivalent next to percent-based fields', () => {
    render(<AccountGuardrailsCard />)
    expect(screen.getByText(/₹4,000\.00/)).toBeInTheDocument()
  })

  it('updates the rupee equivalent when capital changes', async () => {
    const user = userEvent.setup()
    render(<AccountGuardrailsCard />)
    const capitalInput = screen.getByLabelText(/capital/i)
    await user.clear(capitalInput)
    await user.type(capitalInput, '20000')
    expect(await screen.findByText(/₹8,000\.00/)).toBeInTheDocument()
  })

  it('accepts decimal values for percent fields, not just integers', async () => {
    const user = userEvent.setup()
    render(<AccountGuardrailsCard />)
    const drawdownInput = screen.getByLabelText(/max drawdown/i)
    await user.clear(drawdownInput)
    await user.type(drawdownInput, '12.5')
    expect(useSettingsStore.getState().maxDrawdownPct).toBe(12.5)
  })

  it('shows the rupee suffix on the max daily loss field', () => {
    render(<AccountGuardrailsCard />)
    expect(screen.getByText(/max daily loss \(₹\)/i)).toBeInTheDocument()
  })
})
