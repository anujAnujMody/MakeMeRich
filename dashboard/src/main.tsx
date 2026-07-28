import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
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

// The real FastAPI engine (docs/plan) doesn't exist yet, so dev mode runs
// against the same MSW mocks the test suite uses — real interactivity, mock
// data. Remove this branch once a real backend is running behind the Vite
// proxy; this must never be true in a production build (import.meta.env.DEV
// is statically false there, so the worker code is dead-code-eliminated).
if (import.meta.env.DEV) {
  const { worker } = await import('./mocks/browser')
  // 'bypass', not 'error' — a real dev server serves plenty of non-API
  // requests (HMR, JS/CSS chunks, fonts) that were never meant to be mocked.
  // The stricter 'error' setting lives in test-setup.ts, where every request
  // genuinely should be one of our handlers.
  await worker.start({ onUnhandledRequest: 'bypass' })
}

renderApp()
