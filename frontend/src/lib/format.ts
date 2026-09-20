/** Display formatting only. Values arrive as exact strings and are never used for arithmetic here. */

const money = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' })
const quantity = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 3 })

export const NOT_SET = '—'

/** The symbol shown in money inputs. Amounts themselves carry no currency (see docs/ARCHITECTURE.md). */
export const CURRENCY_SYMBOL = '₹'

/** "250.00" -> "₹250.00", with Indian digit grouping (₹1,25,000.00). `null` means "not set", not zero. */
export function formatMoney(value: string | null | undefined): string {
  return value === null || value === undefined ? NOT_SET : money.format(Number(value))
}

/** "20.000" -> "20", "2.500" -> "2.5". */
export function formatQuantity(value: string | null | undefined): string {
  return value === null || value === undefined ? NOT_SET : quantity.format(Number(value))
}

/** "2026-09-20" -> "20/09/2026" (no timezone maths: business dates are plain dates). */
export function formatDate(isoDate: string): string {
  const [year, month, day] = isoDate.split('-')
  return `${day}/${month}/${year}`
}

/** A UTC timestamp shown in the browser's local time as "20/09/2026, 14:30". */
export function formatDateTime(isoTimestamp: string): string {
  return new Date(isoTimestamp).toLocaleString('en-GB', { dateStyle: 'short', timeStyle: 'short' })
}
