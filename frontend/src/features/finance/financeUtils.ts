import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import type { Range } from '@/api/finance'

export const TH = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'

/** A message a person can read, whatever went wrong. */
export function useErrorText() {
  const { t } = useTranslation()
  return (error: unknown): string => (error instanceof ApiError ? error.message : t('common.genericError'))
}

export function isoDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** The first of this month up to today: the default window of every finance screen. */
export function monthToDate(): Required<Range> {
  const now = new Date()
  return { date_from: isoDate(new Date(now.getFullYear(), now.getMonth(), 1)), date_to: isoDate(now) }
}

/** A random key made once per attempt, so a repeated request never records twice. */
export function newKey(): string {
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}
