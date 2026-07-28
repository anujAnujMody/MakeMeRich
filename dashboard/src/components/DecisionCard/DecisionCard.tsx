/* oxlint-disable react/only-export-components -- deliberate compound component:
   Root/Summary/Conditions are internal parts of DecisionCard, not meant to be
   imported standalone. See vercel-composition-patterns skill. */
import { createContext, use, useState, type ReactNode } from 'react'
import { conditionState, type ConditionResult, type DecisionVerdict } from './types'

interface DecisionCardContextValue {
  verdict: DecisionVerdict
  expanded: boolean
  toggle: () => void
}

const DecisionCardContext = createContext<DecisionCardContextValue | null>(null)

function useDecisionCardContext(component: string): DecisionCardContextValue {
  const ctx = use(DecisionCardContext)
  if (!ctx) {
    throw new Error(`DecisionCard.${component} must be rendered inside DecisionCard.Root`)
  }
  return ctx
}

const VERDICT_META: Record<DecisionVerdict, { label: string; icon: string; className: string }> = {
  traded: { label: 'Traded', icon: '✓', className: 'text-good bg-good/10' },
  skipped: { label: 'Skipped', icon: '✕', className: 'text-muted-foreground bg-secondary' },
  error: { label: 'Error', icon: '!', className: 'text-critical bg-critical/10' },
}

function Root({ verdict, children }: { verdict: DecisionVerdict; children: ReactNode }) {
  const [expanded, setExpanded] = useState(false)
  const toggle = () => setExpanded((prev) => !prev)

  return (
    <DecisionCardContext value={{ verdict, expanded, toggle }}>
      <div data-verdict={verdict} data-expanded={expanded} className="border-b border-border last:border-b-0">
        {children}
      </div>
    </DecisionCardContext>
  )
}

function Summary({ time, instrument, text }: { time: string; instrument: string; text: string }) {
  const { verdict, expanded, toggle } = useDecisionCardContext('Summary')
  const meta = VERDICT_META[verdict]

  return (
    <button
      type="button"
      onClick={toggle}
      aria-expanded={expanded}
      className="flex w-full items-center gap-2.5 px-5 py-3 text-left hover:bg-secondary/50"
    >
      <span className="font-numeric w-11 shrink-0 text-xs text-muted-foreground sm:w-[58px]">{time}</span>
      <span
        className={`inline-flex shrink-0 items-center gap-1 rounded px-2 py-0.5 text-[11px] font-bold ${meta.className}`}
      >
        <span aria-hidden="true">{meta.icon}</span>
        {meta.label}
      </span>
      <span className="min-w-0 flex-1 truncate text-sm">
        <span className="font-semibold">{instrument}</span> · {text}
      </span>
      <span aria-hidden="true" className={`shrink-0 text-muted-foreground transition-transform ${expanded ? 'rotate-90' : ''}`}>
        ›
      </span>
    </button>
  )
}

function Conditions({ items }: { items: ConditionResult[] }) {
  const { expanded } = useDecisionCardContext('Conditions')
  if (!expanded) return null

  return (
    <div className="overflow-x-auto px-5 pb-4 pl-4 sm:pl-[88px]">
      <table className="w-full text-xs">
        <thead>
          <tr>
            <th className="pb-1.5 text-left font-semibold text-muted-foreground">Condition</th>
            <th className="pb-1.5 text-right font-semibold text-muted-foreground">Required</th>
            <th className="pb-1.5 text-right font-semibold text-muted-foreground">Actual</th>
            <th className="w-6 pb-1.5" />
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const state = conditionState(item)
            return (
              <tr
                key={item.label}
                data-state={state}
                className={`border-t border-border ${state === 'not-reached' ? 'opacity-60' : ''}`}
              >
                <td className="py-1.5">{item.label}</td>
                <td className="font-numeric py-1.5 text-right">{item.required}</td>
                <td className="font-numeric py-1.5 text-right">{item.actual}</td>
                <td className="py-1.5 text-center">
                  {state === 'pass' && (
                    <span className="font-bold text-good" aria-label="passed">
                      ✓
                    </span>
                  )}
                  {state === 'fail' && (
                    <span className="font-bold text-critical" aria-label="failed">
                      ✕
                    </span>
                  )}
                  {state === 'not-reached' && (
                    <span className="text-muted-foreground" aria-label="not reached">
                      ⋯
                    </span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export const DecisionCard = { Root, Summary, Conditions }
