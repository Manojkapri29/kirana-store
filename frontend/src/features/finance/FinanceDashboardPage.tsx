import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { getAlerts, getDashboard, notifyAlerts, type Change } from '@/api/finance'
import { Alert, Button, PageHeader, QueryError, Spinner } from '@/components/ui'
import { formatDate } from '@/lib/format'

import { Amount, FinanceTabs, MiniBars, RangePicker, Stat } from './common'
import { monthToDate } from './financeUtils'

function Delta({ change }: { change: Change | null }) {
  const { t } = useTranslation()
  if (!change) return null
  if (change.change_pct === null) return <span>{t('finance.noComparison')}</span>
  return <span>{t('finance.vsPrevious', { pct: change.change_pct })}</span>
}

export function FinanceDashboardPage() {
  const { t } = useTranslation()
  const [range, setRange] = useState(monthToDate())
  const dash = useQuery({ queryKey: ['financeDashboard', range], queryFn: () => getDashboard({ ...range, compare: true }) })
  const alerts = useQuery({ queryKey: ['financeAlerts'], queryFn: getAlerts })
  const notify = useMutation({ mutationFn: notifyAlerts })
  const d = dash.data
  const p = d?.pnl

  return (
    <div className="space-y-6">
      <PageHeader title={t('finance.title')} subtitle={t('finance.subtitle')} />
      <FinanceTabs />
      <RangePicker value={range} onChange={setRange} />
      {dash.isError && <QueryError error={dash.error} onRetry={() => void dash.refetch()} />}
      {dash.isPending && <Spinner />}

      {alerts.data && alerts.data.length > 0 && (
        <section aria-labelledby="fin-alerts" className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h2 id="fin-alerts" className="text-lg font-semibold">{t('finance.alerts')}</h2>
            <Button variant="secondary" requires="FINANCE_MANAGE" loading={notify.isPending} onClick={() => notify.mutate()}>{t('finance.alertsSend')}</Button>
          </div>
          {alerts.data.map((a) => (
            <Alert key={a.code + JSON.stringify(a.figures)} tone={a.severity === 'REVIEW' ? 'warning' : 'info'}>
              <p className="font-medium">{a.title}</p>
              <p className="text-sm">{a.message}</p>
            </Alert>
          ))}
        </section>
      )}

      {p && d && (
        <>
          {p.status !== 'ACTUAL' && <Alert tone="warning">{t('finance.profitNotAvailable')}</Alert>}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label={t('finance.revenue')} value={<Amount value={p.revenue} />} hint={<Delta change={d.revenue_change} />} />
            <Stat label={t('finance.cogs')} value={<Amount value={p.cogs} />} />
            <Stat label={t('finance.grossProfit')} value={<Amount value={p.gross_profit} />} hint={<Delta change={d.gross_profit_change} />} />
            <Stat label={t('finance.netProfit')} value={<Amount value={p.net_profit} />} hint={<Delta change={d.net_profit_change} />} />
            <Stat label={t('finance.expenses')} value={<Amount value={p.operating_expenses} />} hint={<Delta change={d.expenses_change} />} />
            <Stat label={t('finance.cashIn')} value={<Amount value={d.cash_flow.inflow} />} />
            <Stat label={t('finance.cashOut')} value={<Amount value={d.cash_flow.outflow} />} hint={<Delta change={d.net_cash_flow_change} />} />
            <Stat label={t('finance.netCash')} value={<Amount value={d.cash_flow.net} />} />
            <Stat label={t('finance.receivables')} value={<Amount value={d.receivables_total} />} hint={t('finance.fromKhata')} />
            <Stat label={t('finance.payables')} value={<Amount value={d.payables_total} />} />
            <Stat label={t('finance.taxLabel')} value={d.tax_status === 'CONFIGURED' ? <Amount value={d.tax_collected} /> : t('finance.notConfigured')} hint={d.tax_status === 'CONFIGURED' ? t('finance.taxCollected') : undefined} />
          </div>
          {d.comparison_from && d.comparison_to && (
            <p className="text-sm text-slate-600">{t('finance.comparedWith', { from: formatDate(d.comparison_from), to: formatDate(d.comparison_to) })}</p>
          )}
          <div className="grid gap-4 lg:grid-cols-2">
            <MiniBars title={t('finance.charts.revenue')} points={d.revenue_trend.map((x) => ({ label: formatDate(x.period_start), value: x.revenue }))} />
            <MiniBars title={t('finance.charts.profit')} points={d.revenue_trend.map((x) => ({ label: formatDate(x.period_start), value: x.net_profit }))} />
            <MiniBars title={t('finance.charts.expense')} points={d.revenue_trend.map((x) => ({ label: formatDate(x.period_start), value: x.operating_expenses }))} />
            <MiniBars title={t('finance.charts.cashFlow')} points={d.cash_flow_trend.map((x) => ({ label: formatDate(x.period_start), value: x.net }))} />
            <MiniBars title={t('finance.charts.receivablesAging')} points={Object.entries(d.receivables_aging).map(([k, v]) => ({ label: t('finance.days', { range: k }), value: v }))} />
            <MiniBars title={t('finance.charts.payablesAging')} points={Object.entries(d.payables_aging).map(([k, v]) => ({ label: t('finance.days', { range: k }), value: v }))} />
            <MiniBars title={t('finance.charts.expenseCategories')} points={d.expense_categories.map((c) => ({ label: c.name, value: c.amount }))} />
            <MiniBars title={t('finance.charts.paymentMix')} points={d.payment_method_mix.map((m) => ({ label: t(`finance.methods.${m.payment_method}`, { defaultValue: m.payment_method }), value: m.inflow }))} />
          </div>
          {d.notes.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">{d.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
        </>
      )}
    </div>
  )
}
