import { render, screen } from '@testing-library/react'
import { StatTile } from '@/components/StatTile'

describe('StatTile', () => {
  it('shows the label and value', () => {
    render(<StatTile label="Positions" value="2 / 5" />)
    expect(screen.getByText('Positions')).toBeInTheDocument()
    expect(screen.getByText('2 / 5')).toBeInTheDocument()
  })
})
