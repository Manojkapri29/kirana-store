import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'

import { CHANNELS, createCampaign, listCampaigns, listGroups, type CampaignStatus, type Channel } from '@/api/crm'
import { FilterSelect, SelectField, TextAreaField, TextField } from '@/components/fields'
import { Alert, Button, EmptyState, PageHeader, QueryError, Spinner } from '@/components/ui'
import { formatDateTime } from '@/lib/format'

import { CampaignStatusBadge, CrmTabs } from './common'
import { TH, useErrorText } from './crmUtils'

const SEGMENTS = ['NEW', 'ACTIVE', 'INACTIVE', 'RECENTLY_INACTIVE', 'LONG_INACTIVE', 'HIGH_VALUE', 'FREQUENT_BUYER', 'ONE_TIME_BUYER'] as const
const STATUSES: readonly CampaignStatus[] = ['DRAFT', 'SCHEDULED', 'RUNNING', 'PAUSED', 'COMPLETED', 'CANCELLED']

function NewCampaignForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const groups = useQuery({ queryKey: ['crmGroups'], queryFn: listGroups })
  const [name, setName] = useState('')
  const [channel, setChannel] = useState<Channel>('IN_APP')
  const [target, setTarget] = useState('segment:ACTIVE')
  const [message, setMessage] = useState('')
  const create = useMutation({
    mutationFn: () => {
      const [kind, value] = target.split(':')
      return createCampaign({
        name, channel, message_template: message,
        target_group_id: kind === 'group' ? Number(value) : null,
        target_segment: kind === 'segment' ? value : null,
      })
    },
    onSuccess: async (campaign) => {
      await queryClient.invalidateQueries({ queryKey: ['campaigns'] })
      onDone()
      void navigate(`/crm/campaigns/${campaign.id}`)
    },
  })
  return (
    <form
      className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
      onSubmit={(e) => { e.preventDefault(); create.mutate() }}
    >
      <TextField label={t('crm.campaigns.name')} value={name} onChange={(e) => setName(e.target.value)} required maxLength={150} />
      <div className="grid gap-4 md:grid-cols-2">
        <SelectField label={t('crm.campaigns.channel')} value={channel} onChange={(e) => setChannel(e.target.value as Channel)}>
          {CHANNELS.map((c) => <option key={c} value={c}>{t(`crm.channels.${c}`)}</option>)}
        </SelectField>
        <SelectField label={t('crm.campaigns.audience')} value={target} onChange={(e) => setTarget(e.target.value)}>
          <optgroup label={t('crm.campaigns.segments')}>
            {SEGMENTS.map((s) => <option key={s} value={`segment:${s}`}>{t(`crm.segments.${s}`)}</option>)}
          </optgroup>
          {(groups.data?.items.length ?? 0) > 0 && (
            <optgroup label={t('crm.campaigns.groups')}>
              {groups.data?.items.map((g) => <option key={g.id} value={`group:${g.id}`}>{g.name}</option>)}
            </optgroup>
          )}
        </SelectField>
      </div>
      <TextAreaField label={t('crm.campaigns.message')} value={message} onChange={(e) => setMessage(e.target.value)} required rows={4} maxLength={4000} />
      <Alert tone="info">{t('crm.campaigns.draftNote')}</Alert>
      {create.isError && <Alert tone="error">{errorText(create.error)}</Alert>}
      <div className="flex gap-3">
        <Button type="submit" loading={create.isPending} disabled={!name.trim() || !message.trim()}>{t('crm.campaigns.createDraft')}</Button>
        <Button variant="secondary" onClick={onDone}>{t('common.cancel')}</Button>
      </div>
    </form>
  )
}

export function CampaignsPage() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<CampaignStatus | ''>('')
  const [creating, setCreating] = useState(false)
  const list = useQuery({ queryKey: ['campaigns', status], queryFn: () => listCampaigns({ status, limit: 100 }) })
  return (
    <div className="space-y-6">
      <PageHeader
        title={t('crm.campaigns.title')}
        subtitle={t('crm.campaigns.subtitle')}
        actions={
          <Button requires="CAMPAIGN_MANAGE" onClick={() => setCreating(true)}>
            <Plus aria-hidden="true" className="size-5" />{t('crm.campaigns.add')}
          </Button>
        }
      />
      <CrmTabs />
      <Alert tone="warning">{t('crm.noProvider')}</Alert>
      {creating && <NewCampaignForm onDone={() => setCreating(false)} />}
      <FilterSelect aria-label={t('crm.campaigns.filterStatus')} value={status} onChange={(e) => setStatus(e.target.value as CampaignStatus | '')}>
        <option value="">{t('crm.campaigns.allStatuses')}</option>
        {STATUSES.map((s) => <option key={s} value={s}>{t(`crm.campaignStatus.${s}`)}</option>)}
      </FilterSelect>
      {list.isError && <QueryError error={list.error} onRetry={() => void list.refetch()} />}
      {list.isPending && <Spinner />}
      {list.data && list.data.items.length === 0 && <EmptyState title={t('crm.campaigns.empty')} hint={t('crm.campaigns.emptyHint')} />}
      {list.data && list.data.items.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200">
            <thead className="bg-slate-50">
              <tr>
                <th className={TH}>{t('crm.campaigns.name')}</th>
                <th className={TH}>{t('crm.campaigns.channel')}</th>
                <th className={TH}>{t('crm.campaigns.audience')}</th>
                <th className={TH}>{t('crm.campaigns.status')}</th>
                <th className={TH}>{t('crm.campaigns.created')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {list.data.items.map((c) => (
                <tr key={c.id} className="hover:bg-slate-50">
                  <td className="px-4 py-3"><Link to={`/crm/campaigns/${c.id}`} className="font-medium text-emerald-800 hover:underline">{c.name}</Link></td>
                  <td className="px-4 py-3 text-sm">{t(`crm.channels.${c.channel}`)}</td>
                  <td className="px-4 py-3 text-sm">{c.target_segment ? t(`crm.segments.${c.target_segment}`, { defaultValue: c.target_segment }) : t('crm.campaigns.savedGroup')}</td>
                  <td className="px-4 py-3">
                    <CampaignStatusBadge status={c.status} />
                    {c.requires_approval && <span className="ml-2 text-xs text-amber-800">{t('crm.campaigns.awaitingApproval')}</span>}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">{formatDateTime(c.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
