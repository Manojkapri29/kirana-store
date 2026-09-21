import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Alert, Button } from '@/components/ui'

import { useAuth } from './authContext'

/** A few minutes before the session ends for being idle, say so and let the person keep it going. */
export function SessionBanner() {
  const { t } = useTranslation()
  const { endsInMinutes, stayActive } = useAuth()
  const [busy, setBusy] = useState(false)
  if (endsInMinutes === null) return null
  return (
    <div className="mx-auto w-full max-w-7xl px-4 pt-4 sm:px-6">
      <Alert tone="warning">
        <p className="font-semibold">{t('auth.endingSoon', { count: endsInMinutes })}</p>
        <p className="mt-1 text-sm">{t('auth.endingHint')}</p>
        <Button
          className="mt-2"
          variant="secondary"
          loading={busy}
          onClick={() => {
            setBusy(true)
            void stayActive().finally(() => setBusy(false))
          }}
        >
          {t('auth.stayIn')}
        </Button>
      </Alert>
    </div>
  )
}
