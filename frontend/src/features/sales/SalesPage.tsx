import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'

import { listSales } from '@/api/sales'
import type { PaymentType, SaleStatus, SaleSummary } from '@/api/types'
import { ExportButtons } from '@/components/ExportButtons'
import { FilterSelect } from '@/components/fields'
import { Pagination } from '@/components/Pagination'
import { SearchInput } from '@/components/SearchInput'
import { Badge, EmptyState, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useDebounced } from '@/hooks/useDebounced'
import { formatDate, formatMoney, NOT_SET } from '@/lib/format'

import { SaleStatusBadge } from './SaleStatusBadge'

const PAGE_SIZE = 25
const DATE_INPUT =
  'block min-h-12 rounded-lg border border-slate-300 bg-white px-3 text-base focus-visible:outline-2 focus-visible:outline-emerald-600'

function PaymentBadge({ type }: { type: PaymentType | null }) {
  const { t } = useTranslation()
  if (type === null) return <span className="text-slate-400">{NOT_SET}</span>
  return <Badge tone={type === 'CREDIT' ? 'amber' : 'green'}>{t(`sales.paymentType.${type}`)}</Badge>
}

export function SalesPage() {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<SaleStatus | ''>('')
  const [paymentType, setPaymentType] = useState<PaymentType | ''>('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [offset, setOffset] = useState(0)
  const q = useDebounced(search.trim())
  const customerId = searchParams.get('customer')

  const rangeInvalid = dateFrom !== '' && dateTo !== '' && dateFrom > dateTo
  const filters = {
    q,
    customer_id: customerId === null ? null : Number(customerId),
    status,
    payment_type: paymentType,
    date_from: dateFrom,
    date_to: dateTo,
  }
  const sales = useQuery({
    queryKey: ['sales', filters, offset],
    queryFn: () => listSales({ ...filters, limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
    enabled: !rangeInvalid,
  })

  const data = sales.data
  const isFiltering = q !== '' || customerId !== null || status !== '' || paymentType !== '' || dateFrom !== '' || dateTo !== ''
  const addButton = (
    <LinkButton to="/sales/new" requires="SALE_CREATE">
      <Plus aria-hidden="true" className="size-5" />
      {t('sales.add')}
    </LinkButton>
  )
  const reset = <T,>(set: (value: T) => void) => (value: T) => {
    set(value)
    setOffset(0)
  }
  const exportFilters = {
    q,
    customer_id: filters.customer_id,
    status: status || undefined,
    payment_type: paymentType || undefined,
    date_from: dateFrom,
    date_to: dateTo,
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t('sales.title')} subtitle={t('sales.subtitle')} actions={addButton} />

      <div className="flex flex-col gap-3 xl:flex-row">
        <SearchInput value={search} onChange={reset(setSearch)} placeholder={t('sales.searchPlaceholder')} autoFocus />
        <FilterSelect
          aria-label={t('sales.filters.status')}
          value={status}
          onChange={(event) => reset(setStatus)(event.target.value as SaleStatus | '')}
        >
          <option value="">{t('sales.filters.allStatuses')}</option>
          <option value="DRAFT">{t('sales.status.DRAFT')}</option>
          <option value="POSTED">{t('sales.status.POSTED')}</option>
          <option value="VOID">{t('sales.status.VOID')}</option>
        </FilterSelect>
        <FilterSelect
          aria-label={t('sales.filters.payment')}
          value={paymentType}
          onChange={(event) => reset(setPaymentType)(event.target.value as PaymentType | '')}
        >
          <option value="">{t('sales.filters.allPayments')}</option>
          <option value="PAID">{t('sales.paymentType.PAID')}</option>
          <option value="CREDIT">{t('sales.paymentType.CREDIT')}</option>
        </FilterSelect>
      </div>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <label className="flex items-center gap-2 text-sm text-slate-700">
          {t('sales.filters.from')}
          <input type="date" value={dateFrom} onChange={(event) => reset(setDateFrom)(event.target.value)} className={DATE_INPUT} />
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          {t('sales.filters.to')}
          <input type="date" value={dateTo} onChange={(event) => reset(setDateTo)(event.target.value)} className={DATE_INPUT} />
        </label>
        {rangeInvalid && (
          <p role="alert" className="text-sm font-medium text-red-700">
            {t('sales.filters.rangeInvalid')}
          </p>
        )}
      </div>

      {sales.isError && <QueryError error={sales.error} onRetry={() => void sales.refetch()} />}
      {sales.isPending && !rangeInvalid && <Spinner />}

      {data && data.items.length === 0 && (
        <EmptyState
          title={isFiltering ? t('sales.noMatch') : t('sales.empty')}
          hint={isFiltering ? undefined : t('sales.emptyHint')}
          action={!isFiltering && addButton}
        />
      )}

      {data && data.items.length > 0 && (
        <>
          <SaleTable items={data.items} />
          <SaleCards items={data.items} />
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
          <div className="space-y-4 border-t border-slate-200 pt-4">
            <div>
              <p className="mb-2 text-sm font-medium text-slate-700">{t('sales.exportList')}</p>
              <ExportButtons kind="sales" filters={exportFilters} />
            </div>
            <div>
              <p className="mb-2 text-sm font-medium text-slate-700">{t('sales.exportItems')}</p>
              <ExportButtons kind="sale-items" filters={exportFilters} />
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function SaleTable({ items }: { items: SaleSummary[] }) {
  const { t } = useTranslation()
  const heading = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'
  return (
    <div className="hidden overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm md:block">
      <table className="min-w-full divide-y divide-slate-200">
        <thead className="bg-slate-50">
          <tr>
            <th className={heading}>{t('sales.columns.number')}</th>
            <th className={heading}>{t('sales.columns.date')}</th>
            <th className={heading}>{t('sales.columns.customer')}</th>
            <th className={`${heading} text-right`}>{t('sales.columns.items')}</th>
            <th className={`${heading} text-right`}>{t('sales.columns.total')}</th>
            <th className={heading}>{t('sales.columns.payment')}</th>
            <th className={heading}>{t('sales.columns.status')}</th>
            <th className={`${heading} hidden xl:table-cell`}>{t('sales.columns.createdBy')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {items.map((sale) => (
            <tr key={sale.id} className={`hover:bg-slate-50 ${sale.status === 'VOID' ? 'text-slate-500' : ''}`}>
              <td className="whitespace-nowrap px-4 py-3">
                <Link to={`/sales/${sale.id}`} className="font-medium text-emerald-800 hover:underline">
                  {sale.invoice_no ?? t('sales.draftLabel', { id: sale.id })}
                </Link>
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm">{formatDate(sale.sale_date)}</td>
              <td className="min-w-40 px-4 py-3">{sale.customer_name ?? <span className="text-slate-400">{t('sales.walkIn')}</span>}</td>
              <td className="px-4 py-3 text-right">{sale.item_count}</td>
              <td className="whitespace-nowrap px-4 py-3 text-right font-semibold">{formatMoney(sale.total_amount)}</td>
              <td className="px-4 py-3">
                <PaymentBadge type={sale.payment_type} />
              </td>
              <td className="px-4 py-3">
                <SaleStatusBadge status={sale.status} />
              </td>
              <td className="hidden whitespace-nowrap px-4 py-3 text-sm xl:table-cell">{sale.created_by_name}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The same sales as tappable cards, for phones. */
function SaleCards({ items }: { items: SaleSummary[] }) {
  const { t } = useTranslation()
  return (
    <ul className="space-y-3 md:hidden">
      {items.map((sale) => (
        <li key={sale.id}>
          <Link
            to={`/sales/${sale.id}`}
            className={`block rounded-xl border border-slate-200 bg-white p-4 shadow-sm ${sale.status === 'VOID' ? 'opacity-70' : ''}`}
          >
            <div className="flex items-start justify-between gap-3">
              <p className="font-semibold text-slate-900">{sale.invoice_no ?? t('sales.draftLabel', { id: sale.id })}</p>
              <SaleStatusBadge status={sale.status} />
            </div>
            <p className="mt-1 text-slate-700">{sale.customer_name ?? t('sales.walkIn')}</p>
            <div className="mt-2 flex items-center justify-between text-sm text-slate-600">
              <span>{formatDate(sale.sale_date)}</span>
              <PaymentBadge type={sale.payment_type} />
              <span className="font-semibold text-slate-900">{formatMoney(sale.total_amount)}</span>
            </div>
          </Link>
        </li>
      ))}
    </ul>
  )
}
