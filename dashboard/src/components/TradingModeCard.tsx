import { useState } from 'react'
import { useTradingMode, useSetTradingMode } from '@/hooks/useTradingMode'
import { useEngineHealth, usePauseEngine, useResumeEngine } from '@/hooks/useEngineHealth'
import { Button } from '@/components/ui/button'
import { GoLiveConfirmDialog } from '@/components/GoLiveConfirmDialog'
import { cn } from '@/lib/utils'

export function TradingModeCard() {
  const { data: engineMode } = useTradingMode()
  const setTradingMode = useSetTradingMode()
  const { data: health } = useEngineHealth()
  const pause = usePauseEngine()
  const resume = useResumeEngine()

  const [confirmOpen, setConfirmOpen] = useState(false)

  const mode = engineMode?.mode ?? 'dry-run'
  const isLive = mode === 'live'

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-muted-foreground">Trading mode</p>
          <p className={cn('text-sm font-semibold', isLive ? 'text-critical' : 'text-accent')}>
            {isLive ? 'LIVE — real orders, real money' : 'DRY RUN — paper trading'}
          </p>
          {setTradingMode.isError && (
            <p className="mt-1 text-xs text-critical">Could not switch mode — try again.</p>
          )}
        </div>
        {isLive ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setTradingMode.mutate('dry-run')}
            disabled={setTradingMode.isPending}
          >
            Switch to dry run
          </Button>
        ) : (
          <Button size="sm" variant="destructive" onClick={() => setConfirmOpen(true)}>
            Switch to live
          </Button>
        )}
      </div>

      {health && (
        <div className="flex items-center justify-between border-t border-border pt-3">
          <div>
            <p className="text-xs text-muted-foreground">Engine</p>
            <p className="text-sm font-medium">{health.runState === 'running' ? 'Running' : 'Paused'}</p>
          </div>
          {health.runState === 'running' ? (
            <Button size="sm" variant="outline" onClick={() => pause.mutate()}>
              Pause engine
            </Button>
          ) : (
            <Button size="sm" onClick={() => resume.mutate()}>
              Resume engine
            </Button>
          )}
        </div>
      )}

      <GoLiveConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        onConfirm={() => setTradingMode.mutate('live')}
      />
    </div>
  )
}
