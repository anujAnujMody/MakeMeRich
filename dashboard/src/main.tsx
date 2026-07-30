import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { isUsingMockData } from '@/lib/mockStatus'
import './index.css'

// Pre-paint theme application, before React mounts and useThemeSync's effect
// runs — avoids a flash of the wrong theme. Reads the same persisted shape
// zustand's `persist` middleware writes for uiStore (name: 'algo-ui').
function readPersistedTheme(): 'dark' | 'light' {
  try {
    const raw = localStorage.getItem('algo-ui')
    const theme = raw ? JSON.parse(raw)?.state?.theme : null
    return theme === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}
document.documentElement.classList.toggle('dark', readPersistedTheme() === 'dark')

function renderApp() {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

// Dev mode runs against MSW mocks by default (real interactivity, mock
// data) — but `yarn dev:real` sets VITE_USE_MOCKS=false so requests fall
// through to vite.config.ts's `/api/`/`/ws/` proxy instead, hitting the real
// engine with Vite's HMR still attached. `isUsingMockData()` is the same
// check `MockDataBanner` renders on, so the two can never disagree; dead-code
// eliminated in a production build since `import.meta.env.DEV` is static.
if (isUsingMockData()) {
  const { worker } = await import('./mocks/browser')
  // 'bypass', not 'error' — a real dev server serves plenty of non-API
  // requests (HMR, JS/CSS chunks, fonts) that were never meant to be mocked.
  // The stricter 'error' setting lives in test-setup.ts, where every request
  // genuinely should be one of our handlers.
  await worker.start({ onUnhandledRequest: 'bypass' })
}

renderApp()
