import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrategyConfigEditor } from '@/components/StrategyConfigEditor'
import type { StrategiesFile } from '@/types'

const config: StrategiesFile = {
  check_interval_secs: 60,
  ml_threshold: 0.55,
  max_trades_per_day: 10,
  risk_per_trade_pct: 1.0,
  max_daily_loss_pct: 3.0,
  max_drawdown_pct: 15.0,
  max_concurrent_positions: 5,
  instruments: [
    { symbol: 'NIFTY', exchange: 'NSE', ticker: '^NSEI', active: true, lot_size: 65 },
    { symbol: 'SENSEX', exchange: 'BFO', ticker: 'BSE:SENSEX', active: true, lot_size: 20 },
  ],
  strategies: [
    { name: 'orbs', active: true, instruments: ['NIFTY', 'SENSEX'], params: { opening_minutes: 15 } },
  ],
}

describe('StrategyConfigEditor', () => {
  it('shows every configured instrument, including SENSEX', () => {
    render(<StrategyConfigEditor config={config} onSave={() => {}} isSaving={false} />)
    expect(screen.getByText('NIFTY')).toBeInTheDocument()
    expect(screen.getByText('SENSEX')).toBeInTheDocument()
  })

  it('lets an instrument be toggled active/inactive via an accessible switch', async () => {
    const user = userEvent.setup()
    render(<StrategyConfigEditor config={config} onSave={() => {}} isSaving={false} />)
    const switches = screen.getAllByRole('switch')
    expect(switches[0]).toHaveAttribute('aria-checked', 'true')
    await user.click(switches[0])
    expect(switches[0]).toHaveAttribute('aria-checked', 'false')
  })

  it('calls onSave with the edited draft, not the original config', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn()
    render(<StrategyConfigEditor config={config} onSave={onSave} isSaving={false} />)

    const maxTradesInput = screen.getByLabelText(/max trades per day/i)
    await user.clear(maxTradesInput)
    await user.type(maxTradesInput, '20')

    await user.click(screen.getByRole('button', { name: /^save$/i }))
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ max_trades_per_day: 20 }))
  })

  it('reset discards edits and restores the original config values', async () => {
    const user = userEvent.setup()
    render(<StrategyConfigEditor config={config} onSave={() => {}} isSaving={false} />)

    const maxTradesInput = screen.getByLabelText(/max trades per day/i) as HTMLInputElement
    await user.clear(maxTradesInput)
    await user.type(maxTradesInput, '99')
    expect(maxTradesInput.value).toBe('99')

    await user.click(screen.getByRole('button', { name: /^reset$/i }))
    expect(maxTradesInput.value).toBe('10')
  })

  it('disables save while saving is in progress', () => {
    render(<StrategyConfigEditor config={config} onSave={() => {}} isSaving={true} />)
    expect(screen.getByRole('button', { name: /saving/i })).toBeDisabled()
  })

  it('re-syncs the draft when the config prop changes underneath it (a refetch), instead of silently editing a stale snapshot', () => {
    const { rerender } = render(<StrategyConfigEditor config={config} onSave={() => {}} isSaving={false} />)

    const refetched: StrategiesFile = { ...config, max_trades_per_day: 42 }
    rerender(<StrategyConfigEditor config={refetched} onSave={() => {}} isSaving={false} />)

    expect((screen.getByLabelText(/max trades per day/i) as HTMLInputElement).value).toBe('42')
  })

  it('toggling an instrument matches on exchange + symbol, not symbol alone', async () => {
    const user = userEvent.setup()
    const twoExchangeConfig: StrategiesFile = {
      ...config,
      instruments: [
        { symbol: 'SENSEX', exchange: 'BSE', ticker: 'BSE:SENSEX', active: true, lot_size: 20 },
        { symbol: 'SENSEX', exchange: 'BFO', ticker: 'BSE:SENSEX', active: true, lot_size: 20 },
      ],
    }
    render(<StrategyConfigEditor config={twoExchangeConfig} onSave={() => {}} isSaving={false} />)

    const switches = screen.getAllByRole('switch')
    await user.click(switches[0])

    expect(switches[0]).toHaveAttribute('aria-checked', 'false')
    expect(switches[1]).toHaveAttribute('aria-checked', 'true')
  })
})
