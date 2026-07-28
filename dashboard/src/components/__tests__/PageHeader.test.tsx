import { render, screen } from '@testing-library/react'
import { PageHeader } from '@/components/PageHeader'

describe('PageHeader', () => {
  it('shows the title', () => {
    render(<PageHeader title="Trades" />)
    expect(screen.getByRole('heading', { name: 'Trades' })).toBeInTheDocument()
  })

  it('shows the subtitle when given', () => {
    render(<PageHeader title="Trades" subtitle="Positions, orders and closed trades" />)
    expect(screen.getByText('Positions, orders and closed trades')).toBeInTheDocument()
  })

  it('renders the actions slot when given', () => {
    render(<PageHeader title="Trades" actions={<button>Refresh</button>} />)
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument()
  })
})
