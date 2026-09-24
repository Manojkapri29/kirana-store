import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { getCashFlow, getPayables, getPnl, getReceivables, getTaxSummary, type Aging } from '@/api/finance'
import { Alert, PageHeader, QueryError, Spinner } from '@/components/ui'

import { Amount, FinanceTabs, MiniBars, RangePicker, Stat } from './common'
import { monthToDate, TH } from './financeUtils'

type Tab = 'pnl' | 'cashflow' | 'tax' | 'receivables' | 'payables'
const TABS: readonly Tab[] = ['pnl', 'cashflow', 'tax', 'receivables', 'payables']

function AgingBars({ title, aging }: { title: string; aging: Aging }) {
  const { t } = useTranslation()
  return <MiniBars title={title} points={Object.entries(aging).map(([k, v]) => ({ label: t('finance.days', { range: k }), value: v }))} />
}

function Pnl({ range }: { range: ReturnType<typeof monthToDate> }) {
  const { t } = useTranslation()
  const q = useQuery({ queryKey: ['pnl', range], queryFn: () => getPnl(range) })
  if (q.isError) return <QueryError error={q.error} onRetry={() => void q.refetch()} />
  if (!q.data) return <Spinner />
  const p = q.data
  return (
    <div className="space-y-4">
      {p.status !== 'ACTUAL' && <Alert tone="warning"><p className="font-medium">{t('finance.profitNotAvailable')}</p><p className="text-sm">{p.status_note}</p></Alert>}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Stat label={t('finance.pnl.detailed')} value={<Amount value={p.detailed_sales} />} />
        <Stat label={t('finance.pnl.quick')} value={<Amount value={p.quick_sales} />} hint={p.quick_sales_have_no_cost ? t('finance.pnl.quickNoCost') : undefined} />
        <Stat label={t('finance.pnl.returns')} value={<Amount value={p.sales_returns} />} />
        <Stat label={t('finance.revenue')} value={<Amount value={p.revenue} />} />
        <Stat label={t('finance.cogs')} value={<Amount value={p.cogs} />} />
        <Stat label={t('finance.grossProfit')} value={<Amount value={p.gross_profit} />} hint={p.gross_margin_pct !== null ? t('finance.pnl.margin', { pct: p.gross_margin_pct }) : undefined} />
        <Stat label={t('finance.expenses')} value={<Amount value={p.operating_expenses} />} hint={t('finance.pnl.postedOnly')} />
        <Stat label={t('finance.netProfit')} value={<Amount value={p.net_profit} />} />
      </div>
      {p.costed_sales.status === 'PARTIAL' && (
        <Alert tone="info">{t('finance.pnl.costedOnly', { profit: p.costed_sales.gross_profit ?? '', pct: p.costed_sales.coverage_pct ?? '' })}</Alert>
      )}
      <p className="text-sm text-slate-600">{t('finance.pnl.formula')}</p>
      <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">{p.notes.map((n) => <li key={n}>{n}</li>)}</ul>
    </div>
  )
}

