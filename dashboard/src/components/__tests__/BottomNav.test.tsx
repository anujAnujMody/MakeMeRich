import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { BottomNav } from '@/components/BottomNav'

function renderNav(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <BottomNav />
    </MemoryRouter>,
  )
}

describe('BottomNav', () => {
  it('shows the primary destinations as links', () => {
    renderNav()
    expect(screen.getByRole('link', { name: /dashboard/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /approve/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /trades/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /performance/i })).toBeInTheDocument()
  })

  it('is hidden on medium screens and up (md:hidden)', () => {
    const { container } = renderNav()
    expect(container.querySelector('nav')).toHaveClass('md:hidden')
  })

  it('opens a "More" overlay listing the remaining routes', async () => {
    const user = userEvent.setup()
    renderNav()
    await user.click(screen.getByRole('button', { name: /more/i }))
    expect(await screen.findByRole('link', { name: /strategies/i })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: /settings/i })).toBeInTheDocument()
  })

  it("marks the current route's link visually active", () => {
    renderNav('/approve')
    expect(screen.getByRole('link', { name: /approve/i })).toHaveAttribute('aria-current', 'page')
  })
})
