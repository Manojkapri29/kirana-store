import { useTranslation } from 'react-i18next'

import type { StockStatus } from '@/api/types'
import { Badge, type BadgeTone } from '@/components/ui'

const TONES: Record<StockStatus, BadgeTone> = {
  IN_STOCK: 'green',
  LOW_STOCK: 'amber',
  OUT_OF_STOCK: 'red',
}

export function StockStatusBadge({ status }: { status: StockStatus }) {
  const { t } = useTranslation()
  const label = {
    IN_STOCK: t('stock.inStock'),
    LOW_STOCK: t('stock.lowStock'),
    OUT_OF_STOCK: t('stock.outOfStock'),
  }[status]
  return <Badge tone={TONES[status]}>{label}</Badge>
}
