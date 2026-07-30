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

  it('shows the account guardrails card with capital', async () => {
    renderWithProviders(<SettingsPage />)
    expect(await screen.findByLabelText(/capital/i)).toBeInTheDocument()
  })

  it('shows the strategy config editor with all configured instruments, including SENSEX', async () => {
    renderWithProviders(<SettingsPage />)
    expect(await screen.findByText('SENSEX')).toBeInTheDocument()
    expect((await screen.findAllByText('NIFTY')).length).toBeGreaterThan(0)
    expect((await screen.findAllByText('BANKNIFTY')).length).toBeGreaterThan(0)
  })

  it('saving account guardrails round-trips through the mock, not just an echo', async () => {
    const user = userEvent.setup()
    const first = renderWithProviders(<SettingsPage />)
    const maxTradesInput = await screen.findByLabelText(/max trades \/ day/i)
    await user.clear(maxTradesInput)
    await user.type(maxTradesInput, '25')
    const saveButtons = await screen.findAllByRole('button', { name: /^save$/i })
    await user.click(saveButtons[0])
    // Wait for the mutation to settle (button label reverts once isSaving flips back).
    await screen.findAllByRole('button', { name: /^save$/i })
    first.unmount()

    // remount and confirm the saved value actually persisted server-side,
    // not just that the mutation resolved — value 25 must come back from
    // the mock's own stored config, not a locally-cached echo.
    renderWithProviders(<SettingsPage />)
    expect(await screen.findByLabelText(/max trades \/ day/i)).toHaveValue(25)
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
