import { useTranslation } from 'react-i18next'

import type { PurchaseStatus } from '@/api/types'
import { Badge, type BadgeTone } from '@/components/ui'

const TONES: Record<PurchaseStatus, BadgeTone> = { DRAFT: 'amber', POSTED: 'green', VOID: 'slate' }

export function PurchaseStatusBadge({ status }: { status: PurchaseStatus }) {
  const { t } = useTranslation()
  return <Badge tone={TONES[status]}>{t(`purchases.status.${status}`)}</Badge>
}
