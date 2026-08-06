import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AppearanceCard } from '@/components/AppearanceCard'
import { useUIStore } from '@/stores/uiStore'

describe('AppearanceCard', () => {
  beforeEach(() => {
    useUIStore.setState({ theme: 'dark', sidebarPinned: false })
  })

  it('shows the current theme', () => {
    render(<AppearanceCard />)
    expect(screen.getByRole('switch', { name: /dark mode/i })).toHaveAttribute('aria-checked', 'true')
  })

  it('toggles the theme', async () => {
    const user = userEvent.setup()
    render(<AppearanceCard />)
    await user.click(screen.getByRole('switch', { name: /dark mode/i }))
    expect(useUIStore.getState().theme).toBe('light')
  })

  it('toggles sidebar pin', async () => {
    const user = userEvent.setup()
    render(<AppearanceCard />)
    await user.click(screen.getByRole('switch', { name: /pin sidebar/i }))
    expect(useUIStore.getState().sidebarPinned).toBe(true)
  })
})
