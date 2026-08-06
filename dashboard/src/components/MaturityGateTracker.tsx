import { cn } from '@/lib/utils'
import { MATURITY_STAGES } from '@/lib/maturity'
import type { MaturityGateStatus } from '@/types/learning'

export function MaturityGateTracker({ status }: { status: MaturityGateStatus }) {
  return (
    <div className="flex flex-col divide-y divide-border rounded-lg border border-border bg-card shadow-sm">
      {MATURITY_STAGES.map((stage) => {
        const isCurrent = stage.key === status.currentStage
        const { cleared, progress } = stage.gate(status)
        return (
          <div key={stage.key} className={cn('flex flex-col gap-1 px-4 py-3', isCurrent && 'bg-accent-wash')}>
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold">{stage.label}</span>
              {isCurrent && (
                <span className="rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-accent uppercase">
                  Current stage
                </span>
              )}
              {cleared && (
                <span className="text-[10px] font-semibold tracking-wide text-good uppercase">Gate cleared</span>
              )}
            </div>
            <p className="text-xs text-muted-foreground">{stage.power}</p>
            <p className="font-numeric text-xs text-muted-foreground">{progress}</p>
          </div>
        )
      })}
    </div>
  )
}
