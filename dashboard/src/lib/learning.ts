import type { TrainingResults } from '@/types'

export const FEATURE_LABELS: Record<string, string> = {
  vix: 'VIX',
  adx: 'ADX',
  prev_ret_5d: 'Prev Return (5d)',
  prev_ret_1d: 'Prev Return (1d)',
  pcr: 'Put-Call Ratio',
  iv_rank: 'IV Rank',
  is_high_vol: 'High Volatility',
  is_trending: 'Trending',
}

export interface ShapDatum {
  name: string
  importance: number
}

/** Fraction (0-1) as a 1-decimal-place percent string, e.g. 0.723 -> "72.3%". */
export function formatPercent1(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

/** Feature-importance bars for the SHAP chart — sorted descending, zero/
 * negative contributions dropped. */
export function computeShapData(training: TrainingResults | undefined): ShapDatum[] {
  if (!training || training.status === 'no_training_results') return []

  return Object.entries(training.feature_importance)
    .filter(([, v]) => v > 0)
    .sort(([, a], [, b]) => b - a)
    .map(([k, v]) => ({ name: FEATURE_LABELS[k] || k, importance: +(v * 100).toFixed(1) }))
}
