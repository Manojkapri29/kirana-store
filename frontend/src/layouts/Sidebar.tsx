import { X, Store } from 'lucide-react'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink } from 'react-router-dom'

import { NAV_ITEMS } from '@/app/navigation'

interface SidebarProps {
  open: boolean
  onClose: () => void
}

export function Sidebar({ open, onClose }: SidebarProps) {
  const { t } = useTranslation()

  // Close the mobile drawer with the Escape key.
  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  return (
    <>
      {/* Backdrop: mobile drawer only */}
      <div
        aria-hidden="true"
        onClick={onClose}
        className={`fixed inset-0 z-30 bg-slate-900/40 transition-opacity lg:hidden ${
          open ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
      />

      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-slate-200 bg-white transition-transform lg:visible lg:translate-x-0 ${
          open ? 'translate-x-0' : 'invisible -translate-x-full'
        }`}
      >
        <div className="flex h-16 shrink-0 items-center justify-between px-4">
          <div className="flex items-center gap-3">
            <span className="flex size-10 items-center justify-center rounded-xl bg-emerald-600 text-white">
              <Store aria-hidden="true" className="size-6" />
            </span>
            <div className="leading-tight">
              <p className="font-semibold text-slate-900">{t('app.name')}</p>
              <p className="text-xs text-slate-500">{t('app.tagline')}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('layout.closeMenu')}
            className="flex size-12 items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100 lg:hidden"
          >
            <X aria-hidden="true" className="size-6" />
          </button>
        </div>

        <nav aria-label={t('layout.mainNavigation')} className="flex-1 overflow-y-auto px-3 py-2">
          <ul className="space-y-1">
            {NAV_ITEMS.map(({ id, path, icon: Icon, labelKey }) => (
              <li key={id}>
                <NavLink
                  to={path}
                  end={path === '/'}
                  onClick={onClose}
                  className={({ isActive }) =>
                    `flex min-h-12 items-center gap-3 rounded-lg px-3 text-base font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600 ${
                      isActive
                        ? 'bg-emerald-50 text-emerald-800'
                        : 'text-slate-700 hover:bg-slate-100'
                    }`
                  }
                >
                  <Icon aria-hidden="true" className="size-5 shrink-0" />
                  {t(labelKey)}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </aside>
    </>
  )
}
