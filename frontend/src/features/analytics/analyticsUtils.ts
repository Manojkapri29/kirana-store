import type { ReportQuery, Value } from '@/api/analytics'
import { formatMoney, formatQuantity } from '@/lib/format'

/** A custom period is only ready to query once both dates are chosen. */
export function queryReady(q: ReportQuery): boolean {
  return q.preset !== 'custom' || Boolean(q.date_from && q.date_to)
}

/** Shows a value the way the backend says it is: a number, or the honest reason there is none. */
export function valueText(v: Value, unit: string, labels: { notAvailable: string; insufficientData: string }): string {
  if (v.amount === null) return v.availability === 'NOT_AVAILABLE' ? labels.notAvailable : labels.insufficientData
  if (unit === 'money') return formatMoney(v.amount)
  if (unit === 'percent') return `${v.amount}%`
  return formatQuantity(v.amount)
}