function CashFlow({ range }: { range: ReturnType<typeof monthToDate> }) {
  const { t } = useTranslation()
  const q = useQuery({ queryKey: ['cashFlowReport', range], queryFn: () => getCashFlow(range) })
  if (q.isError) return <QueryError error={q.error} onRetry={() => void q.refetch()} />
  if (!q.data) return <Spinner />
  const r = q.data
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        <Stat label={t('finance.cashIn')} value={<Amount value={r.inflow} />} /><Stat label={t('finance.cashOut')} value={<Amount value={r.outflow} />} /><Stat label={t('finance.netCash')} value={<Amount value={r.net} />} />
      </div>
      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200">
          <thead className="bg-slate-50"><tr><th className={TH}>{t('finance.cf.class')}</th><th className={`${TH} text-right`}>{t('finance.cashIn')}</th><th className={`${TH} text-right`}>{t('finance.cashOut')}</th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {Object.entries(r.by_class).filter(([, f]) => Number(f.inflow) || Number(f.outflow)).map(([k, f]) => (
              <tr key={k}><td className="px-4 py-2 text-sm">{t(`finance.cf.classes.${k}`, { defaultValue: k })}</td><td className="px-4 py-2 text-right text-sm"><Amount value={f.inflow} /></td><td className="px-4 py-2 text-right text-sm"><Amount value={f.outflow} /></td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-sm text-slate-600">{r.basis}</p>
      {r.notes.map((n) => <Alert key={n} tone="info">{n}</Alert>)}
    </div>
  )
}

function Tax({ range }: { range: ReturnType<typeof monthToDate> }) {
  const { t } = useTranslation()
  const q = useQuery({ queryKey: ['taxSummary', range], queryFn: () => getTaxSummary(range) })
  if (q.isError) return <QueryError error={q.error} onRetry={() => void q.refetch()} />
  if (!q.data) return <Spinner />
  const s = q.data
  if (s.status !== 'CONFIGURED') return <Alert tone="warning">{t('finance.tax.notConfigured')}</Alert>
  return (
    <div className="space-y-4">
      <Alert tone="info">{s.disclaimer}</Alert>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Stat label={t('finance.tax.taxableSales')} value={<Amount value={s.sales?.taxable_amount} />} />
        <Stat label={t('finance.taxCollected')} value={<Amount value={s.tax_collected} />} hint={t('finance.tax.netOfReturns')} />
        <Stat label={t('finance.tax.taxablePurchases')} value={<Amount value={s.purchases?.taxable_amount} />} />
        <Stat label={t('finance.tax.paid')} value={<Amount value={s.tax_paid} />} hint={t('finance.tax.netOfReturns')} />
        <Stat label={t('finance.tax.net')} value={<Amount value={s.net_tax} />} hint={t('finance.tax.indicative')} />
        <Stat label={t('finance.tax.quick')} value={<Amount value={s.quick_sales_amount} />} hint={t('finance.tax.quickNa')} />
      </div>
      <p className="text-sm text-slate-600">{s.methodology}</p>
      <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">{s.notes.map((n) => <li key={n}>{n}</li>)}</ul>
    </div>
  )
}

function Receivables() {
  const { t } = useTranslation()
  const q = useQuery({ queryKey: ['receivables'], queryFn: getReceivables })
  if (q.isError) return <QueryError error={q.error} onRetry={() => void q.refetch()} />
  if (!q.data) return <Spinner />
  const r = q.data
  return (
    <div className="space-y-4">
      <Alert tone="info">{t('finance.ageing.khataTruth')}</Alert>
      <div className="grid grid-cols-2 gap-3"><Stat label={t('finance.receivables')} value={<Amount value={r.total_receivables} />} /><Stat label={t('finance.ageing.advances')} value={<Amount value={r.total_advances} />} /></div>
      <AgingBars title={t('finance.charts.receivablesAging')} aging={r.aging} />
      <p className="text-sm text-slate-600">{r.methodology} {t('finance.ageing.overdue')}: {r.overdue === 'Not Available' ? t('finance.notAvailable') : r.overdue}</p>
      <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
        {r.customers.filter((c) => Number(c.balance) > 0).map((c) => (
          <li key={c.customer_id} className="flex justify-between gap-3 px-4 py-2 text-sm"><span>{c.name}{c.oldest_open_days !== null ? ` · ${t('finance.ageing.oldest', { days: c.oldest_open_days })}` : ''}</span><Amount value={c.balance} /></li>
        ))}
      </ul>
    </div>
  )
}

function Payables() {
  const { t } = useTranslation()
  const q = useQuery({ queryKey: ['payables'], queryFn: getPayables })
  if (q.isError) return <QueryError error={q.error} onRetry={() => void q.refetch()} />
  if (!q.data) return <Spinner />
  const r = q.data
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3"><Stat label={t('finance.payables')} value={<Amount value={r.total_payable} />} /><Stat label={t('finance.ageing.advances')} value={<Amount value={r.total_advances} />} /></div>
      <AgingBars title={t('finance.charts.payablesAging')} aging={r.aging} />
      <p className="text-sm text-slate-600">{r.methodology}</p>
      <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
        {r.suppliers.filter((s) => Number(s.balance) > 0).map((s) => <li key={s.supplier_id} className="flex justify-between gap-3 px-4 py-2 text-sm"><span>{s.name}</span><Amount value={s.balance} /></li>)}
      </ul>
    </div>
  )
}

export function FinanceReportsPage() {
  const { t } = useTranslation()
  const [tab, setTab] = useState<Tab>('pnl')
  const [range, setRange] = useState(monthToDate())
  return (
    <div className="space-y-6">
      <PageHeader title={t('finance.reports.title')} subtitle={t('finance.reports.subtitle')} />
      <FinanceTabs />
      <div role="tablist" aria-label={t('finance.reports.title')} className="flex flex-wrap gap-2">
        {TABS.map((k) => (
          <button key={k} role="tab" type="button" aria-selected={tab === k} onClick={() => setTab(k)} className={`min-h-10 rounded-lg px-4 text-sm font-medium ${tab === k ? 'bg-slate-800 text-white' : 'bg-white ring-1 ring-slate-200'}`}>{t(`finance.reports.tabs.${k}`)}</button>
        ))}
      </div>
      {(tab === 'pnl' || tab === 'cashflow' || tab === 'tax') && <RangePicker value={range} onChange={setRange} />}
      {tab === 'pnl' && <Pnl range={range} />}
      {tab === 'cashflow' && <CashFlow range={range} />}
      {tab === 'tax' && <Tax range={range} />}
      {tab === 'receivables' && <Receivables />}
      {tab === 'payables' && <Payables />}
    </div>
  )
}
