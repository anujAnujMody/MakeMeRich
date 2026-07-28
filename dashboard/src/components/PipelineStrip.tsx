import type { PipelineStageInfo } from '@/types/dashboard-snapshot'

interface PipelineStripProps {
  stages: PipelineStageInfo[]
}

const STATE_CLASSNAME: Record<PipelineStageInfo['state'], string> = {
  done: 'bg-good',
  active: 'bg-accent',
  pending: 'bg-border',
}

export function PipelineStrip({ stages }: PipelineStripProps) {
  return (
    <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
      <div className="mb-2.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        Current cycle
      </div>
      <div className="flex gap-1.5">
        {stages.map((stage) => (
          <div
            key={stage.key}
            data-testid={`pipeline-stage-${stage.key}`}
            data-state={stage.state}
            className="flex flex-1 flex-col gap-1.5"
          >
            <div className="h-1 overflow-hidden rounded-full bg-secondary">
              <div
                className={`h-full rounded-full ${STATE_CLASSNAME[stage.state]}`}
                style={{ width: stage.state === 'pending' ? '0%' : stage.state === 'active' ? '60%' : '100%' }}
              />
            </div>
            <div className="flex justify-between text-[11px] text-muted-foreground">
              <span
                data-testid="pipeline-stage-label"
                className={stage.state === 'active' ? 'font-semibold text-accent' : ''}
              >
                {stage.label}
              </span>
              <span className="font-numeric">{stage.state === 'pending' ? '—' : (stage.durationLabel ?? '…')}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
