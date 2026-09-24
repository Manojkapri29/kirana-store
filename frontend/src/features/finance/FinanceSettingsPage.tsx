import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { createTaxRate, getFinanceSettings, getTaxSettings, listTaxRates, saveFinanceSettings, saveTaxSettings, setTaxRateActive, type FinanceSettings, type TaxSettings } from '@/api/finance'
import { SelectField, TextField } from '@/components/fields'
import { Alert, Badge, Button, PageHeader, QueryError, Spinner } from '@/components/ui'

import { FinanceTabs } from './common'
import { useErrorText } from './financeUtils'

const blankToNull = (v: string): string | null => (v.trim() === '' ? null : v.trim())

function LimitsForm({ s }: { s: FinanceSettings }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const [expense, setExpense] = useState(s.expense_approval_threshold ?? '')
  const [adjustment, setAdjustment] = useState(s.adjustment_approval_threshold ?? '')
  const [cash, setCash] = useState(s.cash_adjustment_threshold ?? '')
  const [reopen, setReopen] = useState(s.period_reopen_requires_approval)
  const [overdue, setOverdue] = useState(String(s.overdue_after_days))
  const [spike, setSpike] = useState(String(s.expense_spike_pct))
  const [margin, setMargin] = useState(String(s.margin_drop_points))
  const [variance, setVariance] = useState(s.cash_variance_alert_amount)
  const save = useMutation({
    mutationFn: () => saveFinanceSettings({
      expense_approval_threshold: blankToNull(expense), adjustment_approval_threshold: blankToNull(adjustment), cash_adjustment_threshold: blankToNull(cash),
      period_reopen_requires_approval: reopen, overdue_after_days: Number(overdue), expense_spike_pct: Number(spike), margin_drop_points: Number(margin), cash_variance_alert_amount: variance || '0',
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['financeSettings'] }),
  })
  return (
    <form className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
      <h2 className="text-lg font-semibold">{t('finance.settings.limitsTitle')}</h2>
      <p className="text-sm text-slate-600">{t('finance.settings.limitsHint')}</p>
      <div className="grid gap-4 md:grid-cols-3">
        <TextField label={t('finance.settings.expenseLimit')} inputMode="decimal" value={expense} onChange={(e) => setExpense(e.target.value)} optional hint={t('finance.settings.blankOff')} />
        <TextField label={t('finance.settings.adjustmentLimit')} inputMode="decimal" value={adjustment} onChange={(e) => setAdjustment(e.target.value)} optional hint={t('finance.settings.blankOff')} />
        <TextField label={t('finance.settings.cashLimit')} inputMode="decimal" value={cash} onChange={(e) => setCash(e.target.value)} optional hint={t('finance.settings.blankOff')} />
      </div>
      <label className="flex min-h-12 items-center gap-3"><input type="checkbox" className="size-5" checked={reopen} onChange={(e) => setReopen(e.target.checked)} /><span className="font-medium">{t('finance.settings.reopenApproval')}</span></label>
      <h3 className="font-semibold">{t('finance.settings.alertsTitle')}</h3>
      <div className="grid gap-4 md:grid-cols-4">
        <TextField label={t('finance.settings.overdueDays')} type="number" min={1} value={overdue} onChange={(e) => setOverdue(e.target.value)} />
        <TextField label={t('finance.settings.spikePct')} type="number" min={0} value={spike} onChange={(e) => setSpike(e.target.value)} />
        <TextField label={t('finance.settings.marginPoints')} type="number" min={0} value={margin} onChange={(e) => setMargin(e.target.value)} />
        <TextField label={t('finance.settings.cashVariance')} inputMode="decimal" value={variance} onChange={(e) => setVariance(e.target.value)} />
      </div>
      {save.isError && <Alert tone="error">{errorText(save.error)}</Alert>}
      {save.isSuccess && <Alert tone="success">{t('finance.saved')}</Alert>}
      <Button type="submit" requires="FINANCE_MANAGE" loading={save.isPending}>{t('common.save')}</Button>
    </form>
  )
}

