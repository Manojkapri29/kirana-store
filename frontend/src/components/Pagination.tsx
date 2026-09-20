import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui'

interface PaginationProps {
  total: number
  limit: number
  offset: number
  onChange: (offset: number) => void
}

export function Pagination({ total, limit, offset, onChange }: PaginationProps) {
  const { t } = useTranslation()
  if (total === 0) return null
  const from = offset + 1
  const to = Math.min(offset + limit, total)

  return (
    <div className="flex flex-col items-center justify-between gap-3 sm:flex-row">
      <p className="text-sm text-slate-600">{t('common.showing', { from, to, total })}</p>
      <div className="flex gap-3">
        <Button variant="secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          <ChevronLeft aria-hidden="true" className="size-5" />
          {t('common.previous')}
        </Button>
        <Button variant="secondary" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
          {t('common.next')}
          <ChevronRight aria-hidden="true" className="size-5" />
        </Button>
      </div>
    </div>
  )
}
