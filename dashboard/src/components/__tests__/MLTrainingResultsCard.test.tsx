import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MLTrainingResultsCard } from '@/components/MLTrainingResultsCard'
import type { TrainingResults } from '@/types'

const training: TrainingResults = {
  accuracy: 0.72,
  feature_importance: { vix: 0.25, adx: 0.18 },
  walk_forward: { oos_sharpe: 1.2, profit_factor: 1.8, total_trades: 45 },
  total_samples: 200,
  win_rate_pct: 68.5,
}

describe('MLTrainingResultsCard', () => {
  it('shows in-sample accuracy flagged not-tradeable, and OOS metrics with provenance', () => {
    render(<MLTrainingResultsCard training={training} onRetrain={() => {}} isRetraining={false} />)
    expect(screen.getByText(/in-sample accuracy/i)).toBeInTheDocument()
    expect(screen.getByText(/in-sample.*not tradeable/i)).toBeInTheDocument()
    expect(screen.getByText('1.20')).toBeInTheDocument()
  })

  it('shows the feature importance chart when there is data', () => {
    render(<MLTrainingResultsCard training={training} onRetrain={() => {}} isRetraining={false} />)
    expect(screen.getByText('Feature Importance (SHAP)')).toBeInTheDocument()
  })

  it('calls onRetrain when Retrain is clicked', async () => {
    const user = userEvent.setup()
    const onRetrain = vi.fn()
    render(<MLTrainingResultsCard training={training} onRetrain={onRetrain} isRetraining={false} />)
    await user.click(screen.getByRole('button', { name: /retrain/i }))
    expect(onRetrain).toHaveBeenCalled()
  })

  it('shows a no-results message when training has not run yet', () => {
    const noResults: TrainingResults = { status: 'no_training_results' }
    render(<MLTrainingResultsCard training={noResults} onRetrain={() => {}} isRetraining={false} />)
    expect(screen.getByText(/no training results yet/i)).toBeInTheDocument()
  })
})
