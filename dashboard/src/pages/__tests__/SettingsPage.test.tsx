import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/mocks/test-utils'
import { SettingsPage } from '@/pages/SettingsPage'

describe('SettingsPage', () => {
  it('renders page title', () => {
    renderWithProviders(<SettingsPage />)
    expect(screen.getByText('Settings')).toBeInTheDocument()
  })

  it('shows the trading mode card', async () => {
    renderWithProviders(<SettingsPage />)
    expect(await screen.findByText(/DRY RUN/)).toBeInTheDocument()
  })

  it('shows the account guardrails card with capital', () => {
    renderWithProviders(<SettingsPage />)
    expect(screen.getByLabelText(/capital/i)).toBeInTheDocument()
  })

  it('shows the strategy config editor with all configured instruments, including SENSEX', async () => {
    renderWithProviders(<SettingsPage />)
    expect(await screen.findByText('SENSEX')).toBeInTheDocument()
    expect((await screen.findAllByText('NIFTY')).length).toBeGreaterThan(0)
    expect((await screen.findAllByText('BANKNIFTY')).length).toBeGreaterThan(0)
  })

  it('saving the strategy config round-trips through the mock, not just an echo', async () => {
    const user = userEvent.setup()
    const first = renderWithProviders(<SettingsPage />)
    const maxTradesInput = await screen.findByLabelText(/max trades per day/i)
    await user.clear(maxTradesInput)
    await user.type(maxTradesInput, '25')
    await user.click(screen.getByRole('button', { name: /^save$/i }))
    // Wait for the mutation to settle (button label reverts once isSaving flips back).
    await screen.findByRole('button', { name: /^save$/i })
    first.unmount()

    // remount and confirm the saved value actually persisted server-side,
    // not just that the mutation resolved — value 25 must come back from
    // the mock's own stored config, not a locally-cached echo.
    renderWithProviders(<SettingsPage />)
    expect(await screen.findByLabelText(/max trades per day/i)).toHaveValue(25)
  })

  it('shows the automation section with autonomy and research toggles', () => {
    renderWithProviders(<SettingsPage />)
    expect(screen.getByRole('switch', { name: /full-auto/i })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: /research agent/i })).toBeInTheDocument()
  })

  it('shows the AI models card with accessibly-labelled selects', () => {
    renderWithProviders(<SettingsPage />)
    expect(screen.getByLabelText(/reasoning model/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/cheap model/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/fallback model/i)).toBeInTheDocument()
  })

  it('shows the appearance card', () => {
    renderWithProviders(<SettingsPage />)
    expect(screen.getByRole('switch', { name: /dark mode/i })).toBeInTheDocument()
  })
})
