import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import {
  addNote, adjustLoyalty, CHANNELS, getCrmProfile, getLoyaltyLedger, getTimeline, listNotes, updateClassification,
  type Channel, type ClassificationChanges, type CrmProfile,
} from '@/api/crm'
import { SelectField, TextAreaField, TextField } from '@/components/fields'
import { Alert, Button, LinkButton, QueryError, Spinner } from '@/components/ui'
import { formatDate, formatDateTime, formatMoney } from '@/lib/format'

import { Stat } from './common'
import { useErrorText } from './crmUtils'

const CONSENT = [
  ['marketing_opt_in_email', 'EMAIL'], ['marketing_opt_in_sms', 'SMS'], ['marketing_opt_in_whatsapp', 'WHATSAPP'], ['marketing_opt_in_push', 'PUSH'],
] as const

function Preferences({ profile }: { profile: CrmProfile }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const save = useMutation({
    mutationFn: (changes: ClassificationChanges) => updateClassification(profile.customer_id, changes),
    onSuccess: async () => {
      await Promise.all(['crmProfile', 'crmTimeline'].map((k) => queryClient.invalidateQueries({ queryKey: [k] })))
    },
  })
  return (
    <section aria-labelledby="prefs" className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h2 id="prefs" className="text-lg font-semibold">{t('crm.customer.preferences')}</h2>
      <p className="text-sm text-slate-600">{t('crm.customer.consentNote')}</p>
      <fieldset className="grid gap-2 sm:grid-cols-2">
        <legend className="mb-1 text-sm font-medium">{t('crm.customer.marketingConsent')}</legend>
        {CONSENT.map(([field, channel]) => (
          <label key={field} className="flex min-h-12 items-center gap-3 rounded-lg border border-slate-200 px-3">
            <input
              type="checkbox" className="size-5" checked={profile[field]} disabled={save.isPending}
              onChange={(e) => save.mutate({ [field]: e.target.checked })}
            />
            <span>{t(`crm.channels.${channel}`)}</span>
          </label>
        ))}
      </fieldset>
      <div className="grid gap-4 md:grid-cols-2">
        <SelectField label={t('crm.customer.preferredChannel')} value={profile.preferred_contact_channel ?? ''} onChange={(e) => save.mutate({ preferred_contact_channel: (e.target.value || null) as Channel | null })}>
          <option value="">{t('crm.customer.noPreference')}</option>
          {CHANNELS.map((c) => <option key={c} value={c}>{t(`crm.channels.${c}`)}</option>)}
        </SelectField>
        <SelectField label={t('crm.customer.type')} value={profile.customer_type ?? ''} onChange={(e) => save.mutate({ customer_type: (e.target.value || null) as CrmProfile['customer_type'] })}>
          <option value="">{t('crm.customer.notSet')}</option>
          {(['RETAIL', 'WHOLESALE', 'OTHER'] as const).map((k) => <option key={k} value={k}>{t(`crm.customer.types.${k}`)}</option>)}
        </SelectField>
      </div>
      {save.isError && <Alert tone="error">{errorText(save.error)}</Alert>}
    </section>
  )
}

