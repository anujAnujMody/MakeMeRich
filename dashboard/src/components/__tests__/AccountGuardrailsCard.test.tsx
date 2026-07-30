import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AccountGuardrailsCard } from '@/components/AccountGuardrailsCard'
import { renderWithProviders } from '@/mocks/test-utils'
import { resetMockState } from '@/mocks/handlers'

describe('AccountGuardrailsCard', () => {
  beforeEach(resetMockState)

  it('shows the capital field', async () => {
    renderWithProviders(<AccountGuardrailsCard />)
    expect(await screen.findByLabelText(/capital/i)).toHaveValue(20000)
  })

  it('shows a live rupee equivalent next to percent-based fields', async () => {
    renderWithProviders(<AccountGuardrailsCard />)
    // 20000 capital x 40% max position size = 8000
    expect(await screen.findByText(/₹8,000\.00/)).toBeInTheDocument()
  })

  it('updates the rupee equivalent when capital changes', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AccountGuardrailsCard />)
    const capitalInput = await screen.findByLabelText(/capital/i)
    await user.clear(capitalInput)
    await user.type(capitalInput, '30000')
    // 30000 x 40% = 12000
    expect(await screen.findByText(/₹12,000\.00/)).toBeInTheDocument()
  })

  it('accepts decimal values for percent fields, not just integers', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AccountGuardrailsCard />)
    const drawdownInput = await screen.findByLabelText(/max drawdown/i)
    await user.clear(drawdownInput)
    await user.type(drawdownInput, '12.5')
    expect(drawdownInput).toHaveValue(12.5)
  })

  it('rounds max trades/day to a whole number — the backend rejects a non-integral value', async () => {
    // Regression, found by review: this field used the same parseFloat as
    // the percent fields, so typing "12.5" would PUT a non-integer to a
    // backend schema typed as `int`, failing with an unexplained 422.
    const user = userEvent.setup()
    renderWithProviders(<AccountGuardrailsCard />)
    const tradesInput = await screen.findByLabelText(/max trades/i)
    await user.clear(tradesInput)
    await user.type(tradesInput, '12.5')
    expect(tradesInput).toHaveValue(12)
  })

  it('shows the rupee suffix on the max daily loss field', async () => {
    renderWithProviders(<AccountGuardrailsCard />)
    expect(await screen.findByText(/max daily loss \(₹\)/i)).toBeInTheDocument()
  })

  it('saving persists through the mock, not just a local echo', async () => {
    const user = userEvent.setup()
    const first = renderWithProviders(<AccountGuardrailsCard />)
    const capitalInput = await screen.findByLabelText(/capital/i)
    await user.clear(capitalInput)
    await user.type(capitalInput, '35000')
    await user.click(screen.getByRole('button', { name: /^save$/i }))
    await screen.findByRole('button', { name: /^save$/i })
    first.unmount()

    renderWithProviders(<AccountGuardrailsCard />)
    expect(await screen.findByLabelText(/capital/i)).toHaveValue(35000)
  })
})
