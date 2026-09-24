import { Download, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Alert, Button } from '@/components/ui'

import { applyUpdate } from './register'

interface InstallEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

/** A new release is ready: the person chooses when to switch, so an update never swaps the app under a sale in progress. */
export function UpdateBanner() {
  const { t } = useTranslation()
  const [registration, setRegistration] = useState<ServiceWorkerRegistration | null>(null)
  useEffect(() => {
    const on = (event: Event) => setRegistration((event as CustomEvent<ServiceWorkerRegistration>).detail)
    window.addEventListener('shop-update-ready', on)
    return () => window.removeEventListener('shop-update-ready', on)
  }, [])
  if (!registration) return null
  return (
    <div className="mx-auto w-full max-w-7xl px-4 pt-4 sm:px-6">
      <Alert tone="info">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="flex items-center gap-2 font-medium"><RefreshCw aria-hidden="true" className="size-4" />{t('pwa.updateReady')}</p>
          <Button variant="secondary" onClick={() => applyUpdate(registration)}>{t('pwa.updateNow')}</Button>
        </div>
      </Alert>
    </div>
  )
}

/** Offers "install" only when the browser says the app can be installed (it decides; nothing is faked). */
export function InstallButton() {
  const { t } = useTranslation()
  const [event, setEvent] = useState<InstallEvent | null>(null)
  useEffect(() => {
    const on = (e: Event) => {
      e.preventDefault()
      setEvent(e as InstallEvent)
    }
    window.addEventListener('beforeinstallprompt', on)
    window.addEventListener('appinstalled', () => setEvent(null))
    return () => window.removeEventListener('beforeinstallprompt', on)
  }, [])
  if (!event) return null
  return (
    <Button variant="secondary" onClick={() => void event.prompt().then(() => setEvent(null))}>
      <Download aria-hidden="true" className="size-4" />
      {t('pwa.install')}
    </Button>
  )
}
