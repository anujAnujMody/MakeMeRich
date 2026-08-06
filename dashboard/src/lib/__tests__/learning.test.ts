import { computeShapData } from '@/lib/learning'
import type { TrainingResults } from '@/types'

const baseTraining = {
  accuracy: 0.7,
  walk_forward: { oos_sharpe: 1, profit_factor: 1, total_trades: 10 },
  total_samples: 100,
  win_rate_pct: 60,
}

describe('computeShapData', () => {
  it('returns an empty list when there are no training results yet', () => {
    const training: TrainingResults = { status: 'no_training_results' }
    expect(computeShapData(training)).toEqual([])
  })

  it('sorts features by importance, descending, dropping zero/negative ones', () => {
    const training: TrainingResults = {
      ...baseTraining,
      feature_importance: { vix: 0.1, adx: 0.3, pcr: 0, prev_ret_5d: -0.05 },
    }

    expect(computeShapData(training)).toEqual([
      { name: 'ADX', importance: 30 },
      { name: 'VIX', importance: 10 },
    ])
  })

  it('falls back to the raw key for an unlabeled feature', () => {
    const training: TrainingResults = { ...baseTraining, feature_importance: { some_new_feature: 0.5 } }
    expect(computeShapData(training)).toEqual([{ name: 'some_new_feature', importance: 50 }])
  })
})
