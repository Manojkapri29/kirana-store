import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  ADVANCED_KINDS, createAdvancedSchedule, listRuns, listSavedReports, listSchedules, runScheduleNow, setScheduleActive,
  type AdvancedKind, type Schedule, type ScheduledReport,
} from '@/api/analytics'
import { SelectField, TextField } from '@/components/fields'
import { Alert, Badge, Button, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useErrorText } from '@/features/finance/financeUtils'
import { formatDateTime } from '@/lib/format'

import { AnalyticsTabs } from './common'

function Runs({ id }: { id: number }) {
  const { t } = useTranslation()
  const q = useQuery({ queryKey: ['analytics', 'runs', id], queryFn: () => listRuns(id) })
  if (!q.data) return <Spinner />
  if (q.data.length === 0) return <p className="text-sm text-slate-500">{t('analytics.schedules.noRuns')}</p>
  return (
    <ul className="space-y-1 text-sm">
      {q.data.map((r) => (
        <li key={r.id} className="flex flex-wrap items-center gap-2">
          <span>{r.period_start} → {r.period_end}</span>
          <Badge tone={r.status === 'OK' ? 'green' : 'red'}>{r.status}</Badge>
          <span className="text-slate-600">{t(`analytics.schedules.delivery.${r.delivery_status}`)}</span>
          {r.error && <span className="text-red-700">{r.error}</span>}
        </li>
      ))}
    </ul>
  )
}

export function SchedulesPage() {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const client = useQueryClient()
  const list = useQuery({ queryKey: ['analytics', 'schedules'], queryFn: listSchedules })
  const savedList = useQuery({ queryKey: ['analytics', 'saved', false], queryFn: () => listSavedReports(false) })
  const [kind, setKind] = useState<AdvancedKind>('adv_kpis')
  const [schedule, setSchedule] = useState<Schedule>('WEEKLY')
  const [saved, setSaved] = useState('')
  const [format, setFormat] = useState('')
  const [email, setEmail] = useState(false)
  const [recipients, setRecipients] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const refresh = () => void client.invalidateQueries({ queryKey: ['analytics'] })
  const create = useMutation({
    mutationFn: () => createAdvancedSchedule({
      kind, schedule, saved_report_id: kind === 'saved_report' ? Number(saved) || null : null, export_format: format || null,
      delivery_channel: email ? 'EMAIL' : null, recipients: email ? recipients.split(',').map((r) => r.trim()).filter(Boolean) : [],
    }),
    onSuccess: refresh,
  })
  const run = useMutation({ mutationFn: (id: number) => runScheduleNow(id), onSuccess: refresh })
  const pause = useMutation({ mutationFn: (r: ScheduledReport) => setScheduleActive(r.id, !r.is_active), onSuccess: refresh })
  const advanced = list.data?.items.filter((r) => r.report_type.startsWith('adv_') || r.report_type === 'saved_report') ?? []
  return (
    <div className="space-y-6">
      <PageHeader title={t('analytics.schedules.title')} subtitle={t('analytics.schedules.subtitle')} />
      <AnalyticsTabs />
      <Alert tone="info">{t('analytics.schedules.deliveryNote')}</Alert>
      <form className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" onSubmit={(e) => { e.preventDefault(); create.mutate() }}>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <SelectField label={t('analytics.schedules.report')} value={kind} onChange={(e) => setKind(e.target.value as AdvancedKind)}>
            {ADVANCED_KINDS.map((k) => <option key={k} value={k}>{t(`analytics.schedules.kinds.${k}`)}</option>)}
          </SelectField>
          <SelectField label={t('analytics.schedules.frequency')} value={schedule} onChange={(e) => setSchedule(e.target.value as Schedule)}>
            {(['DAILY', 'WEEKLY', 'MONTHLY'] as const).map((s) => <option key={s} value={s}>{t(`analytics.schedules.frequencies.${s}`)}</option>)}
          </SelectField>
          <SelectField label={t('analytics.schedules.format')} optional value={format} onChange={(e) => setFormat(e.target.value)}>
            <option value="">{t('analytics.schedules.noFormat')}</option>
            {['CSV', 'XLSX', 'PDF'].map((f) => <option key={f} value={f}>{f}</option>)}
          </SelectField>
        </div>
        {kind === 'saved_report' && (
          <SelectField label={t('analytics.schedules.savedReport')} value={saved} onChange={(e) => setSaved(e.target.value)}>
            <option value="">—</option>
            {savedList.data?.items.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </SelectField>
        )}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={email} onChange={(e) => setEmail(e.target.checked)} />{t('analytics.schedules.email')}</label>
        {email && <TextField label={t('analytics.schedules.recipients')} hint={t('analytics.schedules.recipientsHint')} value={recipients} onChange={(e) => setRecipients(e.target.value)} />}
        <Button type="submit" loading={create.isPending}>{t('analytics.schedules.create')}</Button>
        {create.isError && <Alert tone="error">{errorText(create.error)}</Alert>}
      </form>
      {list.isError && <QueryError error={list.error} onRetry={() => void list.refetch()} />}
      {!list.data && !list.isError && <Spinner />}
      {list.data && advanced.length === 0 && <p className="text-sm text-slate-500">{t('analytics.schedules.empty')}</p>}
      <ul className="space-y-3">
        {advanced.map((r) => (
          <li key={r.id} className="space-y-2 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold text-slate-900">{t(`analytics.schedules.kinds.${r.report_type}`, { defaultValue: r.report_type })} · {t(`analytics.schedules.frequencies.${r.schedule}`)}</h2>
                <p className="text-xs text-slate-500">{t('analytics.schedules.next')}: {formatDateTime(r.next_run_at)}{r.delivery_channel ? ` · ${t('analytics.schedules.emailTo', { to: (r.recipients ?? []).join(', ') })}` : ''}</p>
              </div>
              <div className="flex gap-2">
                <Badge tone={r.is_active ? 'green' : 'slate'}>{r.is_active ? t('analytics.schedules.active') : t('analytics.schedules.paused')}</Badge>
                <Button variant="secondary" loading={run.isPending} onClick={() => run.mutate(r.id)}>{t('analytics.schedules.runNow')}</Button>
                <Button variant="secondary" onClick={() => pause.mutate(r)}>{r.is_active ? t('analytics.schedules.pause') : t('analytics.schedules.resume')}</Button>
                <Button variant="secondary" onClick={() => setOpen(open === r.id ? null : r.id)}>{t('analytics.schedules.history')}</Button>
              </div>
            </div>
            {r.last_error && <Alert tone="warning">{r.last_error}</Alert>}
            {open === r.id && <Runs id={r.id} />}
          </li>
        ))}
      </ul>
      {(run.isError || pause.isError) && <Alert tone="error">{errorText(run.error ?? pause.error)}</Alert>}
    </div>
  )
}
