import { useEffect } from 'react'
import { useUIStore } from '@/stores/uiStore'

/** Syncs the `dark` DOM class to the store's theme. Kept out of the store's
 * setters (which must have no side effects) and out of any page/component
 * body — call once, near the app root (layout.tsx). */
export function useThemeSync() {
  const theme = useUIStore((s) => s.theme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])
}
