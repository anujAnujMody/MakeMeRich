import { PageHeader } from '@/components/PageHeader'
import { ApprovalCard } from '@/components/ApprovalCard'
import { useApprovals, useDecideApproval } from '@/hooks/useApprovals'
import { useTradingMode } from '@/hooks/useTradingMode'

export function ApprovePage() {
  const { data: approvals, isLoading } = useApprovals()
  const decide = useDecideApproval()
  const mode = useTradingMode().data?.mode

  return (
    <div>
      <PageHeader
        title="Approve"
        subtitle="Trades the bot wants to place — each one waits for your decision before it fires."
      />

      <div className="flex flex-col gap-4 p-5">
        {mode === 'dry-run' && (
          <div className="rounded-lg border border-border bg-accent-wash px-4 py-2.5 text-xs text-accent">
            You're in DRY RUN mode — approvals aren't required, trades fire automatically on paper.
            This queue previews what the live approval flow will look like.
          </div>
        )}

        <div className="rounded-lg border border-warning/40 bg-warning/10 px-4 py-2.5 text-xs text-warning">
          Approve/Reject only records your decision right now — placing or cancelling a real order on
          approval isn't built yet. Nothing here executes against a broker.
        </div>

        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading approvals…</p>
        ) : !approvals || approvals.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border bg-card/50 p-8 text-center text-sm text-muted-foreground">
            No pending approvals right now.
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            {approvals.map((approval) => (
              <ApprovalCard
                key={approval.id}
                approval={approval}
                onApprove={(id) => decide.mutate({ id, decision: 'approve' })}
                onReject={(id) => decide.mutate({ id, decision: 'reject' })}
                isDeciding={decide.isPending && decide.variables?.id === approval.id}
                decideFailed={decide.isError && decide.variables?.id === approval.id}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
