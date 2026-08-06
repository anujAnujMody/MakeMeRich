import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { EngineHealthPanel } from '@/components/EngineHealthPanel'
import type { EngineHealthStatus } from '@/types/ops'

const status: EngineHealthStatus = {
  runState: 'running',
  dailyLossState: 'normal',
  drawdownBreakerTripped: false,
  currentDrawdownPct: 3.2,
  maxDrawdownLimitPct: 15,
  lastSuccessfulPollSecondsAgo: 12,
}

describe('EngineHealthPanel', () => {
  it('shows the daily-loss state and drawdown', () => {
    render(<EngineHealthPanel status={status} onPause={() => {}} onResume={() => {}} onResetBreaker={() => {}} />)
    expect(screen.getByText(/normal/i)).toBeInTheDocument()
    expect(screen.getByText(/3\.2/)).toBeInTheDocument()
  })

  it('shows the last successful poll — the alert that matters more than any P&L alert', () => {
    render(<EngineHealthPanel status={status} onPause={() => {}} onResume={() => {}} onResetBreaker={() => {}} />)
    expect(screen.getByText(/12s ago/)).toBeInTheDocument()
  })

  it('pauses without asking for confirmation', async () => {
    const user = userEvent.setup()
    const onPause = vi.fn()
    render(<EngineHealthPanel status={status} onPause={onPause} onResume={() => {}} onResetBreaker={() => {}} />)
    await user.click(screen.getByRole('button', { name: /pause/i }))
    expect(onPause).toHaveBeenCalled()
    expect(screen.queryByText(/are you sure/i)).not.toBeInTheDocument()
  })

  it('never auto-resets a tripped drawdown breaker — requires typing to confirm', async () => {
    const user = userEvent.setup()
    const onResetBreaker = vi.fn()
    render(
      <EngineHealthPanel
        status={{ ...status, drawdownBreakerTripped: true }}
        onPause={() => {}}
        onResume={() => {}}
        onResetBreaker={onResetBreaker}
      />,
    )
    expect(screen.getByText(/breaker tripped/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /reset breaker/i }))
    const confirmInput = await screen.findByLabelText(/type reset to confirm/i)
    const confirmButton = screen.getByRole('button', { name: /^confirm reset$/i })

    expect(confirmButton).toBeDisabled()
    await user.type(confirmInput, 'RESET')
    expect(confirmButton).toBeEnabled()

    await user.click(confirmButton)
    expect(onResetBreaker).toHaveBeenCalled()
  })
})
