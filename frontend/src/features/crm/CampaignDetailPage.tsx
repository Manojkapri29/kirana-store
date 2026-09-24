import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { campaignAction, cancelCampaign, getCampaign, getCampaignAudience, getCampaignSends } from '@/api/crm'
import { TextField } from '@/components/fields'
import { Alert, Button, LinkButton, QueryError, Spinner } from '@/components/ui'
import { formatDateTime } from '@/lib/format'

import { CampaignStatusBadge, SendStatusBadge } from './common'
import { TH, useErrorText } from './crmUtils'

export function CampaignDetailPage() {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const id = Number(useParams().id)
  const valid = Number.isInteger(id) && id > 0
  const [confirmLaunch, setConfirmLaunch] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [reason, setReason] = useState('')
  const campaign = useQuery({ queryKey: ['campaign', id], queryFn: () => getCampaign(id), enabled: valid })
  const audience = useQuery({ queryKey: ['campaignAudience', id], queryFn: () => getCampaignAudience(id), enabled: valid })
  const sends = useQuery({ queryKey: ['campaignSends', id], queryFn: () => getCampaignSends(id), enabled: valid })
  const refresh = () => Promise.all(['campaign', 'campaigns', 'campaignSends', 'campaignAudience'].map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
  const act = useMutation({
    mutationFn: (action: 'launch' | 'pause' | 'resume') => campaignAction(id, action),
    onSuccess: async () => { setConfirmLaunch(false); await refresh() },
  })
  const cancel = useMutation({
    mutationFn: () => cancelCampaign(id, reason),
    onSuccess: async () => { setCancelling(false); setReason(''); await refresh() },
  })

  if (!valid || campaign.isError) {
    const notFound = !valid || (campaign.error instanceof ApiError && campaign.error.status === 404)
    return (
      <div className="space-y-6">
        {notFound ? <Alert tone="error">{t('crm.campaigns.notFound')}</Alert> : <QueryError error={campaign.error} onRetry={() => void campaign.refetch()} />}
        <LinkButton to="/crm/campaigns" variant="secondary">{t('crm.campaigns.back')}</LinkButton>
      </div>
    )
  }
  if (!campaign.data) return <Spinner />
  const c = campaign.data
  const canLaunch = c.status === 'DRAFT' || c.status === 'SCHEDULED'
  const canCancel = c.status !== 'COMPLETED' && c.status !== 'CANCELLED'
  const error = act.error ?? cancel.error

  return (
    <div className="space-y-6">
      <Link to="/crm/campaigns" className="inline-flex min-h-10 items-center gap-2 text-emerald-800 hover:underline">
        <ArrowLeft aria-hidden="true" className="size-4" />{t('crm.campaigns.back')}
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold text-slate-900">{c.name}</h1>
        <CampaignStatusBadge status={c.status} />
      </div>
      {c.requires_approval && <Alert tone="warning">{t('crm.campaigns.approvalPending')}</Alert>}
      {c.status === 'COMPLETED' && <Alert tone="info">{t('crm.noProvider')}</Alert>}
      {error && <Alert tone="error">{errorText(error)}</Alert>}

      <dl className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white px-4 shadow-sm">
        <div className="flex justify-between gap-4 py-2"><dt className="text-slate-600">{t('crm.campaigns.channel')}</dt><dd className="font-medium">{t(`crm.channels.${c.channel}`)}</dd></div>
        <div className="flex justify-between gap-4 py-2"><dt className="text-slate-600">{t('crm.campaigns.audience')}</dt><dd className="font-medium">{c.target_segment ? t(`crm.segments.${c.target_segment}`, { defaultValue: c.target_segment }) : t('crm.campaigns.savedGroup')}{audience.data ? ` · ${t('crm.campaigns.people', { count: audience.data.audience_size })}` : ''}</dd></div>
        <div className="flex justify-between gap-4 py-2"><dt className="text-slate-600">{t('crm.campaigns.launchedAt')}</dt><dd className="font-medium">{c.launched_at ? formatDateTime(c.launched_at) : '—'}</dd></div>
        {c.cancel_reason && <div className="flex justify-between gap-4 py-2"><dt className="text-slate-600">{t('crm.campaigns.cancelReason')}</dt><dd className="font-medium">{c.cancel_reason}</dd></div>}
      </dl>
      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-600">{t('crm.campaigns.message')}</h2>
        <p className="mt-2 whitespace-pre-wrap">{c.message_template}</p>
      </div>

      <div className="flex flex-wrap gap-3">
        {canLaunch && !confirmLaunch && <Button requires="CAMPAIGN_LAUNCH" onClick={() => setConfirmLaunch(true)}>{t('crm.campaigns.launch')}</Button>}
        {c.status === 'RUNNING' && <Button requires="CAMPAIGN_MANAGE" variant="secondary" loading={act.isPending} onClick={() => act.mutate('pause')}>{t('crm.campaigns.pause')}</Button>}
        {c.status === 'PAUSED' && <Button requires="CAMPAIGN_MANAGE" variant="secondary" loading={act.isPending} onClick={() => act.mutate('resume')}>{t('crm.campaigns.resume')}</Button>}
        {canCancel && !cancelling && <Button requires="CAMPAIGN_MANAGE" variant="secondary" onClick={() => setCancelling(true)}>{t('crm.campaigns.cancel')}</Button>}
      </div>
      {confirmLaunch && (
        <Alert tone="warning">
          <p className="font-medium">{t('crm.campaigns.launchConfirm', { count: audience.data?.audience_size ?? 0 })}</p>
          <div className="mt-3 flex gap-3">
            <Button loading={act.isPending} onClick={() => act.mutate('launch')}>{t('crm.campaigns.launchYes')}</Button>
            <Button variant="secondary" onClick={() => setConfirmLaunch(false)}>{t('common.cancel')}</Button>
          </div>
        </Alert>
      )}
      {cancelling && (
        <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
          <TextField label={t('crm.campaigns.cancelReason')} value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="flex gap-3">
            <Button loading={cancel.isPending} disabled={!reason.trim()} onClick={() => cancel.mutate()}>{t('crm.campaigns.cancelYes')}</Button>
            <Button variant="secondary" onClick={() => setCancelling(false)}>{t('common.cancel')}</Button>
          </div>
        </div>
      )}

      <section aria-labelledby="sends" className="space-y-3">
        <h2 id="sends" className="text-lg font-semibold">{t('crm.campaigns.outcomes')}</h2>
        {sends.data && sends.data.items.length === 0 && <p className="text-slate-600">{t('crm.campaigns.noOutcomes')}</p>}
        {sends.data && sends.data.items.length > 0 && (
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50"><tr><th className={TH}>{t('crm.campaigns.customer')}</th><th className={TH}>{t('crm.campaigns.outcome')}</th><th className={TH}>{t('crm.campaigns.detail')}</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {sends.data.items.map((s) => (
                  <tr key={s.id}>
                    <td className="px-4 py-3"><Link to={`/customers/${s.customer_id}/crm`} className="text-emerald-800 hover:underline">#{s.customer_id}</Link></td>
                    <td className="px-4 py-3"><SendStatusBadge status={s.status} /></td>
                    <td className="px-4 py-3 text-sm text-slate-600">{s.detail ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
