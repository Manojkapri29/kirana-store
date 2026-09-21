import { useTranslation } from 'react-i18next'

import type { SaleStatus } from '@/api/types'
import { Badge, type BadgeTone } from '@/components/ui'

const TONES: Record<SaleStatus, BadgeTone> = { DRAFT: 'amber', POSTED: 'green', VOID: 'slate' }

export function SaleStatusBadge({ status }: { status: SaleStatus }) {
  const { t } = useTranslation()
  return <Badge tone={TONES[status]}>{t(`sales.status.${status}`)}</Badge>
}
