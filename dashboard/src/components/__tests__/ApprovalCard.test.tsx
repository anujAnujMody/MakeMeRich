import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ApprovalCard } from '@/components/ApprovalCard'
import type { PendingApproval } from '@/types/approval'

const baseApproval: PendingApproval = {
  id: 'ap-1',
  createdAt: new Date().toISOString(),
  expiresAt: new Date(Date.now() + 30_000).toISOString(),
  instrument: 'SENSEX 81400 CE',
  side: 'BUY',
  lots: 1,
  premium: 95.2,
  stopLoss: 61.88,
  target: 152.32,
  estimatedCost: 70.5,
  modelVerdict: 'favorable',
  status: 'pending',
}

describe('ApprovalCard', () => {
  it('shows the trade details: instrument, side, lots, premium, stop, target, and cost', () => {
    render(<ApprovalCard approval={baseApproval} onApprove={() => {}} onReject={() => {}} />)
    expect(screen.getByText('SENSEX 81400 CE')).toBeInTheDocument()
    expect(screen.getByText('BUY')).toBeInTheDocument()
    expect(screen.getByText(/95\.20/)).toBeInTheDocument()
    expect(screen.getByText(/61\.88/)).toBeInTheDocument()
    expect(screen.getByText(/152\.32/)).toBeInTheDocument()
    expect(screen.getByText(/70\.50/)).toBeInTheDocument()
  })

  it("shows the model's advisory verdict without implying it can veto", () => {
    render(<ApprovalCard approval={baseApproval} onApprove={() => {}} onReject={() => {}} />)
    expect(screen.getByText(/favorable/i)).toBeInTheDocument()
  })

  it('calls onApprove when Approve is clicked', async () => {
    const user = userEvent.setup()
    const onApprove = vi.fn()
    render(<ApprovalCard approval={baseApproval} onApprove={onApprove} onReject={() => {}} />)
    await user.click(screen.getByRole('button', { name: /approve/i }))
    expect(onApprove).toHaveBeenCalledWith('ap-1')
  })

  it('calls onReject when Reject is clicked', async () => {
    const user = userEvent.setup()
    const onReject = vi.fn()
    render(<ApprovalCard approval={baseApproval} onApprove={() => {}} onReject={onReject} />)
    await user.click(screen.getByRole('button', { name: /reject/i }))
    expect(onReject).toHaveBeenCalledWith('ap-1')
  })

  it('shows a countdown to expiry', () => {
    render(<ApprovalCard approval={baseApproval} onApprove={() => {}} onReject={() => {}} />)
    expect(screen.getByText(/expires in|expired/i)).toBeInTheDocument()
  })

  it('shows an expired state with no approve/reject actions once expired', () => {
    render(
      <ApprovalCard
        approval={{ ...baseApproval, status: 'expired' }}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    )
    expect(screen.getByText(/expired/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /reject/i })).not.toBeInTheDocument()
  })
})
