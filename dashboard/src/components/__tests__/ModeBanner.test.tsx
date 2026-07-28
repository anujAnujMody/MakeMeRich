import { render, screen } from '@testing-library/react'
import { ModeBanner } from '@/components/ModeBanner'

describe('ModeBanner', () => {
  it('shows a clear DRY RUN label and explanation in paper mode', () => {
    render(<ModeBanner mode="dry-run" />)
    expect(screen.getByText(/DRY RUN/)).toBeInTheDocument()
    expect(screen.getByText(/no real orders/i)).toBeInTheDocument()
  })

  it('shows a clear LIVE label and warning in live mode', () => {
    render(<ModeBanner mode="live" />)
    expect(screen.getByText(/LIVE/)).toBeInTheDocument()
    expect(screen.getByText(/real orders|real money/i)).toBeInTheDocument()
  })

  it('is always present in the document regardless of mode — never silently hidden', () => {
    const { rerender } = render(<ModeBanner mode="dry-run" />)
    expect(screen.getByRole('status')).toBeInTheDocument()

    rerender(<ModeBanner mode="live" />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('carries a distinct data-mode attribute so live mode can be styled unmistakably differently', () => {
    const { rerender, container } = render(<ModeBanner mode="dry-run" />)
    expect(container.querySelector('[data-mode="dry-run"]')).toBeInTheDocument()

    rerender(<ModeBanner mode="live" />)
    expect(container.querySelector('[data-mode="live"]')).toBeInTheDocument()
  })
})
