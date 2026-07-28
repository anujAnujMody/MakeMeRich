import { cn } from '@/lib/utils'
import type { ShadowComparison } from '@/types/learning'

interface ShadowComparisonListProps {
  comparisons: ShadowComparison[]
}

/** What the bot actually did vs. what the shadow model would have decided —
 * the Shadow-stage answer to "is this model any good yet". */
export function ShadowComparisonList({ comparisons }: ShadowComparisonListProps) {
  if (comparisons.length === 0) {
    return <p className="text-sm text-muted-foreground">No shadow comparisons yet.</p>
  }

  return (
    <ul className="divide-y divide-border">
      {comparisons.map((c) => (
        <li key={c.id} data-agreed={c.agreed} className="flex items-center justify-between gap-3 px-1 py-2 text-sm">
          <span className="font-medium">{c.instrument}</span>
          <span className="text-xs text-muted-foreground">Actual: {c.actualVerdict}</span>
          <span className={cn('text-xs', c.agreed ? 'text-muted-foreground' : 'text-warning')}>
            Model: {c.modelVerdict}
          </span>
        </li>
      ))}
    </ul>
  )
}
