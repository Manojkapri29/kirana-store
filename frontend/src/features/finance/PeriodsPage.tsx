import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { createPeriod, listPeriods, periodAction, reopenPeriod, type Period, type PeriodStatus } from '@/api/finance'
import { TextField } from '@/components/fields'
import { Alert, Badge, Button, EmptyState, PageHeader, QueryError, Spinner, type BadgeTone } from '@/components/ui'
import { formatDate } from '@/lib/format'

import { FinanceTabs } from './common'
import { useErrorText } from './financeUtils'

const TONE: Record<PeriodStatus, BadgeTone> = { OPEN: 'green', LOCKED: 'amber', CLOSED: 'red' }

function PeriodCard({ p }: { p: Period }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const [confirmClose, setConfirmClose] = useState(false)
  const [reason, setReason] = useState('')
  const [reopening, setReopening] = useState(false)
  const refresh = () => Promise.all(['periods', 'financeAlerts'].map((k) => queryClient.invalidateQueries({ queryKey: [k] })))
  const act = useMutation({ mutationFn: (a: 'lock' | 'close' | 'unlock') => periodAction(p.id, a), onSuccess: async () => { setConfirmClose(false); await refresh() } })
  const reopen = useMutation({ mutationFn: () => reopenPeriod(p.id, reason), onSuccess: async () => { setReopening(false); setReason(''); await refresh() } })
  const err = act.error ?? reopen.error
  return (
    <li className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-semibold">{formatDate(p.period_start)} → {formatDate(p.period_end)}</span>
        <Badge tone={TONE[p.status]}>{t(`finance.period.status.${p.status}`)}</Badge>
      </div>
      <p className="text-sm text-slate-600">{t(`finance.period.explain.${p.status}`)}</p>
      {p.reopen_approval_pending && <Alert tone="warning">{t('finance.period.reopenPending')}</Alert>}
      {err && <Alert tone="error">{errorText(err)}</Alert>}
      <div className="flex flex-wrap gap-2">
        {p.status === 'OPEN' && <Button variant="secondary" requires="FINANCE_PERIOD_LOCK" loading={act.isPending} onClick={() => act.mutate('lock')}>{t('finance.period.lock')}</Button>}
        {p.status !== 'CLOSED' && !confirmClose && <Button requires="FINANCE_PERIOD_LOCK" onClick={() => setConfirmClose(true)}>{t('finance.period.close')}</Button>}
        {p.status === 'LOCKED' && <Button variant="secondary" requires="FINANCE_PERIOD_UNLOCK" loading={act.isPending} onClick={() => act.mutate('unlock')}>{t('finance.period.unlock')}</Button>}
        {p.status === 'CLOSED' && !reopening && <Button variant="secondary" requires="FINANCE_PERIOD_UNLOCK" onClick={() => setReopening(true)}>{t('finance.period.reopen')}</Button>}
      </div>
      {confirmClose && (
        <Alert tone="warning">
          <p className="font-medium">{t('finance.period.closeConfirm')}</p>
          <div className="mt-3 flex gap-3"><Button loading={act.isPending} onClick={() => act.mutate('close')}>{t('finance.period.closeYes')}</Button><Button variant="secondary" onClick={() => setConfirmClose(false)}>{t('common.cancel')}</Button></div>
        </Alert>
      )}
      {reopening && (
        <div className="flex flex-wrap items-end gap-3">
          <TextField label={t('finance.reason')} value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button loading={reopen.isPending} disabled={!reason.trim()} onClick={() => reopen.mutate()}>{t('finance.period.reopen')}</Button>
          <Button variant="secondary" onClick={() => setReopening(false)}>{t('common.cancel')}</Button>
        </div>
      )}
    </li>
  )
}

export function PeriodsPage() {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const list = useQuery({ queryKey: ['periods'], queryFn: listPeriods })
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const add = useMutation({ mutationFn: () => createPeriod({ period_start: start, period_end: end }), onSuccess: async () => { setStart(''); setEnd(''); await queryClient.invalidateQueries({ queryKey: ['periods'] }) } })
  return (
    <div className="space-y-6">
      <PageHeader title={t('finance.period.title')} subtitle={t('finance.period.subtitle')} />
      <FinanceTabs />
      <form className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
        <TextField label={t('finance.from')} type="date" value={start} onChange={(e) => setStart(e.target.value)} required />
        <TextField label={t('finance.to')} type="date" value={end} onChange={(e) => setEnd(e.target.value)} required />
        <Button type="submit" requires="FINANCE_PERIOD_LOCK" loading={add.isPending} disabled={!start || !end}>{t('finance.period.add')}</Button>
        {add.isError && <div className="basis-full"><Alert tone="error">{errorText(add.error)}</Alert></div>}
      </form>
      {list.isError && <QueryError error={list.error} onRetry={() => void list.refetch()} />}
      {list.isPending && <Spinner />}
      {list.data && list.data.length === 0 && <EmptyState title={t('finance.period.empty')} hint={t('finance.period.emptyHint')} />}
      <ul className="space-y-3">{list.data?.map((p) => <PeriodCard key={p.id} p={p} />)}</ul>
    </div>
  )
}
