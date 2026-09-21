import { useCallback, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'

import { ErrorBoundary } from '@/components/ErrorBoundary'

import { Header } from './Header'
import { Sidebar } from './Sidebar'

/** Shell shared by every page: sidebar (drawer on mobile), header, and the page content. */
export function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()
  const closeSidebar = useCallback(() => setSidebarOpen(false), [])

  return (
    <div className="min-h-dvh bg-slate-50 text-slate-900">
      <Sidebar open={sidebarOpen} onClose={closeSidebar} />
      <div className="lg:pl-64">
        <Header onMenuClick={() => setSidebarOpen(true)} />
        <main className="mx-auto w-full max-w-7xl p-4 sm:p-6">
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
