import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Layout } from '@/components/layout'

const queryClient = new QueryClient()

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<div className="p-6"><h1 className="text-2xl font-bold">Dashboard</h1></div>} />
            <Route path="/strategies" element={<div className="p-6"><h1 className="text-2xl font-bold">Strategies</h1></div>} />
            <Route path="/orders" element={<div className="p-6"><h1 className="text-2xl font-bold">Orders</h1></div>} />
            <Route path="/positions" element={<div className="p-6"><h1 className="text-2xl font-bold">Positions</h1></div>} />
            <Route path="/insights" element={<div className="p-6"><h1 className="text-2xl font-bold">Insights</h1></div>} />
            <Route path="/learn" element={<div className="p-6"><h1 className="text-2xl font-bold">Learn</h1></div>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
