import { useQuery } from '@tanstack/react-query'
import { Sparkles } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'

import { getBackupStatus, getBusinessHealth, getOverview, getUsage } from '@/api/account'
import { Alert, Badge, QueryError, Spinner } from '@/components/ui'
import { localIsoDate } from '@/lib/dates'
import { useCan } from '@/features/auth/authContext'
import { formatDateTime, formatMoney } from '@/lib/format'

const today = () => localIsoDate()

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-sm font-medium text-slate-600">{label}</p>
      <p className="mt-2 text-3xl font-bold text-slate-900">{value}</p>
      {hint && <p className="mt-2 text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

/** Today at a glance, straight from the shop's records. Nothing is estimated; what cannot be known says so. */
function TodaySummary() {
  const { t } = useTranslation()
  const query = useQuery({ queryKey: ['dashboard', 'today'], queryFn: () => getOverview({ date_from: today(), date_to: today() }) })
  if (query.isError) return <QueryError error={query.error} onRetry={() => void query.refetch()} />
  if (!query.data) return <Spinner />
  const o = query.data
  return (
    <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Kpi label={t('dashboard.kpi.todaysSales')} value={formatMoney(o.sales.net)} hint={t('dashboard.bills', { count: o.sales.transactions })} />
      <Kpi label={t('dashboard.kpi.todaysPurchases')} value={formatMoney(o.purchases.total)} />
      <Kpi label={t('dashboard.kpi.stockValue')} value={o.inventory.stock_value ? formatMoney(o.inventory.stock_value) : t('insights.notAvailable')} hint={o.inventory.products_without_cost > 0 ? t('insights.withoutCost', { count: o.inventory.products_without_cost }) : undefined} />
      <Kpi label={t('insights.outstanding')} value={formatMoney(o.customers.outstanding_total)} hint={t('dashboard.customersOwing', { count: o.customers.customers_owing })} />
      <Kpi label={t('dashboard.kpi.totalProducts')} value={String(o.inventory.products)} />
      <Kpi label={t('dashboard.kpi.lowStock')} value={String(o.inventory.low_stock)} />
      <Kpi label={t('dashboard.kpi.outOfStock')} value={String(o.inventory.out_of_stock)} />
      <Kpi label={t('insights.profit')} value={o.profit.available ? formatMoney(o.profit.gross_profit) : t('insights.notAvailable')} />
    </section>
  )
}

function HealthPanel() {
  const { t } = useTranslation()
  const query = useQuery({ queryKey: ['account', 'health'], queryFn: getBusinessHealth, retry: false })
  if (query.isError) return null // a health card is a convenience: never a reason to hide the page
  if (!query.data) return null
  const { alerts } = query.data
  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">{t('dashboard.health')}</h2>
      {alerts.length === 0 ? (
        <p className="text-sm text-slate-600">{t('dashboard.healthNothing')}</p>
      ) : (
        <ul className="space-y-2">
          {alerts.map((a) => (
            <li key={a.kind} className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
              <p className="font-semibold">{a.title}</p>
              <p>{a.message}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function UsagePanel() {
  const { t } = useTranslation()
  const usage = useQuery({ queryKey: ['account', 'usage'], queryFn: getUsage, retry: false })
  const backup = useQuery({ queryKey: ['account', 'backup'], queryFn: getBackupStatus, retry: false })
  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">{t('dashboard.planUse')}</h2>
      {usage.data ? (
        <ul className="space-y-2 text-sm">
          {Object.entries(usage.data.limits)
            .filter(([, item]) => !item.unlimited)
            .map(([key, item]) => (
              <li key={key}>
                <div className="flex justify-between gap-3">
                  <span className="text-slate-700">{t(`plan.limits.${key as 'max_products'}`, { defaultValue: key })}</span>
                  <span className="font-medium">
                    {item.used} / {item.limit}
                  </span>
                </div>
                <div className="mt-1 h-2 rounded-full bg-slate-100">
                  <div className={`h-2 rounded-full ${(item.percent_used ?? 0) >= 90 ? 'bg-red-500' : 'bg-emerald-500'}`} style={{ width: `${Math.min(item.percent_used ?? 0, 100)}%` }} />
                </div>
              </li>
            ))}
        </ul>
      ) : null}
      <p className="text-sm text-slate-700">
        {t('dashboard.backup')}:{' '}
        {backup.data ? (
          <>
            <Badge tone={backup.data.state === 'recent' ? 'green' : backup.data.state === 'stale' ? 'amber' : 'slate'}>{t(`dashboard.backupState.${backup.data.state}`)}</Badge>
            {backup.data.last_backup_at && <span className="ml-2 text-slate-500">{formatDateTime(backup.data.last_backup_at)}</span>}
          </>
        ) : (
          '—'
        )}
      </p>
      <Link to="/plan" className="text-sm font-medium text-emerald-700 underline">
        {t('dashboard.seePlan')}
      </Link>
    </section>
  )
}

export function DashboardPage() {
  const { t } = useTranslation()
  const can = useCan()
  const navigate = useNavigate()
  const [question, setQuestion] = useState('')
  const ask = (event: FormEvent) => {
    event.preventDefault()
    void navigate(question.trim() ? `/assistant?q=${encodeURIComponent(question.trim())}` : '/assistant')
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">{t('dashboard.title')}</h1>
        <p className="mt-1 text-slate-600">{t('dashboard.subtitle')}</p>
      </div>

      <form onSubmit={ask} className="space-y-2 rounded-xl border border-emerald-200 bg-emerald-50 p-4">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-emerald-900">
          <Sparkles aria-hidden="true" className="size-5" />
          {t('assistant.askHeading')}
        </h2>
        <div className="flex gap-3">
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={t('assistant.placeholder')}
            aria-label={t('assistant.askHeading')}
            maxLength={600}
            className="block min-h-12 flex-1 rounded-lg border border-slate-300 bg-white px-3 text-base focus-visible:outline-2 focus-visible:outline-emerald-600"
          />
          <button type="submit" className="min-h-12 rounded-lg bg-emerald-600 px-5 font-medium text-white hover:bg-emerald-700">
            {t('assistant.ask')}
          </button>
        </div>
      </form>

      {can('REPORT_VIEW') && <TodaySummary />}

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {can('REPORT_VIEW') && <HealthPanel />}
        {can('SUBSCRIPTION_VIEW') && <UsagePanel />}
      </section>

      {can('REPORT_VIEW') && <Alert tone="info">
        <p className="text-sm">
          {t('dashboard.moreInInsights')}{' '}
          <Link to="/insights" className="font-medium underline">
            {t('nav.insights')}
          </Link>
        </p>
      </Alert>}
    </div>
  )
}
