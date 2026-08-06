import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import type { EngineHealthStatus } from '@/types/ops'

interface EngineHealthPanelProps {
  status: EngineHealthStatus
  onPause: () => void
  onResume: () => void
  onResetBreaker: () => void
}

const DAILY_LOSS_META: Record<EngineHealthStatus['dailyLossState'], { label: string; className: string }> = {
  normal: { label: 'Normal', className: 'text-good' },
  reduced: { label: 'Reduced sizing', className: 'text-warning' },
  halted_day: { label: 'Halted for the day', className: 'text-critical' },
}

const RESET_PHRASE = 'RESET'

export function EngineHealthPanel({ status, onPause, onResume, onResetBreaker }: EngineHealthPanelProps) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirmText, setConfirmText] = useState('')

  const dailyLoss = DAILY_LOSS_META[status.dailyLossState]

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-muted-foreground">Daily loss state</p>
          <p className={cn('text-sm font-semibold', dailyLoss.className)}>{dailyLoss.label}</p>
        </div>
        {status.runState === 'running' ? (
          <Button size="sm" variant="outline" onClick={onPause}>
            Pause
          </Button>
        ) : (
          <Button size="sm" onClick={onResume}>
            Resume
          </Button>
        )}
      </div>

      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-muted-foreground">Drawdown</p>
          <p className="font-numeric text-sm">
            {status.currentDrawdownPct.toFixed(1)}% / {status.maxDrawdownLimitPct}% limit
          </p>
        </div>
        {status.drawdownBreakerTripped && (
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-critical">Breaker tripped</span>
            <Button size="sm" variant="destructive" onClick={() => setConfirmOpen(true)}>
              Reset breaker
            </Button>
          </div>
        )}
      </div>

      <div>
        <p className="text-xs text-muted-foreground">Last successful poll</p>
        <p className="font-numeric text-sm">{status.lastSuccessfulPollSecondsAgo}s ago</p>
      </div>

      <Dialog
        open={confirmOpen}
        onOpenChange={(open) => {
          setConfirmOpen(open)
          if (!open) setConfirmText('')
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset the drawdown breaker?</DialogTitle>
            <DialogDescription>
              The breaker does not auto-reset by design — a real person has to look at the drawdown
              before trading resumes. Type {RESET_PHRASE} to confirm.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="reset-confirm" className="text-xs text-muted-foreground">
              Type {RESET_PHRASE} to confirm
            </label>
            <Input
              id="reset-confirm"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              autoComplete="off"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={confirmText !== RESET_PHRASE}
              onClick={() => {
                onResetBreaker()
                setConfirmOpen(false)
                setConfirmText('')
              }}
            >
              Confirm reset
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