function Loyalty({ profile }: { profile: CrmProfile }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const id = profile.customer_id
  const ledger = useQuery({ queryKey: ['loyaltyLedger', id], queryFn: () => getLoyaltyLedger(id) })
  const [points, setPoints] = useState('')
  const [note, setNote] = useState('')
  const [approvalId, setApprovalId] = useState('')
  const [pendingId, setPendingId] = useState<number | null>(null)
  const adjust = useMutation({
    mutationFn: () => adjustLoyalty(id, { points_delta: Number(points), note, approval_request_id: approvalId ? Number(approvalId) : null }),
    onSuccess: async (result) => {
      if ('status' in result) {
        setPendingId(result.approval_request_id)
        return
      }
      setPendingId(null)
      setPoints(''); setNote(''); setApprovalId('')
      await Promise.all(['loyaltyLedger', 'crmProfile', 'crmTimeline'].map((k) => queryClient.invalidateQueries({ queryKey: [k] })))
    },
  })
  return (
    <section aria-labelledby="loyalty" className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h2 id="loyalty" className="text-lg font-semibold">{t('crm.customer.loyalty')}</h2>
      {!profile.loyalty_program_active && <Alert tone="warning">{t('crm.loyalty.notConfigured')}</Alert>}
      <p className="text-slate-700">{t('crm.customer.balance', { points: ledger.data?.balance ?? profile.loyalty_balance })}</p>
      <form className="grid gap-4 md:grid-cols-3" onSubmit={(e) => { e.preventDefault(); adjust.mutate() }}>
        <TextField label={t('crm.customer.pointsDelta')} type="number" value={points} onChange={(e) => setPoints(e.target.value)} required hint={t('crm.customer.pointsDeltaHint')} />
        <TextField label={t('crm.customer.reason')} value={note} onChange={(e) => setNote(e.target.value)} required maxLength={300} />
        <TextField label={t('crm.customer.approvalId')} type="number" value={approvalId} onChange={(e) => setApprovalId(e.target.value)} optional hint={t('crm.customer.approvalIdHint')} />
        <div className="md:col-span-3">
          <Button type="submit" requires="LOYALTY_MANAGE" loading={adjust.isPending} disabled={!points || Number(points) === 0 || !note.trim()}>{t('crm.customer.adjust')}</Button>
        </div>
      </form>
      {pendingId !== null && <Alert tone="warning">{t('crm.customer.needsApproval', { id: pendingId })}</Alert>}
      {adjust.isError && <Alert tone="error">{errorText(adjust.error)}</Alert>}
      {ledger.data && ledger.data.items.length > 0 && (
        <ul className="divide-y divide-slate-100">
          {ledger.data.items.map((e) => (
            <li key={e.id} className="flex justify-between gap-4 py-2 text-sm">
              <span>{formatDate(e.entry_date)} · {t(`crm.customer.entry.${e.entry_type}`)}{e.note ? ` · ${e.note}` : ''}</span>
              <span className={`font-medium ${e.points_delta < 0 ? 'text-red-700' : 'text-emerald-800'}`}>{e.points_delta > 0 ? '+' : ''}{e.points_delta}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Notes({ id }: { id: number }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const notes = useQuery({ queryKey: ['crmNotes', id], queryFn: () => listNotes(id) })
  const [body, setBody] = useState('')
  const add = useMutation({
    mutationFn: () => addNote(id, body),
    onSuccess: async () => { setBody(''); await Promise.all(['crmNotes', 'crmTimeline'].map((k) => queryClient.invalidateQueries({ queryKey: [k] }))) },
  })
  return (
    <section aria-labelledby="notes" className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h2 id="notes" className="text-lg font-semibold">{t('crm.customer.notes')}</h2>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
        <TextAreaField label={t('crm.customer.newNote')} value={body} onChange={(e) => setBody(e.target.value)} rows={2} />
        {add.isError && <Alert tone="error">{errorText(add.error)}</Alert>}
        <Button type="submit" requires="CRM_MANAGE" loading={add.isPending} disabled={!body.trim()}>{t('crm.customer.addNote')}</Button>
      </form>
      {notes.data && notes.data.length === 0 && <p className="text-slate-600">{t('crm.customer.noNotes')}</p>}
      <ul className="space-y-2">
        {notes.data?.map((n) => (
          <li key={n.id} className="rounded-lg bg-slate-50 p-3 text-sm"><div className="whitespace-pre-wrap">{n.body}</div><div className="mt-1 text-xs text-slate-500">{formatDateTime(n.created_at)}</div></li>
        ))}
      </ul>
    </section>
  )
}

function Timeline({ id }: { id: number }) {
  const { t } = useTranslation()
  const timeline = useQuery({ queryKey: ['crmTimeline', id], queryFn: () => getTimeline(id) })
  return (
    <section aria-labelledby="timeline" className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h2 id="timeline" className="text-lg font-semibold">{t('crm.customer.timeline')}</h2>
      {timeline.isPending && <Spinner />}
      {timeline.data && timeline.data.length === 0 && <p className="text-slate-600">{t('crm.customer.noTimeline')}</p>}
      <ul className="divide-y divide-slate-100">
        {timeline.data?.map((e, i) => (
          <li key={`${e.kind}-${e.reference_id ?? i}-${e.occurred_at}`} className="flex justify-between gap-4 py-2 text-sm">
            <span><span className="font-medium">{e.title}</span> <span className="text-slate-600">{e.detail}</span><span className="block text-xs text-slate-500">{formatDateTime(e.occurred_at)}</span></span>
            {e.amount && <span className="whitespace-nowrap font-medium">{formatMoney(e.amount)}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}

export function CustomerCrmPage() {
  const { t } = useTranslation()
  const id = Number(useParams().id)
  const valid = Number.isInteger(id) && id > 0
  const profile = useQuery({ queryKey: ['crmProfile', id], queryFn: () => getCrmProfile(id), enabled: valid })
  if (!valid || profile.isError) {
    const notFound = !valid || (profile.error instanceof ApiError && profile.error.status === 404)
    return (
      <div className="space-y-6">
        {notFound ? <Alert tone="error">{t('crm.customer.notFound')}</Alert> : <QueryError error={profile.error} onRetry={() => void profile.refetch()} />}
        <LinkButton to="/customers" variant="secondary">{t('crm.customer.back')}</LinkButton>
      </div>
    )
  }
  if (!profile.data) return <Spinner />
  const p = profile.data
  return (
    <div className="space-y-6">
      <Link to={`/customers/${id}`} className="inline-flex min-h-10 items-center gap-2 text-emerald-800 hover:underline">
        <ArrowLeft aria-hidden="true" className="size-4" />{t('crm.customer.back')}
      </Link>
      <h1 className="text-2xl font-bold text-slate-900">{p.name}</h1>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label={t('crm.customer.totalPurchases')} value={formatMoney(p.analytics.total_purchases)} />
        <Stat label={t('crm.customer.outstanding')} value={formatMoney(p.analytics.outstanding)} />
        <Stat label={t('crm.customer.lastPurchase')} value={p.analytics.days_since_last_purchase === null ? '—' : t('crm.days', { count: p.analytics.days_since_last_purchase })} />
        <Stat label={t('crm.customer.referralCode')} value={p.referral_code ?? '—'} hint={t('crm.customer.referralsMade', { count: p.referrals_made })} />
      </div>
      {p.analytics.segments.length > 0 && <p className="text-slate-700">{t('crm.customer.segments')}: {p.analytics.segments.map((s) => t(`crm.segments.${s}`, { defaultValue: s })).join(', ')}</p>}
      <Preferences profile={p} />
      <Loyalty profile={p} />
      <Notes id={id} />
      <Timeline id={id} />
    </div>
  )
}
