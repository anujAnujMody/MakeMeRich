import { render, screen } from '@testing-library/react'
import { StrategyRuleList } from '@/components/StrategyRuleList'

describe('StrategyRuleList', () => {
  it('shows each strategy parameter as a plain-English label and its value', () => {
    render(
      <StrategyRuleList
        instruments={['NIFTY', 'BANKNIFTY']}
        params={{ opening_minutes: 15, volume_filter_x: 1.5, range_width_max: 200 }}
      />,
    )
    expect(screen.getByText(/opening minutes/i)).toBeInTheDocument()
    expect(screen.getByText('15')).toBeInTheDocument()
    expect(screen.getByText(/volume filter x/i)).toBeInTheDocument()
    expect(screen.getByText('1.5')).toBeInTheDocument()
  })

  it('lists the instruments this strategy trades', () => {
    render(<StrategyRuleList instruments={['NIFTY', 'BANKNIFTY']} params={{}} />)
    expect(screen.getByText('NIFTY')).toBeInTheDocument()
    expect(screen.getByText('BANKNIFTY')).toBeInTheDocument()
  })

  it('shows a fallback message when there are no configured parameters', () => {
    render(<StrategyRuleList instruments={[]} params={{}} />)
    expect(screen.getByText(/no configured parameters/i)).toBeInTheDocument()
  })
})
