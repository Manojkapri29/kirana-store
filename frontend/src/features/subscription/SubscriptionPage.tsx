import { useQuery } from '@tanstack/react-query'
import { Check, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { getSubscription } from '@/api/subscription'
import type { Subscription, SubscriptionPlan } from '@/api/types'
import { Alert, Badge, PageHeader, QueryError, Spinner } from '@/components/ui'
import { formatDate, formatMoney } from '@/lib/format'

const FEATURES = ['barcode_lookup', 'promotions', 'price_intelligence', 'advanced_reports', 'online_store'] as const
const LIMITS: { key: string; usage: keyof Subscription['usage'] }[] = [
  { key: 'max_products', usage: 'products' },
  { key: 'max_users', usage: 'users' },
  { key: 'max_monthly_invoices', usage: 'invoices' },
  { key: 'max_price_lookups_per_month', usage: 'price_lookups' },
]

function FeatureRow({ name, on }: { name: string; on: boolean }) {
  const { t } = useTranslation()
  return (
    <li className="flex items-center justify-between gap-3 py-2">
      <span className={on ? 'text-slate-900' : 'text-slate-500'}>{name}</span>
      <span className={`inline-flex items-center gap-1.5 text-sm font-medium ${on ? 'text-emerald-800' : 'text-slate-500'}`}>
        {on ? <Check aria-hidden="true" className="size-4" /> : <X aria-hidden="true" className="size-4" />}
        {on ? t('plan.included') : t('plan.notIncluded')}
      </span>
    </li>
  )
}

function PlanCard({ plan }: { plan: SubscriptionPlan }) {
  const { t } = useTranslation()
  const interval = plan.billing_interval === 'YEARLY' ? t('plan.perYear') : t('plan.perMonth')
  return (
    <li className={`rounded-xl border p-5 shadow-sm ${plan.is_current ? 'border-emerald-600 bg-emerald-50' : 'border-slate-200 bg-white'}`}>
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-lg font-semibold text-slate-900">{plan.name}</h3>
        {plan.is_current && <Badge tone="green">{t('plan.yourPlan')}</Badge>}
      </div>
      {plan.description && <p className="mt-1 text-sm text-slate-600">{plan.description}</p>}
      <p className="mt-2 text-slate-800">
        {plan.price === null ? t('plan.priceOnRequest') : `${plan.currency} ${formatMoney(plan.price).replace(/^[^\d-]+/, '')} ${interval}`}
      </p>
      <ul className="mt-3 divide-y divide-slate-100 text-sm">
        {FEATURES.map((feature) => (
          <FeatureRow key={feature} name={t(`plan.features.${feature}`)} on={Boolean(plan.features[feature])} />
        ))}
      </ul>
      <ul className="mt-3 space-y-1 text-sm text-slate-700">
        {LIMITS.map(({ key }) => (
          <li key={key} className="flex justify-between gap-3">
            <span>{t(`plan.limits.${key as 'max_products'}`)}</span>
            <span className="font-medium">{plan.limits[key] === null || plan.limits[key] === undefined ? t('plan.unlimited') : plan.limits[key]}</span>
          </li>
        ))}
      </ul>
    </li>
  )
}

/**
 * The shop's plan: what it includes, its limits and this month's use, and the other plans. There is no payment
 * here and no fake purchase: changing a plan is done by an administrator ("Contact admin").
 */
export function SubscriptionPage() {
  const { t } = useTranslation()
  const query = useQuery({ queryKey: ['subscription'], queryFn: getSubscription })
  if (query.isError) return <QueryError onRetry={() => void query.refetch()} />
  if (!query.data) return <Spinner />
  const s = query.data

  return (
    <div className="space-y-6">
      <PageHeader title={t('plan.title')} subtitle={t('plan.subtitle')} />

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <p className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t('plan.current')}</p>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h2 className="text-2xl font-bold text-slate-900">{s.plan_name}</h2>
          {s.status && <Badge tone={s.status === 'ACTIVE' || s.status === 'TRIAL' ? 'green' : 'slate'}>{s.status}</Badge>}
        </div>
        {s.source === 'default' && <p className="mt-2 text-sm text-slate-600">{t('plan.defaultPlan')}</p>}
        {s.ends_at && <p className="mt-2 text-sm text-slate-600">{t('plan.endsOn', { date: formatDate(s.ends_at.slice(0, 10)) })}</p>}
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-lg font-semibold text-slate-900">{t('plan.featuresHeading')}</h2>
          <ul className="mt-2 divide-y divide-slate-100">
            {FEATURES.map((feature) => (
              <FeatureRow key={feature} name={t(`plan.features.${feature}`)} on={Boolean(s.features[feature])} />
            ))}
          </ul>
        </section>
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-lg font-semibold text-slate-900">{t('plan.limitsHeading')}</h2>
          <ul className="mt-2 divide-y divide-slate-100">
            {LIMITS.map(({ key, usage }) => {
              const limit = s.limits[key]
              const used = s.usage[usage]
              return (
                <li key={key} className="flex items-center justify-between gap-3 py-2">
                  <span>{t(`plan.limits.${key as 'max_products'}`)}</span>
                  <span className="text-sm font-medium text-slate-800">
                    {limit === null || limit === undefined
                      ? `${t('plan.usedNoLimit', { used })} · ${t('plan.unlimited')}`
                      : t('plan.used', { used, limit })}
                  </span>
                </li>
              )
            })}
          </ul>
        </section>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-slate-900">{t('plan.plansHeading')}</h2>
        <ul className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {s.plans.map((plan) => (
            <PlanCard key={plan.code} plan={plan} />
          ))}
        </ul>
        <Alert tone="info">{t('plan.upgradeNote')}</Alert>
        <p>
          <span className="inline-flex min-h-12 cursor-not-allowed items-center rounded-lg border border-slate-300 bg-slate-100 px-5 font-semibold text-slate-600" aria-disabled="true">
            {t('plan.upgrade')}
          </span>
        </p>
      </section>
    </div>
  )
}
