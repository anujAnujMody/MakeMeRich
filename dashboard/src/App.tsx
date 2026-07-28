import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { Layout } from '@/components/layout'

import { DashboardHome } from '@/pages/DashboardHome'
import { ApprovePage } from '@/pages/ApprovePage'
import { StrategiesPage } from '@/pages/StrategiesPage'
import { TradesPage } from '@/pages/TradesPage'
import { PerformancePage } from '@/pages/PerformancePage'
import { JournalPage } from '@/pages/JournalPage'
import { OpsPage } from '@/pages/OpsPage'
import { LearningPage } from '@/pages/LearningPage'
import { SettingsPage } from '@/pages/SettingsPage'

const queryClient = new QueryClient()

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<ErrorBoundary><Layout /></ErrorBoundary>}>
            <Route path="/" element={<DashboardHome />} />
            <Route path="/approve" element={<ApprovePage />} />
            <Route path="/strategies" element={<StrategiesPage />} />
            <Route path="/trades" element={<TradesPage />} />
            <Route path="/performance" element={<PerformancePage />} />
            <Route path="/journal" element={<JournalPage />} />
            <Route path="/ops" element={<OpsPage />} />
            <Route path="/learn" element={<LearningPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
