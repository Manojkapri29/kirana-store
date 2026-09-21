import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Pause, Pencil, Play, Power, Square } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { getPromotion, listPromotionUsage, setPromotionState } from '@/api/promotions'
import type { Promotion } from '@/api/types'
import { ExportButtons } from '@/components/ExportButtons'
import { Alert, Button, LinkButton, QueryError, Spinner } from '@/components/ui'
import { formatDate, formatDateTime, formatMoney } from '@/lib/format'

import { PromotionStatusBadge } from './PromotionBadges'

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-2">
      <dt className="text-slate-600">{label}</dt>
      <dd className="text-right font-medium text-slate-900">{children}</dd>
    </div>
  )
}

export function PromotionDetailPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const id = Number(useParams().id)
  const validId = Number.isInteger(id) && id > 0
  const [confirmExpire, setConfirmExpire] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const promotion = useQuery({ queryKey: ['promotion', id], queryFn: () => getPromotion(id), enabled: validId })
  const usage = useQuery({ queryKey: ['promotionUsage', id], queryFn: () => listPromotionUsage({ promotion_id: id }), enabled: validId })
  const change = useMutation({
    mutationFn: (action: 'activate' | 'pause' | 'expire') => setPromotionState(id, action),
    onSuccess: async () => {
      setConfirmExpire(false)
      setActionError(null)
      await Promise.all(['promotions', 'promotion', 'promotionUsage'].map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
    },
    onError: (error) => setActionError(error instanceof ApiError ? (error.status === 403 ? error.message : error.message) : t('common.genericError')),
  })

  if (!validId || promotion.isError) {
    const notFound = !validId || (promotion.error instanceof ApiError && promotion.error.status === 404)
    return (
      <div className="space-y-6">
        {notFound ? <Alert tone="error">{t('promotions.detail.notFound')}</Alert> : <QueryError onRetry={() => void promotion.refetch()} />}
        <LinkButton to="/promotions" variant="secondary">{t('promotions.detail.backToList')}</LinkButton>
      </div>
    )
  }
  if (!promotion.data) return <Spinner />
  const p: Promotion = promotion.data
  const status = p.effective_status
  const canEdit = status !== 'EXPIRED'
  const names = (label: string, list: string[]) =>
    list.length > 0 && (
      <Row label={label}>
        <span className="max-w-xs text-right">{list.join(', ')}</span>
      </Row>
    )

  return (
    <div className="space-y-6">
      <Link to="/promotions" className="inline-flex min-h-10 items-center gap-2 text-emerald-800 hover:underline">
        <ArrowLeft aria-hidden="true" className="size-5" />
        {t('promotions.detail.backToList')}
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold text-slate-900">{p.name}</h1>
            <PromotionStatusBadge promotion={p} />
            {p.is_live && <span className="text-sm font-medium text-emerald-800">{t('promotions.live')}</span>}
          </div>
          <p className="mt-1 text-lg text-slate-700">{p.terms}</p>
          {p.description && <p className="mt-1 text-slate-600">{p.description}</p>}
        </div>
        <div className="flex flex-wrap gap-3">
          {(status === 'DRAFT' || status === 'PAUSED') && (
            <Button loading={change.isPending} onClick={() => change.mutate('activate')}>
              <Play aria-hidden="true" className="size-5" />
              {status === 'PAUSED' ? t('promotions.detail.resume') : t('promotions.detail.activate')}
            </Button>
          )}
          {status === 'ACTIVE' && (
            <Button variant="secondary" loading={change.isPending} onClick={() => change.mutate('pause')}>
              <Pause aria-hidden="true" className="size-5" />
              {t('promotions.detail.pause')}
            </Button>
          )}
          {canEdit && (
            <LinkButton to={`/promotions/${p.id}/edit`} variant="secondary">
              <Pencil aria-hidden="true" className="size-5" />
              {t('common.edit')}
            </LinkButton>
          )}
          {canEdit && !confirmExpire && (
            <Button variant="danger" onClick={() => setConfirmExpire(true)}>
              <Power aria-hidden="true" className="size-5" />
              {t('promotions.detail.expire')}
            </Button>
          )}
        </div>
      </div>

      {actionError && <Alert tone="error">{actionError}</Alert>}
      {confirmExpire && (
        <div className="space-y-3 rounded-xl border border-red-200 bg-red-50 p-5">
          <p className="font-medium text-red-900">{t('promotions.detail.expireWarning')}</p>
          <div className="flex gap-3">
            <Button variant="danger" loading={change.isPending} onClick={() => change.mutate('expire')}>
              <Square aria-hidden="true" className="size-5" />
              {t('promotions.detail.confirmExpire')}
            </Button>
            <Button variant="secondary" onClick={() => setConfirmExpire(false)}>{t('common.cancel')}</Button>
          </div>
        </div>
      )}

      <Alert tone="info">{t('promotions.detail.historySafe')}</Alert>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-1 text-lg font-semibold text-slate-900">{t('promotions.detail.summary')}</h2>
          <dl className="divide-y divide-slate-100">
            <Row label={t('promotions.form.kind')}>{t(`promotions.types.${p.promo_type}`)}</Row>
            <Row label={t('promotions.form.scope')}>{t(`promotions.scopes.${p.scope}`)}</Row>
            {names(t('promotions.detail.products'), p.products)}
            {names(t('promotions.detail.categories'), p.categories)}
            <Row label={t('promotions.form.audience')}>{t(`promotions.audiences.${p.audience}`)}</Row>
            {names(t('promotions.detail.customers'), p.customers)}
            {p.coupon_code && <Row label={t('promotions.form.couponCode')}><span className="font-mono">{p.coupon_code}</span></Row>}
            <Row label={t('promotions.form.startsAt')}>{p.starts_at ? formatDateTime(p.starts_at) : '—'}</Row>
            <Row label={t('promotions.form.endsAt')}>{p.ends_at ? formatDateTime(p.ends_at) : '—'}</Row>
            <Row label={t('promotions.form.priority')}>{p.priority}</Row>
            <Row label={t('promotions.detail.stackNotes')}>{p.stackable ? t('promotions.detail.combinable') : t('promotions.detail.exclusive')}</Row>
          </dl>
        </section>
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-1 text-lg font-semibold text-slate-900">{t('promotions.detail.limits')}</h2>
          <dl className="divide-y divide-slate-100">
            <Row label={t('promotions.form.minCart')}>{p.min_cart_value ? formatMoney(p.min_cart_value) : '—'}</Row>
            <Row label={t('promotions.form.minQuantity')}>{p.min_quantity ?? '—'}</Row>
            <Row label={t('promotions.form.maxDiscount')}>{p.max_discount ? formatMoney(p.max_discount) : '—'}</Row>
            <Row label={t('promotions.form.usageLimit')}>{p.usage_limit ?? t('promotions.detail.unlimited')}</Row>
            <Row label={t('promotions.form.perCustomer')}>{p.per_customer_limit ?? t('promotions.detail.unlimited')}</Row>
          </dl>
          <p className="mt-3 text-slate-700">{t('promotions.detail.usage', { count: p.used_count, amount: formatMoney(p.discount_given) })}</p>
          <p className="mt-1 text-sm text-slate-500">{t('promotions.detail.createdBy', { name: p.created_by_name })}</p>
        </section>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-slate-900">{t('promotions.detail.usageHeading')}</h2>
        {usage.isError && <QueryError onRetry={() => void usage.refetch()} />}
        {usage.data && usage.data.items.length === 0 && <p className="text-slate-600">{t('promotions.detail.noUsage')}</p>}
        {usage.data && usage.data.items.length > 0 && (
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">{t('promotions.detail.usageColumns.date')}</th>
                  <th className="px-4 py-3">{t('promotions.detail.usageColumns.invoice')}</th>
                  <th className="px-4 py-3">{t('promotions.detail.usageColumns.customer')}</th>
                  <th className="px-4 py-3 text-right">{t('promotions.detail.usageColumns.discount')}</th>
                  <th className="px-4 py-3">{t('promotions.detail.usageColumns.why')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {usage.data.items.map((u) => (
                  <tr key={`${u.sale_id}-${u.promotion_id}`} className={u.sale_status === 'VOID' ? 'text-slate-500' : ''}>
                    <td className="whitespace-nowrap px-4 py-2">{formatDate(u.sale_date)}</td>
                    <td className="whitespace-nowrap px-4 py-2">
                      <Link to={`/sales/${u.sale_id}`} className="font-medium text-emerald-800 hover:underline">{u.invoice_no}</Link>
                      {u.sale_status === 'VOID' && <span className="ml-2 text-xs">{t('promotions.detail.voidUse')}</span>}
                    </td>
                    <td className="px-4 py-2">{u.customer_name ?? t('sales.walkIn')}</td>
                    <td className="whitespace-nowrap px-4 py-2 text-right font-medium">{formatMoney(u.discount_amount)}</td>
                    <td className="px-4 py-2 text-xs text-slate-600">{u.basis}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="border-t border-slate-200 pt-4">
          <p className="mb-2 text-sm font-medium text-slate-700">{t('promotions.exportUsage')}</p>
          <ExportButtons kind="promotion-usage" filters={{ promotion_id: p.id }} />
        </div>
      </section>
    </div>
  )
}
