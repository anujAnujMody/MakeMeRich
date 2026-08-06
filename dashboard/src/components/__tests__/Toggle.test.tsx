import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Toggle } from '@/components/Toggle'

describe('Toggle', () => {
  it('exposes an accessible switch role with the current checked state', () => {
    render(<Toggle checked={false} onCheckedChange={() => {}} label="Research agent" />)
    const el = screen.getByRole('switch', { name: 'Research agent' })
    expect(el).toHaveAttribute('aria-checked', 'false')
  })

  it('reflects checked=true', () => {
    render(<Toggle checked={true} onCheckedChange={() => {}} label="Research agent" />)
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true')
  })

  it('calls onCheckedChange with the flipped value on click', async () => {
    const user = userEvent.setup()
    const onCheckedChange = vi.fn()
    render(<Toggle checked={false} onCheckedChange={onCheckedChange} label="Research agent" />)
    await user.click(screen.getByRole('switch'))
    expect(onCheckedChange).toHaveBeenCalledWith(true)
  })

  it('can be disabled', () => {
    render(<Toggle checked={false} onCheckedChange={() => {}} label="Research agent" disabled />)
    expect(screen.getByRole('switch')).toBeDisabled()
  })
})
