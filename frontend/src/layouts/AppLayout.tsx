import { useCallback, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'

import { AccountBanner } from '@/components/AccountBanner'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { OfflineBanner } from '@/components/OfflineBanner'
import { SessionBanner } from '@/features/auth/SessionBanner'
import { useAutoSync, useScope } from '@/offline/useOffline'
import { UpdateBanner } from '@/pwa/PwaBanners'

import { Header } from './Header'
import { Sidebar } from './Sidebar'

/** Shell shared by every page: sidebar (drawer on mobile), header, and the page content. */
export function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()
  const closeSidebar = useCallback(() => setSidebarOpen(false), [])
  useAutoSync(useScope()) // sends anything queued offline as soon as there is a connection

  return (
    <div className="min-h-dvh bg-slate-50 text-slate-900">
      <Sidebar open={sidebarOpen} onClose={closeSidebar} />
      <div className="lg:pl-64">
        <Header onMenuClick={() => setSidebarOpen(true)} />
        <UpdateBanner />
        <OfflineBanner />
        <AccountBanner />
        <SessionBanner />
        <main className="mx-auto w-full max-w-7xl p-4 sm:p-6">
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
