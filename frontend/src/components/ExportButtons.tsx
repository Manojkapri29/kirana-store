import { FileSpreadsheet, FileText } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { downloadExport, type ExportKind } from '@/api/exports'
import type { Query } from '@/api/client'
import type { ExportFormat } from '@/api/types'
import { Alert, Button } from '@/components/ui'
import { useCan } from '@/features/auth/authContext'

interface ExportButtonsProps {
  kind: ExportKind
  /** Same filters as the list being viewed, so the file matches the screen. */
  filters?: Query
}

export function ExportButtons({ kind, filters }: ExportButtonsProps) {
  const { t } = useTranslation()
  const can = useCan()
  const [busy, setBusy] = useState<ExportFormat | null>(null)
  const [failed, setFailed] = useState(false)

  async function run(format: ExportFormat) {
    setBusy(format)
    setFailed(false)
    try {
      await downloadExport(kind, format, filters)
    } catch {
      setFailed(true)
    } finally {
      setBusy(null)
    }
  }

  // Stock files need INVENTORY_EXPORT, every other file REPORT_EXPORT. (Only a convenience: the server checks it too.)
  const needed = kind === 'inventory' || kind === 'inventory-history' ? 'INVENTORY_EXPORT' : 'REPORT_EXPORT'
  if (!can(needed)) return null

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-3">
        <Button variant="secondary" loading={busy === 'csv'} disabled={busy !== null} onClick={() => void run('csv')}>
          <FileText aria-hidden="true" className="size-5" />
          {busy === 'csv' ? t('common.exporting') : t('common.exportCsv')}
        </Button>
        <Button variant="secondary" loading={busy === 'xlsx'} disabled={busy !== null} onClick={() => void run('xlsx')}>
          <FileSpreadsheet aria-hidden="true" className="size-5" />
          {busy === 'xlsx' ? t('common.exporting') : t('common.exportExcel')}
        </Button>
      </div>
      {failed && <Alert tone="error">{t('common.exportFailed')}</Alert>}
    </div>
  )
}
