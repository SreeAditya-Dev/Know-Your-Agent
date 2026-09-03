import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Dashboard } from './pages/Dashboard'
import { QuarantinePage } from './pages/Quarantine'
import { MetricsPage } from './pages/Metrics'
import { DecisionPage } from './pages/Decision'
import { SimulationPage } from './pages/Simulation'
import { DisputesPage } from './pages/Disputes'
import { StorePage } from './pages/Store'
import { LandingPage } from './pages/LandingPage'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route element={<Layout />}>
        <Route path="/store" element={<StorePage />} />
        <Route path="/dashboard/store" element={<StorePage />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/dashboard/simulation" element={<SimulationPage />} />
        <Route path="/dashboard/disputes" element={<DisputesPage />} />
        <Route path="/dashboard/quarantine" element={<QuarantinePage />} />
        <Route path="/dashboard/metrics" element={<MetricsPage />} />
        <Route path="/dashboard/decisions/:id" element={<DecisionPage />} />
      </Route>
    </Routes>
  )
}
