import { useTranslation } from 'react-i18next'

import type { BalanceStatus } from '@/api/types'
import { Badge, type BadgeTone } from '@/components/ui'
import { formatMoney } from '@/lib/format'

const TONES: Record<BalanceStatus, BadgeTone> = { OUTSTANDING: 'red', SETTLED: 'slate', ADVANCE: 'green' }

export function BalanceBadge({ status }: { status: BalanceStatus }) {
  const { t } = useTranslation()
  return <Badge tone={TONES[status]}>{t(`customers.balanceStatus.${status}`)}</Badge>
}

/** "-300.00" -> "300.00": the sign is shown in words (owes / advance), not as a minus. */
const absolute = (value: string): string => (value.startsWith('-') ? value.slice(1) : value)

const TEXT_TONES: Record<BalanceStatus, string> = {
  OUTSTANDING: 'text-red-700',
  SETTLED: 'text-slate-700',
  ADVANCE: 'text-emerald-700',
}

/** A signed balance as an amount plus what it means: "₹1,200.00 owes", "₹300.00 advance", "₹0.00 settled". */
export function BalanceAmount({ balance, status, large = false }: { balance: string; status: BalanceStatus; large?: boolean }) {
  const { t } = useTranslation()
  return (
    <span className={TEXT_TONES[status]}>
      <span className={large ? 'text-3xl font-bold' : 'font-semibold'}>{formatMoney(absolute(balance))}</span>{' '}
      <span className={large ? 'text-base font-medium' : 'text-sm'}>{t(`customers.balanceWords.${status}`)}</span>
    </span>
  )
}
