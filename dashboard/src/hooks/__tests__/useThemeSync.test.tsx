import { renderHook, act } from '@testing-library/react'
import { useThemeSync } from '@/hooks/useThemeSync'
import { useUIStore } from '@/stores/uiStore'

describe('useThemeSync', () => {
  beforeEach(() => {
    document.documentElement.classList.remove('dark')
    useUIStore.setState({ theme: 'dark' })
  })

  it('applies the dark class on mount when theme is dark', () => {
    renderHook(() => useThemeSync())
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('removes the dark class when theme is light', () => {
    useUIStore.setState({ theme: 'light' })
    renderHook(() => useThemeSync())
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('re-syncs the DOM when the theme changes after mount', () => {
    renderHook(() => useThemeSync())
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    act(() => useUIStore.getState().setTheme('light'))
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })
})
