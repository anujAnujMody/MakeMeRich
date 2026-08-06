import { Button } from '@/components/ui/button'
import { useNow } from '@/hooks/useNow'
import { formatINR } from '@/lib/currency'
import { formatDuration } from '@/lib/datetime'
import { cn } from '@/lib/utils'
import type { PendingApproval } from '@/types/approval'

interface ApprovalCardProps {
  approval: PendingApproval
  onApprove: (id: string) => void
  onReject: (id: string) => void
  isDeciding?: boolean
  decideFailed?: boolean
}

const VERDICT_META: Record<PendingApproval['modelVerdict'], { label: string; className: string }> = {
  favorable: { label: 'Model: favorable', className: 'text-good' },
  neutral: { label: 'Model: neutral', className: 'text-muted-foreground' },
  caution: { label: 'Model: caution', className: 'text-warning' },
}

function formatCountdown(seconds: number): string {
  return seconds <= 0 ? 'expired' : `expires in ${formatDuration(seconds)}`
}

export function ApprovalCard({ approval, onApprove, onReject, isDeciding, decideFailed }: ApprovalCardProps) {
  const now = useNow(approval.status === 'pending' ? 1000 : false)

  const remainingSeconds = Math.max(0, Math.round((new Date(approval.expiresAt).getTime() - now) / 1000))
  const isDecidable = approval.status === 'pending' && remainingSeconds > 0
  const verdict = VERDICT_META[approval.modelVerdict]

  return (
    <div data-status={approval.status} className="rounded-lg border border-border bg-card p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span
              className={cn(
                'rounded px-1.5 py-0.5 text-[10px] font-semibold',
                approval.side === 'BUY' ? 'bg-gain/10 text-gain' : 'bg-loss/10 text-loss',
              )}
            >
              {approval.side}
            </span>
            <span className="font-medium">{approval.instrument}</span>
            <span className="text-xs text-muted-foreground">{approval.lots} lot{approval.lots === 1 ? '' : 's'}</span>
          </div>
          <p className={cn('mt-1 text-xs font-medium', verdict.className)}>{verdict.label}</p>
        </div>
        <span className="font-numeric text-xs text-muted-foreground">
          {approval.status === 'pending' ? formatCountdown(remainingSeconds) : approval.status}
        </span>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-muted-foreground">Premium</dt>
          <dd className="font-numeric">{formatINR(approval.premium)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Stop</dt>
          <dd className="font-numeric text-loss">{formatINR(approval.stopLoss)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Target</dt>
          <dd className="font-numeric text-gain">{formatINR(approval.target)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Est. cost</dt>
          <dd className="font-numeric">{formatINR(approval.estimatedCost)}</dd>
        </div>
      </dl>

      {isDecidable && (
        <div className="mt-3 flex flex-col gap-2">
          {decideFailed && (
            <p className="text-xs text-critical">That decision didn't go through — try again.</p>
          )}
          <div className="flex gap-2">
            <Button size="sm" className="flex-1" disabled={isDeciding} onClick={() => onApprove(approval.id)}>
              Approve
            </Button>
            <Button size="sm" variant="outline" className="flex-1" disabled={isDeciding} onClick={() => onReject(approval.id)}>
              Reject
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
