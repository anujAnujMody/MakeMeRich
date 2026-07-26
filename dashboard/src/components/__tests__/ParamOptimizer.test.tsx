import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/mocks/test-utils'
import { ParamOptimizer } from '@/components/ParamOptimizer'
import { useLearningStore } from '@/stores/learningStore'

describe('ParamOptimizer', () => {
  it('shows select-strategy prompt when none selected', () => {
    renderWithProviders(<ParamOptimizer />)
    expect(screen.getByText(/select a strategy/i)).toBeInTheDocument()
  })

  it('shows param grid editor when strategy selected', () => {
    useLearningStore.setState({ selectedStrategy: 'orbs' })
    renderWithProviders(<ParamOptimizer />)
    expect(screen.getByLabelText(/parameter grid/i)).toBeInTheDocument()
    expect(screen.getByText(/target_rr/i)).toBeInTheDocument()
  })

  it('shows run optimization button', () => {
    useLearningStore.setState({ selectedStrategy: 'orbs' })
    renderWithProviders(<ParamOptimizer />)
    expect(screen.getByRole('button', { name: /run optimization/i })).toBeInTheDocument()
  })

  it('shows results after optimization', async () => {
    useLearningStore.setState({ selectedStrategy: 'orbs' })
    const user = userEvent.setup()
    renderWithProviders(<ParamOptimizer />)
    const btn = screen.getByRole('button', { name: /run optimization/i })
    await user.click(btn)
    expect(await screen.findByText(/results/i)).toBeInTheDocument()
    expect(await screen.findByText(/target_rr=1.5/i)).toBeInTheDocument()
  })

  it('shows parameter impact section after results', async () => {
    useLearningStore.setState({ selectedStrategy: 'orbs' })
    const user = userEvent.setup()
    renderWithProviders(<ParamOptimizer />)
    const btn = screen.getByRole('button', { name: /run optimization/i })
    await user.click(btn)
    expect(await screen.findByText(/parameter impact/i)).toBeInTheDocument()
  })
})
