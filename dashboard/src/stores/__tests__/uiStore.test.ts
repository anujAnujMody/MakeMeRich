import { useUIStore } from '@/stores/uiStore'

describe('uiStore', () => {
  beforeEach(() => {
    document.documentElement.classList.add('dark')
    useUIStore.setState({ theme: 'dark', sidebarPinned: false, sidebarCollapsed: true })
  })

  it('setTheme updates state without touching the DOM directly — that is the sync hook\'s job', () => {
    useUIStore.getState().setTheme('light')
    expect(useUIStore.getState().theme).toBe('light')
    // The DOM class is untouched by the store itself — still 'dark' from beforeEach, even
    // though state now says 'light'. Only the sync hook (rendered separately) applies it.
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('toggleTheme flips the theme in state only', () => {
    useUIStore.getState().toggleTheme()
    expect(useUIStore.getState().theme).toBe('light')
  })

  it('setSidebarPinned updates state', () => {
    useUIStore.getState().setSidebarPinned(true)
    expect(useUIStore.getState().sidebarPinned).toBe(true)
  })
})
