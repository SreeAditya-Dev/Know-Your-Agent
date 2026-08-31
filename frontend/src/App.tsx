import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Dashboard } from './pages/Dashboard'
import { QuarantinePage } from './pages/Quarantine'
import { MetricsPage } from './pages/Metrics'
import { DecisionPage } from './pages/Decision'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/dashboard/quarantine" element={<QuarantinePage />} />
        <Route path="/dashboard/metrics" element={<MetricsPage />} />
        <Route path="/dashboard/decisions/:id" element={<DecisionPage />} />
      </Route>
    </Routes>
  )
}
