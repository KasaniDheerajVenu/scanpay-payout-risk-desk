import { Route, Routes } from "react-router"
import { AppLayout } from "@/layouts/app-layout"
import { HealthPage } from "@/pages/health-page"
import { NotFoundPage } from "@/pages/not-found-page"

export function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<HealthPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
