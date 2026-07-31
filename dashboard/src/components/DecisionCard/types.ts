export type DecisionVerdict = 'traded' | 'skipped' | 'error'

/** Four states, not three. 'not-reached' (short-circuited by an earlier
 * failure) and 'unmeasurable' (reached, but the data to measure it does not
 * exist) are genuinely different, and were rendered identically before — so
 * a condition that HAD run was announced as "not reached" to anyone reading
 * the aria-label rather than the actual text. */
export type ConditionState = 'pass' | 'fail' | 'not-reached' | 'unmeasurable'

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
  /** The backend's explicit verdict. Sent so the UI never re-derives four
   * states from two booleans (or parses `actual`) — which is how "reached
   * but unmeasurable" got collapsed into "not reached". Optional so a
   * payload or fixture predating the field still renders. */
  outcome?: 'passed' | 'failed' | 'not_reached' | 'unmeasurable'
}

export function conditionState(condition: ConditionResult): ConditionState {
  switch (condition.outcome) {
    case 'passed':
      return 'pass'
    case 'failed':
      return 'fail'
    case 'not_reached':
      return 'not-reached'
    case 'unmeasurable':
      return 'unmeasurable'
    default:
      if (!condition.evaluated) return 'not-reached'
      return condition.passed ? 'pass' : 'fail'
  }
}
