import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { expireLoyaltyPoints, getLoyaltyProgram, getLoyaltySummary, saveLoyaltyProgram, type LoyaltyProgram, type LoyaltyProgramPayload } from '@/api/crm'
import { TextField } from '@/components/fields'
import { Alert, Button, PageHeader, QueryError, Spinner } from '@/components/ui'

import { CrmTabs, Stat } from './common'
import { useErrorText } from './crmUtils'

const optionalInt = (v: string): number | null => (v.trim() === '' ? null : Number(v))

function LoyaltyForm({ program }: { program: LoyaltyProgram | null }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const [active, setActive] = useState(program?.is_active ?? false)
  const [perAmount, setPerAmount] = useState(program?.points_per_amount ?? '')
  const [minTxn, setMinTxn] = useState(program?.min_transaction_amount ?? '0')
  const [redemption, setRedemption] = useState(program?.redemption_value ?? '')
  const [minRedeem, setMinRedeem] = useState(program?.min_redemption_points?.toString() ?? '')
  const [maxRedeem, setMaxRedeem] = useState(program?.max_redeem_points_per_txn?.toString() ?? '')
  const [expiry, setExpiry] = useState(program?.points_expiry_days?.toString() ?? '')
  const save = useMutation({
    mutationFn: () => {
      const payload: LoyaltyProgramPayload = {
        is_active: active, points_per_amount: perAmount, min_transaction_amount: minTxn || '0', redemption_value: redemption,
        min_redemption_points: optionalInt(minRedeem), max_redeem_points_per_txn: optionalInt(maxRedeem), points_expiry_days: optionalInt(expiry),
      }
      return saveLoyaltyProgram(payload)
    },
    onSuccess: () => Promise.all(['loyaltyProgram', 'loyaltySummary', 'crmDashboard'].map((k) => queryClient.invalidateQueries({ queryKey: [k] }))),
  })
  return (
    <form className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
      <label className="flex min-h-12 items-center gap-3">
        <input type="checkbox" className="size-5" checked={active} onChange={(e) => setActive(e.target.checked)} />
        <span className="font-medium">{t('crm.loyalty.active')}</span>
      </label>
      <div className="grid gap-4 md:grid-cols-2">
        <TextField label={t('crm.loyalty.pointsPerAmount')} inputMode="decimal" value={perAmount} onChange={(e) => setPerAmount(e.target.value)} required hint={t('crm.loyalty.pointsPerAmountHint')} />
        <TextField label={t('crm.loyalty.minTxn')} inputMode="decimal" value={minTxn} onChange={(e) => setMinTxn(e.target.value)} />
        <TextField label={t('crm.loyalty.redemptionValue')} inputMode="decimal" value={redemption} onChange={(e) => setRedemption(e.target.value)} required hint={t('crm.loyalty.redemptionValueHint')} />
        <TextField label={t('crm.loyalty.minRedeem')} type="number" min={1} value={minRedeem} onChange={(e) => setMinRedeem(e.target.value)} optional />
        <TextField label={t('crm.loyalty.maxRedeem')} type="number" min={1} value={maxRedeem} onChange={(e) => setMaxRedeem(e.target.value)} optional />
        <TextField label={t('crm.loyalty.expiryDays')} type="number" min={1} value={expiry} onChange={(e) => setExpiry(e.target.value)} optional hint={t('crm.loyalty.expiryHint')} />
      </div>
      {save.isError && <Alert tone="error">{errorText(save.error)}</Alert>}
      {save.isSuccess && <Alert tone="success">{t('crm.saved')}</Alert>}
      <Button type="submit" requires="LOYALTY_MANAGE" loading={save.isPending}>{t('common.save')}</Button>
    </form>
  )
}

export function LoyaltyPage() {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const program = useQuery({ queryKey: ['loyaltyProgram'], queryFn: getLoyaltyProgram })
  const summary = useQuery({ queryKey: ['loyaltySummary'], queryFn: getLoyaltySummary })
  const expire = useMutation({
    mutationFn: expireLoyaltyPoints,
    onSuccess: () => Promise.all(['loyaltySummary', 'crmDashboard'].map((k) => queryClient.invalidateQueries({ queryKey: [k] }))),
  })

  return (
    <div className="space-y-6">
      <PageHeader title={t('crm.loyalty.title')} subtitle={t('crm.loyalty.subtitle')} />
      <CrmTabs />
      {program.isError && <QueryError error={program.error} onRetry={() => void program.refetch()} />}
      {program.isPending && <Spinner />}
      {program.data === null && <Alert tone="warning">{t('crm.loyalty.notConfigured')}</Alert>}
      {summary.data && (
        <div className="grid grid-cols-3 gap-3">
          <Stat label={t('crm.stats.pointsIssued')} value={summary.data.points_issued} />
          <Stat label={t('crm.stats.pointsRedeemed')} value={summary.data.points_redeemed} />
          <Stat label={t('crm.stats.pointsOutstanding')} value={summary.data.points_outstanding} />
        </div>
      )}
      {!program.isPending && !program.isError && <LoyaltyForm key={program.data?.id ?? 'new'} program={program.data ?? null} />}
      <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold">{t('crm.loyalty.expireTitle')}</h2>
        <p className="text-slate-600">{t('crm.loyalty.expireHint')}</p>
        {expire.isError && <Alert tone="error">{errorText(expire.error)}</Alert>}
        {expire.data && <Alert tone="success">{t('crm.loyalty.expired', { customers: expire.data.customers_expired, points: expire.data.points_expired })}</Alert>}
        <Button variant="secondary" requires="LOYALTY_MANAGE" loading={expire.isPending} disabled={!program.data?.points_expiry_days} onClick={() => expire.mutate()}>{t('crm.loyalty.expireNow')}</Button>
      </section>
    </div>
  )
}
