import { useTranslation } from 'react-i18next'

import type { Promotion, PromotionStatus } from '@/api/types'
import { Badge, type BadgeTone } from '@/components/ui'

const TONES: Record<PromotionStatus, BadgeTone> = { DRAFT: 'amber', ACTIVE: 'green', PAUSED: 'slate', EXPIRED: 'red' }

/** The status as it really is now (an offer past its end date reads as Expired), plus scheduled / live. */
export function PromotionStatusBadge({ promotion }: { promotion: Promotion }) {
  const { t } = useTranslation()
  const status = promotion.effective_status
  const scheduled = status === 'ACTIVE' && !promotion.is_live
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <Badge tone={scheduled ? 'amber' : TONES[status]}>{scheduled ? t('promotions.scheduled') : t(`promotions.status.${status}`)}</Badge>
    </span>
  )
}
