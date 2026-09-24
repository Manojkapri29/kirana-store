import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { getApprovalSettings, getReferralProgram, saveApprovalSettings, saveReferralProgram, type ApprovalSettings, type ReferralProgram } from '@/api/crm'
import { TextField } from '@/components/fields'
import { Alert, Button, PageHeader, QueryError, Spinner } from '@/components/ui'

import { CrmTabs } from './common'
import { useErrorText } from './crmUtils'

const optionalInt = (v: string): number | null => (v.trim() === '' ? null : Number(v))

function ApprovalSettingsForm({ settings }: { settings: ApprovalSettings }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const [audience, setAudience] = useState(settings.campaign_audience_threshold?.toString() ?? '')
  const [adjustment, setAdjustment] = useState(settings.loyalty_adjustment_threshold?.toString() ?? '')
  const save = useMutation({
    mutationFn: () => saveApprovalSettings({ campaign_audience_threshold: optionalInt(audience), loyalty_adjustment_threshold: optionalInt(adjustment) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['crmApprovalSettings'] }),
  })
  return (
    <form className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
      <h2 className="text-lg font-semibold">{t('crm.settings.approvalTitle')}</h2>
      <p className="text-slate-600">{t('crm.settings.approvalHint')}</p>
      <div className="grid gap-4 md:grid-cols-2">
        <TextField label={t('crm.settings.audienceThreshold')} type="number" min={0} value={audience} onChange={(e) => setAudience(e.target.value)} optional hint={t('crm.settings.blankOff')} />
        <TextField label={t('crm.settings.adjustmentThreshold')} type="number" min={0} value={adjustment} onChange={(e) => setAdjustment(e.target.value)} optional hint={t('crm.settings.blankOff')} />
      </div>
      {save.isError && <Alert tone="error">{errorText(save.error)}</Alert>}
      {save.isSuccess && <Alert tone="success">{t('crm.saved')}</Alert>}
      <Button type="submit" requires="CAMPAIGN_MANAGE" loading={save.isPending}>{t('common.save')}</Button>
    </form>
  )
}

function ReferralForm({ program }: { program: ReferralProgram | null }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const [active, setActive] = useState(program?.is_active ?? false)
  const [referrer, setReferrer] = useState(program?.referrer_reward_points?.toString() ?? '')
  const [referred, setReferred] = useState(program?.referred_reward_points?.toString() ?? '')
  const [minPurchase, setMinPurchase] = useState(program?.min_purchase_amount ?? '')
  const [maxReferrals, setMaxReferrals] = useState(program?.max_referrals_per_customer?.toString() ?? '')
  const save = useMutation({
    mutationFn: () =>
      saveReferralProgram({
        is_active: active, referrer_reward_points: optionalInt(referrer), referred_reward_points: optionalInt(referred),
        min_purchase_amount: minPurchase.trim() === '' ? null : minPurchase, max_referrals_per_customer: optionalInt(maxReferrals), expiry_days: null,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['referralProgram'] }),
  })
  return (
    <form className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
      <h2 className="text-lg font-semibold">{t('crm.settings.referralTitle')}</h2>
      <p className="text-slate-600">{t('crm.settings.referralHint')}</p>
      {program === null && <Alert tone="warning">{t('crm.settings.referralNone')}</Alert>}
      <label className="flex min-h-12 items-center gap-3">
        <input type="checkbox" className="size-5" checked={active} onChange={(e) => setActive(e.target.checked)} />
        <span className="font-medium">{t('crm.settings.referralActive')}</span>
      </label>
      <div className="grid gap-4 md:grid-cols-2">
        <TextField label={t('crm.settings.referrerReward')} type="number" min={1} value={referrer} onChange={(e) => setReferrer(e.target.value)} optional />
        <TextField label={t('crm.settings.referredReward')} type="number" min={1} value={referred} onChange={(e) => setReferred(e.target.value)} optional />
        <TextField label={t('crm.settings.minPurchase')} inputMode="decimal" value={minPurchase} onChange={(e) => setMinPurchase(e.target.value)} optional />
        <TextField label={t('crm.settings.maxReferrals')} type="number" min={1} value={maxReferrals} onChange={(e) => setMaxReferrals(e.target.value)} optional />
      </div>
      {save.isError && <Alert tone="error">{errorText(save.error)}</Alert>}
      {save.isSuccess && <Alert tone="success">{t('crm.saved')}</Alert>}
      <Button type="submit" requires="REFERRAL_MANAGE" loading={save.isPending}>{t('common.save')}</Button>
    </form>
  )
}

function ApprovalSettingsCard() {
  const settings = useQuery({ queryKey: ['crmApprovalSettings'], queryFn: getApprovalSettings })
  if (settings.isError) return <QueryError error={settings.error} onRetry={() => void settings.refetch()} />
  if (!settings.data) return <Spinner />
  return <ApprovalSettingsForm key={JSON.stringify(settings.data)} settings={settings.data} />
}

function ReferralCard() {
  const program = useQuery({ queryKey: ['referralProgram'], queryFn: getReferralProgram })
  if (program.isError) return <QueryError error={program.error} onRetry={() => void program.refetch()} />
  if (program.isPending) return <Spinner />
  return <ReferralForm key={program.data?.id ?? 'new'} program={program.data ?? null} />
}

export function CrmSettingsPage() {
  const { t } = useTranslation()
  return (
    <div className="space-y-6">
      <PageHeader title={t('crm.settings.title')} subtitle={t('crm.settings.subtitle')} />
      <CrmTabs />
      <ApprovalSettingsCard />
      <ReferralCard />
    </div>
  )
}
