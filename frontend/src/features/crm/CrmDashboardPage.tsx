import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { getCrmDashboard } from '@/api/crm'
import { FilterSelect } from '@/components/fields'
import { PageHeader, QueryError, Spinner } from '@/components/ui'
import { formatMoney, NOT_SET } from '@/lib/format'

import { CrmTabs, Stat } from './common'

const PERIODS = [30, 90, 180, 365] as const

export function CrmDashboardPage() {
  const { t } = useTranslation()
  const [period, setPeriod] = useState<number>(90)
  const dash = useQuery({ queryKey: ['crmDashboard', period], queryFn: () => getCrmDashboard(period) })
  const d = dash.data
  const rate = (v: string | null) => (v === null ? t('crm.notAvailable') : `${v}%`)

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('crm.title')}
        subtitle={t('crm.subtitle')}
        actions={
          <FilterSelect aria-label={t('crm.period')} value={period} onChange={(e) => setPeriod(Number(e.target.value))}>
            {PERIODS.map((p) => <option key={p} value={p}>{t('crm.lastDays', { count: p })}</option>)}
          </FilterSelect>
        }
      />
      <CrmTabs />
      {dash.isError && <QueryError error={dash.error} onRetry={() => void dash.refetch()} />}
      {dash.isPending && <Spinner />}
      {d && (
        <>
          <section aria-labelledby="crm-customers" className="space-y-3">
            <h2 id="crm-customers" className="text-lg font-semibold">{t('crm.sections.customers')}</h2>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
              <Stat label={t('crm.stats.total')} value={d.customers.total_customers} />
              <Stat label={t('crm.stats.new')} value={d.customers.new_customers} />
              <Stat label={t('crm.stats.active')} value={d.customers.active_customers} />
              <Stat label={t('crm.stats.returning')} value={d.customers.returning_customers} />
              <Stat label={t('crm.stats.inactive')} value={d.customers.inactive_customers} />
            </div>
          </section>
          <section aria-labelledby="crm-revenue" className="space-y-3">
            <h2 id="crm-revenue" className="text-lg font-semibold">{t('crm.sections.revenue')}</h2>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
              <Stat label={t('crm.stats.revenue')} value={formatMoney(d.revenue.customer_revenue)} />
              <Stat label={t('crm.stats.avgTxn')} value={d.revenue.average_transaction_value ? formatMoney(d.revenue.average_transaction_value) : NOT_SET} />
              <Stat label={t('crm.stats.repeatRevenue')} value={formatMoney(d.revenue.repeat_customer_revenue)} />
            </div>
          </section>
          <section aria-labelledby="crm-retention" className="space-y-3">
            <h2 id="crm-retention" className="text-lg font-semibold">{t('crm.sections.retention')}</h2>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label={t('crm.stats.repeatRate')} value={rate(d.retention.repeat_purchase_rate)} hint={t('crm.stats.repeatRateHint', { repeat: d.retention.repeat_customers, total: d.retention.customers_with_purchases })} />
              <Stat label={t('crm.stats.cohort')} value={d.retention.cohort_retention_rate === 'NOT_ENOUGH_DATA' ? t('crm.notEnoughData') : `${d.retention.cohort_retention_rate}%`} />
              <Stat label={t('crm.stats.interval')} value={d.retention.average_purchase_interval_days ? t('crm.days', { count: Number(d.retention.average_purchase_interval_days) }) : t('crm.notAvailable')} />
              <Stat label={t('crm.stats.reactivated')} value={d.retention.reactivated_count} />
            </div>
          </section>
          <section aria-labelledby="crm-engagement" className="space-y-3">
            <h2 id="crm-engagement" className="text-lg font-semibold">{t('crm.sections.engagement')}</h2>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label={t('crm.stats.pointsIssued')} value={d.loyalty.points_issued} hint={d.loyalty.program_active ? undefined : t('crm.loyaltyOff')} />
              <Stat label={t('crm.stats.pointsRedeemed')} value={d.loyalty.points_redeemed} />
              <Stat label={t('crm.stats.pointsOutstanding')} value={d.loyalty.points_outstanding} />
              <Stat label={t('crm.stats.campaigns')} value={d.campaigns.total_campaigns} hint={t('crm.stats.campaignsHint', { running: d.campaigns.running, completed: d.campaigns.completed, draft: d.campaigns.draft })} />
              <Stat label={t('crm.stats.referrals')} value={d.referrals.total_referrals} hint={t('crm.stats.referralsHint', { ok: d.referrals.successful_referrals, pending: d.referrals.pending_referrals })} />
            </div>
          </section>
        </>
      )}
    </div>
  )
}
