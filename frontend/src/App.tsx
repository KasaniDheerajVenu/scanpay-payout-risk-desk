import { Navigate, Outlet, Route, Routes, useLocation } from "react-router"
import { LoadingPanel, RetryNotice } from "@/components/ui/workspace"
import { SessionProvider, useAuth } from "@/features/auth/session"
import { AppLayout } from "@/layouts/app-layout"
import { DatasetsPage } from "@/pages/datasets-page"
import { EvaluationsPage } from "@/pages/evaluations-page"
import { HealthPage } from "@/pages/health-page"
import { LoginPage } from "@/pages/login-page"
import { NotFoundPage } from "@/pages/not-found-page"
import { RunWorkspacePage } from "@/pages/run-workspace-page"
import { errorMessage } from "@/services/api"

function SessionGate({ requireUser = false }: { requireUser?: boolean }) {
  const auth = useAuth()
  const location = useLocation()
  if (auth.session === undefined) {
    return <main className="public-area">{auth.error
      ? <RetryNotice message={errorMessage(auth.error)} onRetry={auth.reload} />
      : <LoadingPanel label="Checking session" />}</main>
  }
  if (requireUser && !auth.session) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  return <Outlet />
}

export function App() {
  return (
    <Routes>
      <Route path="/status" element={<main className="public-area"><HealthPage /></main>} />
      <Route element={<SessionProvider><Outlet /></SessionProvider>}>
        <Route element={<SessionGate />}>
          <Route path="/login" element={<LoginPage />} />
        </Route>
        <Route element={<SessionGate requireUser />}>
          <Route element={<AppLayout />}>
            <Route index element={<Navigate to="/datasets" replace />} />
            <Route path="/datasets" element={<DatasetsPage />} />
            <Route path="/evaluations" element={<EvaluationsPage />} />
            <Route path="/dashboard" element={<RunWorkspacePage title="Overview" />} />
            <Route path="/payouts" element={<RunWorkspacePage title="Payout queue" />} />
            <Route path="/fraud" element={<RunWorkspacePage title="Fraud insights" />} />
            <Route path="/models" element={<RunWorkspacePage title="Model insights" />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<main className="public-area"><NotFoundPage /></main>} />
    </Routes>
  )
}
