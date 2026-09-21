import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { listPromotions } from '@/api/promotions'
import type { Promotion, PromotionStatus, PromotionType } from '@/api/types'
import { ExportButtons } from '@/components/ExportButtons'
import { FilterSelect } from '@/components/fields'
import { Pagination } from '@/components/Pagination'
import { SearchInput } from '@/components/SearchInput'
import { Alert, EmptyState, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useEntitlements } from '@/features/subscription/useEntitlements'
import { useDebounced } from '@/hooks/useDebounced'
import { formatDateTime, formatMoney } from '@/lib/format'

import { PromotionStatusBadge } from './PromotionBadges'

const PAGE_SIZE = 25
type CouponFilter = '' | 'true' | 'false'

function dates(p: Promotion): string {
  if (!p.starts_at && !p.ends_at) return '—'
  return `${p.starts_at ? formatDateTime(p.starts_at) : '…'} → ${p.ends_at ? formatDateTime(p.ends_at) : '…'}`
}

export function PromotionsPage() {
  const { t } = useTranslation()
  const { allows } = useEntitlements()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<PromotionStatus | ''>('')
  const [type, setType] = useState<PromotionType | ''>('')
  const [coupon, setCoupon] = useState<CouponFilter>('')
  const [offset, setOffset] = useState(0)
  const q = useDebounced(search.trim())
  const filters = { q, status, promo_type: type, coupon_only: coupon === '' ? null : coupon === 'true' }
  const list = useQuery({
    queryKey: ['promotions', filters, offset],
    queryFn: () => listPromotions({ ...filters, limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
  })
  const data = list.data
  const isFiltering = q !== '' || status !== '' || type !== '' || coupon !== ''
  const reset = <T,>(set: (value: T) => void) => (value: T) => {
    set(value)
    setOffset(0)
  }
  const addButton = (
    <LinkButton to="/promotions/new">
      <Plus aria-hidden="true" className="size-5" />
      {t('promotions.add')}
    </LinkButton>
  )
  const heading = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'

  return (
    <div className="space-y-6">
      <PageHeader title={t('promotions.title')} subtitle={t('promotions.subtitle')} actions={allows('promotions') === false ? undefined : addButton} />
      {allows('promotions') === false && <Alert tone="warning">{t('promotions.planNeeded')}</Alert>}

      <div className="flex flex-col gap-3 xl:flex-row">
        <SearchInput value={search} onChange={reset(setSearch)} placeholder={t('promotions.searchPlaceholder')} />
        <FilterSelect aria-label={t('promotions.filters.status')} value={status} onChange={(e) => reset(setStatus)(e.target.value as PromotionStatus | '')}>
          <option value="">{t('promotions.filters.allStatuses')}</option>
          {(['DRAFT', 'ACTIVE', 'PAUSED', 'EXPIRED'] as const).map((s) => (
            <option key={s} value={s}>{t(`promotions.status.${s}`)}</option>
          ))}
        </FilterSelect>
        <FilterSelect aria-label={t('promotions.filters.type')} value={type} onChange={(e) => reset(setType)(e.target.value as PromotionType | '')}>
          <option value="">{t('promotions.filters.allTypes')}</option>
          {(['PERCENT', 'AMOUNT', 'OFFER_PRICE', 'BUY_X_GET_Y'] as const).map((k) => (
            <option key={k} value={k}>{t(`promotions.types.${k}`)}</option>
          ))}
        </FilterSelect>
        <FilterSelect aria-label={t('promotions.filters.coupons')} value={coupon} onChange={(e) => reset(setCoupon)(e.target.value as CouponFilter)}>
          <option value="">{t('promotions.filters.allCoupons')}</option>
          <option value="true">{t('promotions.filters.couponOnly')}</option>
          <option value="false">{t('promotions.filters.automaticOnly')}</option>
        </FilterSelect>
      </div>

      {list.isError && <QueryError error={list.error} onRetry={() => void list.refetch()} />}
      {list.isPending && <Spinner />}
      {list.error instanceof ApiError && list.error.status === 403 && <Alert tone="warning">{t('promotions.planNeeded')}</Alert>}
      {data && data.items.length === 0 && (
        <EmptyState title={isFiltering ? t('promotions.noMatch') : t('promotions.empty')} hint={isFiltering ? undefined : t('promotions.emptyHint')} action={!isFiltering && allows('promotions') !== false && addButton} />
      )}

      {data && data.items.length > 0 && (
        <>
          <div className="hidden overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm md:block">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50">
                <tr>
                  <th className={heading}>{t('promotions.columns.name')}</th>
                  <th className={heading}>{t('promotions.columns.gives')}</th>
                  <th className={heading}>{t('promotions.columns.appliesTo')}</th>
                  <th className={heading}>{t('promotions.columns.coupon')}</th>
                  <th className={heading}>{t('promotions.columns.dates')}</th>
                  <th className={`${heading} text-right`}>{t('promotions.columns.used')}</th>
                  <th className={heading}>{t('promotions.columns.status')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.items.map((p) => (
                  <tr key={p.id} className="hover:bg-slate-50">
                    <td className="min-w-40 px-4 py-3">
                      <Link to={`/promotions/${p.id}`} className="font-medium text-emerald-800 hover:underline">{p.name}</Link>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm">{p.terms}</td>
                    <td className="px-4 py-3 text-sm">{t(`promotions.scopes.${p.scope}`)}</td>
                    <td className="px-4 py-3 font-mono text-sm">{p.coupon_code ?? '—'}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm">{dates(p)}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm">{p.used_count} · {formatMoney(p.discount_given)}</td>
                    <td className="px-4 py-3"><PromotionStatusBadge promotion={p} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="space-y-3 md:hidden">
            {data.items.map((p) => (
              <li key={p.id}>
                <Link to={`/promotions/${p.id}`} className="block rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                  <div className="flex items-start justify-between gap-3">
                    <p className="font-semibold text-slate-900">{p.name}</p>
                    <PromotionStatusBadge promotion={p} />
                  </div>
                  <p className="mt-1 text-slate-700">{p.terms}</p>
                  <p className="mt-1 text-sm text-slate-600">{t(`promotions.scopes.${p.scope}`)}{p.coupon_code ? ` · ${p.coupon_code}` : ''}</p>
                </Link>
              </li>
            ))}
          </ul>
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
          <div className="space-y-4 border-t border-slate-200 pt-4">
            <div>
              <p className="mb-2 text-sm font-medium text-slate-700">{t('promotions.exportList')}</p>
              <ExportButtons kind="promotions" filters={{ q, status: status || undefined, promo_type: type || undefined, coupon_only: filters.coupon_only }} />
            </div>
            <div>
              <p className="mb-2 text-sm font-medium text-slate-700">{t('promotions.exportCoupons')}</p>
              <ExportButtons kind="promotion-usage" filters={{ coupon_only: true }} />
            </div>
          </div>
        </>
      )}
    </div>
  )
}
