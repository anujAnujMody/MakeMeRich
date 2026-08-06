import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ErrorBoundary } from '@/components/ErrorBoundary'

function Bomb(): never {
  throw new Error('exploded')
}

describe('ErrorBoundary', () => {
  // React logs the error to the console on its own too — expected noise for this test.
  beforeEach(() => vi.spyOn(console, 'error').mockImplementation(() => {}))

  it('renders children normally when nothing throws', () => {
    render(
      <ErrorBoundary>
        <p>All good</p>
      </ErrorBoundary>,
    )
    expect(screen.getByText('All good')).toBeInTheDocument()
  })

  it('catches a render error and shows a fallback with the error message', () => {
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    )
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
    expect(screen.getByText('exploded')).toBeInTheDocument()
  })

  it('logs the caught error via componentDidCatch, not just the fallback UI', () => {
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    )
    // React's own dev warning also calls console.error — assert our explicit
    // log fired by checking for a call whose first arg is the actual Error.
    const loggedOurError = vi.mocked(console.error).mock.calls.some(
      (call) => call[0] instanceof Error && call[0].message === 'exploded',
    )
    expect(loggedOurError).toBe(true)
  })

  it('sends the user back to the dashboard instead of re-rendering the same crashing children', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign })
    const user = userEvent.setup()

    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    )
    await user.click(screen.getByRole('button', { name: /back to dashboard/i }))

    expect(assign).toHaveBeenCalledWith('/')
    vi.unstubAllGlobals()
  })
})
