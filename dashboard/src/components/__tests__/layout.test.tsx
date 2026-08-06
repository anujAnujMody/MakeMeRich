import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/mocks/test-utils'
import { Layout } from '@/components/layout'
import { Routes, Route } from 'react-router-dom'

function renderLayout() {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<div>Home content</div>} />
      </Route>
    </Routes>,
  )
}

describe('Layout', () => {
  it('switching to live requires the same typed-phrase confirmation as Settings, not a plain click', async () => {
    const user = userEvent.setup()
    renderLayout()

    await user.click(await screen.findByRole('button', { name: /paper/i }))

    const confirmButton = await screen.findByRole('button', { name: /^confirm switch to live$/i })
    expect(confirmButton).toBeDisabled()

    await user.type(await screen.findByLabelText(/type go live to confirm/i), 'GO LIVE')
    expect(confirmButton).toBeEnabled()
  })

  it('switching back to dry run needs no confirmation dialog', async () => {
    const user = userEvent.setup()
    renderLayout()

    await user.click(await screen.findByRole('button', { name: /paper/i }))
    await user.type(await screen.findByLabelText(/type go live to confirm/i), 'GO LIVE')
    await user.click(screen.getByRole('button', { name: /^confirm switch to live$/i }))

    await user.click(await screen.findByRole('button', { name: /^live$/i }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
