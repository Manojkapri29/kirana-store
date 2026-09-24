import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { getLedger, MANUAL_EVENTS, PAYMENT_METHODS, recordEntry, type EventType, type PaymentMethod } from '@/api/finance'
import { FilterSelect, SelectField, TextField } from '@/components/fields'
import { Alert, Badge, Button, EmptyState, PageHeader, QueryError, Spinner } from '@/components/ui'
import { formatDate, formatMoney } from '@/lib/format'

import { Amount, FinanceTabs, RangePicker } from './common'
import { isoDate, monthToDate, newKey, TH, useErrorText } from './financeUtils'

const EVENT_TYPES: readonly EventType[] = ['SALE', 'PURCHASE', 'SALE_RETURN', 'PURCHASE_RETURN', 'CUSTOMER_PAYMENT', 'SUPPLIER_PAYMENT', 'EXPENSE', 'OWNER_CAPITAL', 'OWNER_WITHDRAWAL', 'ADJUSTMENT', 'OTHER_INCOME']

function RecordEntry() {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const [type, setType] = useState<EventType>('OTHER_INCOME')
  const [amount, setAmount] = useState('')
  const [method, setMethod] = useState<PaymentMethod>('CASH')
  const [date, setDate] = useState(isoDate(new Date()))
  const [supplierId, setSupplierId] = useState('')
  const [note, setNote] = useState('')
  const key = useRef(newKey())
  const save = useMutation({
    mutationFn: () => recordEntry({ event_type: type, amount, payment_method: method, entry_date: date, supplier_id: supplierId ? Number(supplierId) : null, note: note || null }, key.current),
    onSuccess: async () => {
      key.current = newKey() // the next entry is a new attempt; a retry of THIS one reuses its key
      setAmount(''); setNote('')
      await Promise.all(['ledger', 'financeDashboard', 'cashSummary'].map((k) => queryClient.invalidateQueries({ queryKey: [k] })))
    },
  })
  return (
    <form className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
      <h2 className="text-lg font-semibold">{t('finance.ledger.recordTitle')}</h2>
      <p className="text-sm text-slate-600">{t('finance.ledger.recordHint')}</p>
      <div className="grid gap-4 md:grid-cols-3">
        <SelectField label={t('finance.ledger.type')} value={type} onChange={(e) => setType(e.target.value as EventType)}>
          {MANUAL_EVENTS.map((k) => <option key={k} value={k}>{t(`finance.events.${k}`)}</option>)}
        </SelectField>
        <TextField label={t('finance.amount')} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required />
        <SelectField label={t('finance.method')} value={method} onChange={(e) => setMethod(e.target.value as PaymentMethod)}>
          {PAYMENT_METHODS.map((m) => <option key={m} value={m}>{t(`finance.methods.${m}`)}</option>)}
        </SelectField>
        <TextField label={t('finance.expense.date')} type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
        {type === 'SUPPLIER_PAYMENT' && <TextField label={t('finance.ledger.supplierId')} type="number" min={1} value={supplierId} onChange={(e) => setSupplierId(e.target.value)} required />}
        <TextField label={t('finance.ledger.note')} value={note} onChange={(e) => setNote(e.target.value)} optional />
      </div>
      {save.isError && <Alert tone="error">{errorText(save.error)}</Alert>}
      <Button type="submit" requires="FINANCE_MANAGE" loading={save.isPending} disabled={!amount || (type === 'SUPPLIER_PAYMENT' && !supplierId)}>{t('finance.ledger.record')}</Button>
    </form>
  )
}

export function LedgerPage() {
  const { t } = useTranslation()
  const [range, setRange] = useState(monthToDate())
  const [type, setType] = useState<EventType | ''>('')
  const [method, setMethod] = useState('')
  const ledger = useQuery({ queryKey: ['ledger', range, type, method], queryFn: () => getLedger({ ...range, event_type: type, payment_method: method || undefined, limit: 200 }) })
  return (
    <div className="space-y-6">
      <PageHeader title={t('finance.ledger.title')} subtitle={t('finance.ledger.subtitle')} />
      <FinanceTabs />
      <RangePicker value={range} onChange={setRange} />
      <div className="flex flex-col gap-3 sm:flex-row">
        <FilterSelect aria-label={t('finance.ledger.type')} value={type} onChange={(e) => setType(e.target.value as EventType | '')}>
          <option value="">{t('finance.ledger.allTypes')}</option>
          {EVENT_TYPES.map((k) => <option key={k} value={k}>{t(`finance.events.${k}`)}</option>)}
        </FilterSelect>
        <FilterSelect aria-label={t('finance.method')} value={method} onChange={(e) => setMethod(e.target.value)}>
          <option value="">{t('finance.ledger.allMethods')}</option>
          {PAYMENT_METHODS.map((m) => <option key={m} value={m}>{t(`finance.methods.${m}`)}</option>)}
        </FilterSelect>
      </div>
      {ledger.isError && <QueryError error={ledger.error} onRetry={() => void ledger.refetch()} />}
      {ledger.isPending && <Spinner />}
      {ledger.data && (
        <>
          <p className="text-sm text-slate-700">{t('finance.ledger.totals', { in: formatMoney(ledger.data.total_in), out: formatMoney(ledger.data.total_out) })}</p>
          {ledger.data.items.length === 0 ? (
            <EmptyState title={t('finance.ledger.empty')} />
          ) : (
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
              <table className="min-w-full divide-y divide-slate-200">
                <thead className="bg-slate-50"><tr>
                  <th className={TH}>{t('finance.ledger.date')}</th><th className={TH}>{t('finance.ledger.type')}</th><th className={TH}>{t('finance.ledger.source')}</th>
                  <th className={`${TH} text-right`}>{t('finance.amount')}</th><th className={`${TH} text-right`}>{t('finance.ledger.settled')}</th><th className={TH}>{t('finance.method')}</th><th className={TH}>{t('finance.ledger.status')}</th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {ledger.data.items.map((r) => (
                    <tr key={r.key}>
                      <td className="whitespace-nowrap px-4 py-2 text-sm">{formatDate(r.entry_date)}</td>
                      <td className="px-4 py-2 text-sm">{t(`finance.events.${r.event_type}`)}</td>
                      <td className="px-4 py-2 text-sm">{r.reference ?? `${r.source_type} #${r.source_id}`}</td>
                      <td className="px-4 py-2 text-right text-sm"><Amount value={r.amount} /></td>
                      <td className={`px-4 py-2 text-right text-sm font-medium ${r.direction === 'OUT' ? 'text-red-700' : 'text-emerald-800'}`}>{r.direction === 'OUT' ? '−' : '+'}<Amount value={r.settled_amount} /></td>
                      <td className="px-4 py-2 text-sm">{r.payment_method === 'NOT_RECORDED' ? t('finance.ledger.notRecorded') : t(`finance.methods.${r.payment_method}`, { defaultValue: r.payment_method })}</td>
                      <td className="px-4 py-2 text-sm">{r.status === 'POSTED' ? <Badge tone="green">{t('finance.ledger.posted')}</Badge> : <Badge tone="amber">{t(`finance.ledger.st.${r.status}`, { defaultValue: r.status })}</Badge>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      <RecordEntry />
    </div>
  )
}
