export type DecisionVerdict = 'traded' | 'skipped' | 'error'

export type ConditionState = 'pass' | 'fail' | 'not-reached'

export interface ConditionResult {
  label: string
  /** Human-readable requirement, e.g. "≥ 1.5x avg" — not a raw operator/value pair. */
  required: string
  /** The REAL value at evaluation time. If the backend can't produce this, the
   * condition must not be shown at all — see the decision-explainability skill. */
  actual: string
  passed: boolean
  /** false when short-circuited by an earlier failing condition. */
  evaluated: boolean
}

export function conditionState(condition: ConditionResult): ConditionState {
  if (!condition.evaluated) return 'not-reached'
  return condition.passed ? 'pass' : 'fail'
}
