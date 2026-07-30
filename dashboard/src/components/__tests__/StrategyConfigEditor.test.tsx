import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrategyConfigEditor } from '@/components/StrategyConfigEditor'
import type { InstrumentSelections } from '@/types/ops'

const config: InstrumentSelections = {
  instruments: [
    { symbol: 'NIFTY', exchange: 'NFO', lotSize: 65, active: true },
    { symbol: 'SENSEX', exchange: 'BFO', lotSize: 20, active: true },
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

    const lotSizeInput = screen.getByLabelText(/lot size/i, { selector: '#lot-NFO-NIFTY' })
    await user.clear(lotSizeInput)
    await user.type(lotSizeInput, '75')

    await user.click(screen.getByRole('button', { name: /^save$/i }))
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        instruments: expect.arrayContaining([expect.objectContaining({ symbol: 'NIFTY', lotSize: 75 })]),
      }),
    )
  })

  it('reset discards edits and restores the original config values', async () => {
    const user = userEvent.setup()
    render(<StrategyConfigEditor config={config} onSave={() => {}} isSaving={false} />)

    const lotSizeInput = screen.getByLabelText(/lot size/i, { selector: '#lot-NFO-NIFTY' }) as HTMLInputElement
    await user.clear(lotSizeInput)
    await user.type(lotSizeInput, '99')
    expect(lotSizeInput.value).toBe('99')

    await user.click(screen.getByRole('button', { name: /^reset$/i }))
    expect(lotSizeInput.value).toBe('65')
  })

  it('disables save while saving is in progress', () => {
    render(<StrategyConfigEditor config={config} onSave={() => {}} isSaving={true} />)
    expect(screen.getByRole('button', { name: /saving/i })).toBeDisabled()
  })

  it('re-syncs the draft when the config prop changes underneath it (a refetch), instead of silently editing a stale snapshot', () => {
    const { rerender } = render(<StrategyConfigEditor config={config} onSave={() => {}} isSaving={false} />)

    const refetched: InstrumentSelections = {
      instruments: config.instruments.map((i) => (i.symbol === 'NIFTY' ? { ...i, lotSize: 42 } : i)),
    }
    rerender(<StrategyConfigEditor config={refetched} onSave={() => {}} isSaving={false} />)

    expect((screen.getByLabelText(/lot size/i, { selector: '#lot-NFO-NIFTY' }) as HTMLInputElement).value).toBe('42')
  })

  it('toggling an instrument matches on exchange + symbol, not symbol alone', async () => {
    const user = userEvent.setup()
    const twoExchangeConfig: InstrumentSelections = {
      instruments: [
        { symbol: 'SENSEX', exchange: 'BSE', lotSize: 20, active: true },
        { symbol: 'SENSEX', exchange: 'BFO', lotSize: 20, active: true },
      ],
    }
    render(<StrategyConfigEditor config={twoExchangeConfig} onSave={() => {}} isSaving={false} />)

    const switches = screen.getAllByRole('switch')
    await user.click(switches[0])

    expect(switches[0]).toHaveAttribute('aria-checked', 'false')
    expect(switches[1]).toHaveAttribute('aria-checked', 'true')
  })

  it('shows a message when no instruments are configured', () => {
    render(<StrategyConfigEditor config={{ instruments: [] }} onSave={() => {}} isSaving={false} />)
    expect(screen.getByText(/no instruments configured/i)).toBeInTheDocument()
  })
})
