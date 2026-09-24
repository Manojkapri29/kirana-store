import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { getReconciliation, markReconciliation, type ReconItem, type ReconStatus } from '@/api/finance'
import { TextField } from '@/components/fields'
import { Alert, Badge, Button, EmptyState, PageHeader, QueryError, Spinner, type BadgeTone } from '@/components/ui'
import { formatDate } from '@/lib/format'

import { Amount, FinanceTabs, RangePicker, Stat } from './common'
import { monthToDate, useErrorText } from './financeUtils'

const TONE: Record<ReconStatus, BadgeTone> = { MATCHED: 'green', UNMATCHED: 'slate', PARTIAL: 'amber', REVIEW_REQUIRED: 'red' }
const STATUSES: readonly ReconStatus[] = ['MATCHED', 'UNMATCHED', 'PARTIAL', 'REVIEW_REQUIRED']

function ItemRow({ item }: { item: ReconItem }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<ReconStatus>('MATCHED')
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [open, setOpen] = useState(false)
  const mark = useMutation({
    mutationFn: () => markReconciliation({ source_type: item.row.source_type, source_id: item.row.source_id, status, confirmed_amount: status === 'PARTIAL' ? amount : null, note: note || null }),
    onSuccess: async () => { setOpen(false); await queryClient.invalidateQueries({ queryKey: ['reconciliation'] }) },
  })
  return (
    <li className="space-y-2 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium">{item.row.reference ?? `${item.row.source_type} #${item.row.source_id}`}</span>
        <span className="text-sm text-slate-600">{formatDate(item.row.entry_date)} · {t(`finance.methods.${item.row.payment_method}`, { defaultValue: item.row.payment_method })}</span>
        <Badge tone={TONE[item.status]}>{t(`finance.recon.status.${item.status}`)}</Badge>
        <span className="ml-auto font-semibold"><Amount value={item.row.settled_amount} /></span>
      </div>
      {item.confirmed_amount && <p className="text-sm text-slate-600">{t('finance.recon.confirmed')} <Amount value={item.confirmed_amount} />{item.note ? ` · ${item.note}` : ''}</p>}
      {!open && <Button variant="secondary" requires="FINANCE_RECONCILIATION_MANAGE" onClick={() => setOpen(true)}>{t('finance.recon.review')}</Button>}
      {open && (
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm font-medium">{t('finance.recon.markAs')}
            <select className="min-h-12 rounded-lg border px-3" value={status} onChange={(e) => setStatus(e.target.value as ReconStatus)}>{STATUSES.map((s) => <option key={s} value={s}>{t(`finance.recon.status.${s}`)}</option>)}</select>
          </label>
          {status === 'PARTIAL' && <TextField label={t('finance.recon.confirmedAmount')} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />}
          <TextField label={t('finance.ledger.note')} value={note} onChange={(e) => setNote(e.target.value)} optional />
          <Button loading={mark.isPending} onClick={() => mark.mutate()}>{t('common.save')}</Button>
          <Button variant="secondary" onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
          {mark.isError && <div className="basis-full"><Alert tone="error">{errorText(mark.error)}</Alert></div>}
        </div>
      )}
    </li>
  )
}

export function ReconciliationPage() {
  const { t } = useTranslation()
  const [range, setRange] = useState(monthToDate())
  const q = useQuery({ queryKey: ['reconciliation', range], queryFn: () => getReconciliation(range) })
  return (
    <div className="space-y-6">
      <PageHeader title={t('finance.recon.title')} subtitle={t('finance.recon.subtitle')} />
      <FinanceTabs />
      <Alert tone="warning">{t('finance.recon.noBank')}</Alert>
      <RangePicker value={range} onChange={setRange} />
      {q.isError && <QueryError error={q.error} onRetry={() => void q.refetch()} />}
      {q.isPending && <Spinner />}
      {q.data && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {STATUSES.map((s) => <Stat key={s} label={t(`finance.recon.status.${s}`)} value={q.data.counts[s]} hint={<Amount value={q.data.amounts[s]} />} />)}
          </div>
          {q.data.items.length === 0 && <EmptyState title={t('finance.recon.empty')} hint={t('finance.recon.emptyHint')} />}
          <ul className="space-y-3">{q.data.items.map((i) => <ItemRow key={`${i.row.source_type}-${i.row.source_id}`} item={i} />)}</ul>
        </>
      )}
    </div>
  )
}
