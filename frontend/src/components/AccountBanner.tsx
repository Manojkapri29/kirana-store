import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { getAccount } from '@/api/account'

import { Alert } from './ui'

/**
 * Tells the owner when the shop's account is restricted (suspended or deactivated), in the words the server chose.
 * Nothing is hidden or deleted: it explains why some actions are refused. The server enforces the restriction.
 */
export function AccountBanner() {
  const { t } = useTranslation()
  const { data } = useQuery({ queryKey: ['account'], queryFn: getAccount, staleTime: 60_000, retry: false })
  if (!data || (data.account_status !== 'SUSPENDED' && data.account_status !== 'DEACTIVATED')) return null
  return (
    <div className="mx-auto w-full max-w-7xl px-4 pt-4 sm:px-6">
      <Alert tone="warning">
        <p className="font-semibold">{t(data.account_status === 'SUSPENDED' ? 'account.suspendedTitle' : 'account.deactivatedTitle')}</p>
        {data.message && <p className="mt-1 text-sm">{data.message}</p>}
        <p className="mt-1 text-sm">{t('account.dataSafe')}</p>
      </Alert>
    </div>
  )
}
