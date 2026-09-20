import { useTranslation } from 'react-i18next'

import { useBackendStatus, type BackendStatus } from '@/hooks/useBackendStatus'

const DOT_STYLES: Record<BackendStatus, string> = {
  checking: 'bg-amber-400',
  online: 'bg-emerald-500',
  offline: 'bg-red-500',
}

export function BackendStatusBadge() {
  const { t } = useTranslation()
  const status = useBackendStatus()

  return (
    <div
      role="status"
      className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700"
    >
      <span aria-hidden="true" className={`size-2.5 rounded-full ${DOT_STYLES[status]}`} />
      {/* The text is hidden on very small screens; the dot keeps the state visible. */}
      <span className="hidden sm:inline">{t(`status.${status}`)}</span>
      <span className="sr-only sm:hidden">{t(`status.${status}`)}</span>
    </div>
  )
}
