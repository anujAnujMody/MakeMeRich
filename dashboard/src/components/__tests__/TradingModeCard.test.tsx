import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/mocks/test-utils'
import { TradingModeCard } from '@/components/TradingModeCard'

describe('TradingModeCard', () => {
  it('shows the current mode', async () => {
    renderWithProviders(<TradingModeCard />)
    expect(await screen.findByText(/DRY RUN/)).toBeInTheDocument()
  })

  it('requires typing GO LIVE to confirm switching to live mode', async () => {
    const user = userEvent.setup()
    renderWithProviders(<TradingModeCard />)
    await screen.findByText(/DRY RUN/)

    await user.click(screen.getByRole('button', { name: /switch to live/i }))
    const confirmInput = await screen.findByLabelText(/type go live to confirm/i)
    const confirmButton = screen.getByRole('button', { name: /^confirm switch to live$/i })

    expect(confirmButton).toBeDisabled()
    await user.type(confirmInput, 'GO LIVE')
    expect(confirmButton).toBeEnabled()

    await user.click(confirmButton)
    expect(await screen.findByText(/^LIVE/)).toBeInTheDocument()
  })

  it('shows engine run state with a pause control', async () => {
    renderWithProviders(<TradingModeCard />)
    expect(await screen.findByRole('button', { name: /pause/i })).toBeInTheDocument()
  })
})
