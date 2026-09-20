import { Route, Routes } from 'react-router-dom'

import { AppLayout } from '@/layouts/AppLayout'
import { ComingSoonPage } from '@/pages/ComingSoonPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { NotFoundPage } from '@/pages/NotFoundPage'

import { NAV_ITEMS } from './navigation'

export function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<DashboardPage />} />
        {/* Modules that are not built yet render a placeholder. Each phase swaps in the real page. */}
        {NAV_ITEMS.filter((item) => item.phase !== undefined).map((item) => (
          <Route key={item.id} path={item.path} element={<ComingSoonPage item={item} />} />
        ))}
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
