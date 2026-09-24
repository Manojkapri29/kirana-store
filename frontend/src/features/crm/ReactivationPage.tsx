import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'

import { CHANNELS, createReactivationDraft, previewReactivation, type Channel } from '@/api/crm'
import { SelectField, TextAreaField, TextField } from '@/components/fields'
import { Alert, Badge, Button, EmptyState, PageHeader, QueryError, Spinner } from '@/components/ui'

import { CrmTabs } from './common'
import { TH, useErrorText } from './crmUtils'

export function ReactivationPage() {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const navigate = useNavigate()
  const [inactiveDays, setInactiveDays] = useState('60')
  const [cooldownDays, setCooldownDays] = useState('30')
  const [channel, setChannel] = useState<Channel>('SMS')
  const [name, setName] = useState('')
  const [message, setMessage] = useState('')
  const params = { inactive_days: Math.max(1, Number(inactiveDays) || 60), cooldown_days: Math.max(0, Number(cooldownDays) || 0), channel }
  const preview = useQuery({ queryKey: ['reactivationPreview', params], queryFn: () => previewReactivation(params) })
  const draft = useMutation({
    mutationFn: () => createReactivationDraft({ ...params, name, message_template: message }),
    onSuccess: (campaign) => void navigate(`/crm/campaigns/${campaign.id}`),
  })
  const p = preview.data

  return (
    <div className="space-y-6">
      <PageHeader title={t('crm.reactivation.title')} subtitle={t('crm.reactivation.subtitle')} />
      <CrmTabs />
      <div className="grid gap-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:grid-cols-3">
        <TextField label={t('crm.reactivation.inactiveDays')} type="number" min={1} value={inactiveDays} onChange={(e) => setInactiveDays(e.target.value)} />
        <TextField label={t('crm.reactivation.cooldownDays')} type="number" min={0} value={cooldownDays} onChange={(e) => setCooldownDays(e.target.value)} hint={t('crm.reactivation.cooldownHint')} />
        <SelectField label={t('crm.campaigns.channel')} value={channel} onChange={(e) => setChannel(e.target.value as Channel)}>
          {CHANNELS.map((c) => <option key={c} value={c}>{t(`crm.channels.${c}`)}</option>)}
        </SelectField>
      </div>
      {preview.isError && <QueryError error={preview.error} onRetry={() => void preview.refetch()} />}
      {preview.isPending && <Spinner />}
      {p && (
        <>
          <p className="text-slate-700">{t('crm.reactivation.summary', { eligible: p.eligible_count, excluded: p.excluded_count })}</p>
          {p.rows.length === 0 ? (
            <EmptyState title={t('crm.reactivation.empty')} />
          ) : (
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
              <table className="min-w-full divide-y divide-slate-200">
                <thead className="bg-slate-50"><tr><th className={TH}>{t('crm.campaigns.customer')}</th><th className={TH}>{t('crm.reactivation.verdict')}</th><th className={TH}>{t('crm.reactivation.why')}</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {p.rows.map((r) => (
                    <tr key={r.customer_id}>
                      <td className="px-4 py-3"><Link to={`/customers/${r.customer_id}/crm`} className="font-medium text-emerald-800 hover:underline">{r.name}</Link></td>
                      <td className="px-4 py-3"><Badge tone={r.verdict === 'ELIGIBLE' ? 'green' : 'slate'}>{t(`crm.verdict.${r.verdict}`)}</Badge></td>
                      <td className="px-4 py-3 text-sm text-slate-700">{r.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {p.eligible_count > 0 && (
            <form className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" onSubmit={(e) => { e.preventDefault(); draft.mutate() }}>
              <h2 className="text-lg font-semibold">{t('crm.reactivation.createTitle')}</h2>
              <TextField label={t('crm.campaigns.name')} value={name} onChange={(e) => setName(e.target.value)} required />
              <TextAreaField label={t('crm.campaigns.message')} value={message} onChange={(e) => setMessage(e.target.value)} required rows={3} />
              <Alert tone="info">{t('crm.campaigns.draftNote')}</Alert>
              {draft.isError && <Alert tone="error">{errorText(draft.error)}</Alert>}
              <Button type="submit" requires="CAMPAIGN_MANAGE" loading={draft.isPending} disabled={!name.trim() || !message.trim()}>{t('crm.campaigns.createDraft')}</Button>
            </form>
          )}
        </>
      )}
    </div>
  )
}
