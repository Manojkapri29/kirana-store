import { WifiOff } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { useOnline } from '@/hooks/useOnline'

import { Alert } from './ui'

/** Shown while the browser has no connection. It says what still works (the offline till) and what does not (everything else). */
export function OfflineBanner() {
  const { t } = useTranslation()
  const online = useOnline()
  if (online) return null
  return (
    <div className="mx-auto w-full max-w-7xl px-4 pt-4 sm:px-6">
      <Alert tone="warning">
        <p className="flex items-center gap-2 font-semibold">
          <WifiOff aria-hidden="true" className="size-4" />
          {t('offline.title')}
        </p>
        <p className="mt-1 text-sm">{t('offline.message')}</p>
        <Link to="/offline" className="mt-2 inline-block text-sm font-semibold underline">{t('offline.openTill')}</Link>
      </Alert>
    </div>
  )
}
