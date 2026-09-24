import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'

/** A message a person can read, whatever went wrong. */
export function useErrorText() {
  const { t } = useTranslation()
  return (error: unknown): string => (error instanceof ApiError ? error.message : t('common.genericError'))
}

export const TH = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'