function TaxForm({ s }: { s: TaxSettings | null }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const rates = useQuery({ queryKey: ['taxRates'], queryFn: listTaxRates })
  const [type, setType] = useState<TaxSettings['tax_type']>(s?.tax_type ?? 'GST')
  const [reg, setReg] = useState(s?.registration_number ?? '')
  const [state, setState] = useState(s?.location_state ?? '')
  const [inclusive, setInclusive] = useState(s?.prices_include_tax ?? true)
  const [name, setName] = useState('')
  const [rate, setRate] = useState('')
  const save = useMutation({
    mutationFn: () => saveTaxSettings({ tax_type: type, registration_number: reg || null, location_state: state || null, prices_include_tax: inclusive }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['taxSettings'] }),
  })
  const addRate = useMutation({ mutationFn: () => createTaxRate({ name, rate_percent: rate }), onSuccess: async () => { setName(''); setRate(''); await queryClient.invalidateQueries({ queryKey: ['taxRates'] }) } })
  const toggle = useMutation({ mutationFn: (r: { id: number; on: boolean }) => setTaxRateActive(r.id, r.on), onSuccess: () => queryClient.invalidateQueries({ queryKey: ['taxRates'] }) })
  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="text-lg font-semibold">{t('finance.settings.taxTitle')}</h2>
      <Alert tone="info">{t('finance.settings.taxHint')}</Alert>
      {s === null && <Alert tone="warning">{t('finance.tax.notConfigured')}</Alert>}
      <form className="grid gap-4 md:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
        <SelectField label={t('finance.settings.taxType')} value={type} onChange={(e) => setType(e.target.value as TaxSettings['tax_type'])}>
          {(['GST', 'VAT', 'SALES_TAX', 'OTHER'] as const).map((k) => <option key={k} value={k}>{k}</option>)}
        </SelectField>
        <TextField label={t('finance.settings.registration')} value={reg} onChange={(e) => setReg(e.target.value)} optional />
        <TextField label={t('finance.settings.location')} value={state} onChange={(e) => setState(e.target.value)} optional />
        <label className="flex min-h-12 items-center gap-3"><input type="checkbox" className="size-5" checked={inclusive} onChange={(e) => setInclusive(e.target.checked)} /><span>{t('finance.settings.inclusive')}</span></label>
        <div className="md:col-span-2"><Button type="submit" requires="FINANCE_MANAGE" loading={save.isPending}>{t('common.save')}</Button></div>
      </form>
      <h3 className="font-semibold">{t('finance.settings.rates')}</h3>
      <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); addRate.mutate() }}>
        <TextField label={t('finance.settings.rateName')} value={name} onChange={(e) => setName(e.target.value)} />
        <TextField label={t('finance.settings.ratePercent')} inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} hint={t('finance.settings.rateHint')} />
        <Button type="submit" requires="FINANCE_MANAGE" loading={addRate.isPending} disabled={!name.trim() || !rate}>{t('finance.settings.addRate')}</Button>
      </form>
      {(save.isError || addRate.isError || toggle.isError) && <Alert tone="error">{errorText(save.error ?? addRate.error ?? toggle.error)}</Alert>}
      <ul className="flex flex-wrap gap-2">
        {rates.data?.map((r) => (
          <li key={r.id} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-1 text-sm">
            <span>{r.name} · {r.rate_percent}%</span>{!r.is_active && <Badge tone="slate">{t('finance.settings.off')}</Badge>}
            <Button variant="secondary" requires="FINANCE_MANAGE" onClick={() => toggle.mutate({ id: r.id, on: !r.is_active })}>{r.is_active ? t('finance.expense.deactivate') : t('finance.expense.activate')}</Button>
          </li>
        ))}
      </ul>
    </section>
  )
}

export function FinanceSettingsPage() {
  const { t } = useTranslation()
  const settings = useQuery({ queryKey: ['financeSettings'], queryFn: getFinanceSettings })
  const tax = useQuery({ queryKey: ['taxSettings'], queryFn: getTaxSettings })
  return (
    <div className="space-y-6">
      <PageHeader title={t('finance.settings.title')} subtitle={t('finance.settings.subtitle')} />
      <FinanceTabs />
      {settings.isError && <QueryError error={settings.error} onRetry={() => void settings.refetch()} />}
      {settings.isPending && <Spinner />}
      {settings.data && <LimitsForm key={JSON.stringify(settings.data)} s={settings.data} />}
      {tax.isSuccess && <TaxForm key={JSON.stringify(tax.data)} s={tax.data} />}
    </div>
  )
}
