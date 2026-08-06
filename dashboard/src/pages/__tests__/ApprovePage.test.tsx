import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '@/mocks/test-utils'
import { server } from '@/mocks/server'
import { ApprovePage } from '@/pages/ApprovePage'

describe('ApprovePage', () => {
  it('shows pending approvals', async () => {
    renderWithProviders(<ApprovePage />)
    expect(await screen.findByText(/SENSEX/)).toBeInTheDocument()
  })

  it('approving a trade removes it from the pending queue', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ApprovePage />)
    await screen.findByText(/SENSEX/)
    await user.click(screen.getAllByRole('button', { name: /^approve$/i })[0])
    expect(await screen.findByText(/no pending approvals/i)).toBeInTheDocument()
  })

  it('shows an empty-state message when there is nothing to approve', async () => {
    server.use(http.get('*/api/approvals', () => HttpResponse.json([])))
    renderWithProviders(<ApprovePage />)
    expect(await screen.findByText(/no pending approvals/i)).toBeInTheDocument()
  })

  it('explains that approvals are not required while in dry-run mode', async () => {
    renderWithProviders(<ApprovePage />)
    expect(await screen.findByText(/dry run|not required/i)).toBeInTheDocument()
  })

  it('shows an error on the card and keeps it in the queue when a decision fails', async () => {
    server.use(http.post('*/api/approvals/decide', () => HttpResponse.error()))
    const user = userEvent.setup()
    renderWithProviders(<ApprovePage />)

    await screen.findByText(/SENSEX/)
    await user.click(screen.getAllByRole('button', { name: /^approve$/i })[0])

    expect(await screen.findByText(/didn't go through/i)).toBeInTheDocument()
    expect(screen.getByText(/SENSEX/)).toBeInTheDocument()
  })
})
