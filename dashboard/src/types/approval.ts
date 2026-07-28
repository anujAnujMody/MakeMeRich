export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'expired'
export type ApprovalVerdict = 'favorable' | 'neutral' | 'caution'

export interface PendingApproval {
  id: string
  createdAt: string
  /** ISO timestamp — the approval auto-expires if not decided by then. */
  expiresAt: string
  instrument: string
  side: 'BUY' | 'SELL'
  lots: number
  premium: number
  stopLoss: number
  target: number
  /** Estimated round-trip cost (brokerage + STT + exchange + GST etc). */
  estimatedCost: number
  /** The model's advisory read — shown, never gating (see maturity gates). */
  modelVerdict: ApprovalVerdict
  status: ApprovalStatus
}
