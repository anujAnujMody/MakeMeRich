import { render, screen } from '@testing-library/react'
import { MockDataBanner } from '@/components/MockDataBanner'

describe('MockDataBanner', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('warns loudly when mocks are active (the default dev/test env)', () => {
    render(<MockDataBanner />)
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText(/MOCK DATA/)).toBeInTheDocument()
  })

  it('renders nothing once VITE_USE_MOCKS=false (yarn dev:real / a real build)', () => {
    vi.stubEnv('VITE_USE_MOCKS', 'false')
    const { container } = render(<MockDataBanner />)
    expect(container).toBeEmptyDOMElement()
  })
})
