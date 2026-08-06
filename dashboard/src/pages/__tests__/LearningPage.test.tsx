import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/mocks/test-utils'
import { LearningPage } from '@/pages/LearningPage'

describe('LearningPage', () => {
  it('renders page title', () => {
    renderWithProviders(<LearningPage />)
    expect(screen.getByText('Learning')).toBeInTheDocument()
  })

  it('shows the maturity gate tracker with the current stage', async () => {
    renderWithProviders(<LearningPage />)
    expect(await screen.findByText('Current stage')).toBeInTheDocument()
    expect(await screen.findByText(/42 \/ 100 closed trades/)).toBeInTheDocument()
  })

  it('shows the shadow comparison history', async () => {
    renderWithProviders(<LearningPage />)
    expect(await screen.findByText('SENSEX 81400 CE')).toBeInTheDocument()
  })

  it('marks in-sample training accuracy as not tradeable, never as a plain result', async () => {
    renderWithProviders(<LearningPage />)
    await screen.findByText(/in-sample accuracy/i)
    expect(await screen.findByText(/in-sample.*not tradeable/i)).toBeInTheDocument()
  })

  it('shows the pattern library', async () => {
    renderWithProviders(<LearningPage />)
    expect(await screen.findByText('Pattern Library (What Works)')).toBeInTheDocument()
  })
})
