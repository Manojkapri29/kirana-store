import { useTranslation } from 'react-i18next'

import { Alert, Button } from './ui'

/** Offered when an unsaved form from earlier was found. Nothing is applied until the person chooses. */
export function RestoreBanner({ onRestore, onDiscard }: { onRestore: () => void; onDiscard: () => void }) {
  const { t } = useTranslation()
  return (
    <Alert tone="info">
      <p>{t('recovery.restore.found')}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        <Button onClick={onRestore}>{t('recovery.restore.restore')}</Button>
        <Button variant="secondary" onClick={onDiscard}>
          {t('recovery.restore.discard')}
        </Button>
      </div>
    </Alert>
  )
}
