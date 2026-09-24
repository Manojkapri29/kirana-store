import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { getCashSummary, listCashCounts, PAYMENT_METHODS, recordAdjustment, recordCashCount } from '@/api/finance'
import { TextField } from '@/components/fields'
import { Alert, Button, PageHeader, QueryError, Spinner } from '@/components/ui'
import { formatDate } from '@/lib/format'

import { Amount, FinanceTabs, Stat } from './common'
import { isoDate, TH, useErrorText } from './financeUtils'

const LINE_ORDER = ['cash_sales', 'customer_payments', 'other_cash_income', 'owner_capital', 'supplier_refunds', 'cash_purchases', 'expenses', 'supplier_payments', 'refunds', 'owner_withdrawals', 'adjustments']

export function CashPage() {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const [day, setDay] = useState(isoDate(new Date()))
  const summary = useQuery({ queryKey: ['cashSummary', day], queryFn: () => getCashSummary(day) })
  const counts = useQuery({ queryKey: ['cashCounts'], queryFn: listCashCounts })
  const [actual, setActual] = useState('')
  const [reason, setReason] = useState('')
  const [adjAmount, setAdjAmount] = useState('')
  const [adjDir, setAdjDir] = useState<'IN' | 'OUT'>('IN')
  const [adjNote, setAdjNote] = useState('')
  const [pendingId, setPendingId] = useState<number | null>(null)
  const refresh = () => Promise.all(['cashSummary', 'cashCounts', 'financeDashboard'].map((k) => queryClient.invalidateQueries({ queryKey: [k] })))
  const count = useMutation({
    mutationFn: () => recordCashCount({ count_date: day, actual_cash: actual, reason: reason || null }),
    onSuccess: async () => { setActual(''); setReason(''); await refresh() },
  })
  const adjust = useMutation({
    mutationFn: () => recordAdjustment({ direction: adjDir, amount: adjAmount, payment_method: PAYMENT_METHODS[0], entry_date: day, note: adjNote }),
    onSuccess: async (r) => {
      if ('status' in r) { setPendingId(r.approval_request_id); return }
      setPendingId(null); setAdjAmount(''); setAdjNote(''); await refresh()
    },
  })
  const s = summary.data
  return (
    <div className="space-y-6">
      <PageHeader title={t('finance.cash.title')} subtitle={t('finance.cash.subtitle')} />
      <FinanceTabs />
      <div className="sm:max-w-xs"><TextField label={t('finance.cash.day')} type="date" value={day} max={isoDate(new Date())} onChange={(e) => setDay(e.target.value)} /></div>
      {summary.isError && <QueryError error={summary.error} onRetry={() => void summary.refetch()} />}
      {summary.isPending && <Spinner />}
      {s && (
        <>
          {s.opening_cash === null && <Alert tone="warning">{s.opening_basis}</Alert>}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
            <Stat label={t('finance.cash.opening')} value={<Amount value={s.opening_cash} />} hint={s.opening_cash !== null ? s.opening_basis : undefined} />
            <Stat label={t('finance.cash.net')} value={<Amount value={s.net_movement} />} />
            <Stat label={t('finance.cash.expected')} value={<Amount value={s.expected_closing} />} />
          </div>
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50"><tr><th className={TH}>{t('finance.cash.line')}</th><th className={`${TH} text-right`}>{t('finance.amount')}</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {LINE_ORDER.map((k) => (
                  <tr key={k}><td className="px-4 py-2 text-sm">{t(`finance.cash.lines.${k}`, { defaultValue: k })}</td><td className="px-4 py-2 text-right text-sm font-medium"><Amount value={s.lines[k]} /></td></tr>
                ))}
              </tbody>
            </table>
          </div>
          {s.notes.map((n) => <Alert key={n} tone="info">{n}</Alert>)}
        </>
      )}

      <form className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" onSubmit={(e) => { e.preventDefault(); count.mutate() }}>
        <h2 className="text-lg font-semibold">{t('finance.cash.countTitle')}</h2>
        <p className="text-sm text-slate-600">{t('finance.cash.countHint')}</p>
        <div className="grid gap-4 md:grid-cols-2">
          <TextField label={t('finance.cash.actual')} inputMode="decimal" value={actual} onChange={(e) => setActual(e.target.value)} required />
          <TextField label={t('finance.reason')} value={reason} onChange={(e) => setReason(e.target.value)} optional hint={t('finance.cash.reasonHint')} />
        </div>
        {count.isError && <Alert tone="error">{errorText(count.error)}</Alert>}
        <Button type="submit" requires="FINANCE_CASH_MANAGE" loading={count.isPending} disabled={!actual}>{t('finance.cash.record')}</Button>
      </form>

      <form className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" onSubmit={(e) => { e.preventDefault(); adjust.mutate() }}>
        <h2 className="text-lg font-semibold">{t('finance.cash.adjustTitle')}</h2>
        <p className="text-sm text-slate-600">{t('finance.cash.adjustHint')}</p>
        <div className="grid gap-4 md:grid-cols-3">
          <label className="flex flex-col gap-1 text-sm font-medium">{t('finance.cash.direction')}
            <select className="min-h-12 rounded-lg border px-3" value={adjDir} onChange={(e) => setAdjDir(e.target.value as 'IN' | 'OUT')}>
              <option value="IN">{t('finance.cash.moneyIn')}</option><option value="OUT">{t('finance.cash.moneyOut')}</option>
            </select>
          </label>
          <TextField label={t('finance.amount')} inputMode="decimal" value={adjAmount} onChange={(e) => setAdjAmount(e.target.value)} required />
          <TextField label={t('finance.reason')} value={adjNote} onChange={(e) => setAdjNote(e.target.value)} required />
        </div>
        {pendingId !== null && <Alert tone="warning">{t('finance.cash.needsApproval', { id: pendingId })}</Alert>}
        {adjust.isError && <Alert tone="error">{errorText(adjust.error)}</Alert>}
        <Button type="submit" requires="FINANCE_ADJUSTMENT_MANAGE" loading={adjust.isPending} disabled={!adjAmount || !adjNote.trim()}>{t('finance.cash.adjust')}</Button>
      </form>

      <section aria-labelledby="counts" className="space-y-2">
        <h2 id="counts" className="text-lg font-semibold">{t('finance.cash.history')}</h2>
        {counts.data && counts.data.length === 0 && <p className="text-slate-600">{t('finance.cash.noCounts')}</p>}
        <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
          {counts.data?.map((c) => (
            <li key={c.id} className="flex flex-wrap justify-between gap-3 px-4 py-2 text-sm">
              <span>{formatDate(c.count_date)}{c.reason ? ` · ${c.reason}` : ''}</span>
              <span>{t('finance.cash.countLine')} <Amount value={c.actual_cash} /> · {t('finance.cash.expectedShort')} <Amount value={c.expected_cash} /> · {t('finance.cash.difference')} <Amount value={c.difference} /></span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
