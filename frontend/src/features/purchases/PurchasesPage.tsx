import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'

import { listPurchases } from '@/api/purchases'
import { listSupplierOptions } from '@/api/suppliers'
import type { PurchaseStatus, PurchaseSummary } from '@/api/types'
import { ExportButtons } from '@/components/ExportButtons'
import { FilterSelect } from '@/components/fields'
import { Pagination } from '@/components/Pagination'
import { SearchInput } from '@/components/SearchInput'
import { EmptyState, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useDebounced } from '@/hooks/useDebounced'
import { formatDate, formatMoney, NOT_SET } from '@/lib/format'

import { PurchaseStatusBadge } from './PurchaseStatusBadge'

const PAGE_SIZE = 25
const DATE_INPUT =
  'block min-h-12 rounded-lg border border-slate-300 bg-white px-3 text-base focus-visible:outline-2 focus-visible:outline-emerald-600'

function purchaseLabel(p: { purchase_no: string | null; id: number }, draftText: (id: number) => string) {
  return p.purchase_no ?? draftText(p.id)
}

export function PurchasesPage() {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const [searchParams] = useSearchParams()
  const [supplierId, setSupplierId] = useState(searchParams.get('supplier') ?? '')
  const [status, setStatus] = useState<PurchaseStatus | ''>('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [offset, setOffset] = useState(0)
  const q = useDebounced(search.trim())

  const rangeInvalid = dateFrom !== '' && dateTo !== '' && dateFrom > dateTo
  const filters = {
    q,
    supplier_id: supplierId === '' ? null : Number(supplierId),
    status,
    date_from: dateFrom,
    date_to: dateTo,
  }
  const purchases = useQuery({
    queryKey: ['purchases', filters, offset],
    queryFn: () => listPurchases({ ...filters, limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
    enabled: !rangeInvalid,
  })
  const suppliers = useQuery({ queryKey: ['supplierOptions'], queryFn: listSupplierOptions })

  const data = purchases.data
  const isFiltering = q !== '' || supplierId !== '' || status !== '' || dateFrom !== '' || dateTo !== ''
  const addButton = (
    <LinkButton to="/purchases/new">
      <Plus aria-hidden="true" className="size-5" />
      {t('purchases.add')}
    </LinkButton>
  )

  function change<T>(set: (value: T) => void) {
    return (value: T) => {
      set(value)
      setOffset(0)
    }
  }
  const exportFilters = {
    q,
    supplier_id: filters.supplier_id,
    status: status || undefined,
    date_from: dateFrom,
    date_to: dateTo,
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t('purchases.title')} subtitle={t('purchases.subtitle')} actions={addButton} />

      <div className="flex flex-col gap-3 xl:flex-row">
        <SearchInput
          value={search}
          onChange={change(setSearch)}
          placeholder={t('purchases.searchPlaceholder')}
          autoFocus
        />
        <FilterSelect
          aria-label={t('purchases.filters.supplier')}
          value={supplierId}
          onChange={(event) => change(setSupplierId)(event.target.value)}
        >
          <option value="">{t('purchases.filters.allSuppliers')}</option>
          {suppliers.data?.map((supplier) => (
            <option key={supplier.id} value={supplier.id}>
              {supplier.name}
            </option>
          ))}
        </FilterSelect>
        <FilterSelect
          aria-label={t('purchases.filters.status')}
          value={status}
          onChange={(event) => change(setStatus)(event.target.value as PurchaseStatus | '')}
        >
          <option value="">{t('purchases.filters.allStatuses')}</option>
          <option value="DRAFT">{t('purchases.status.DRAFT')}</option>
          <option value="POSTED">{t('purchases.status.POSTED')}</option>
          <option value="VOID">{t('purchases.status.VOID')}</option>
        </FilterSelect>
      </div>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <label className="flex items-center gap-2 text-sm text-slate-700">
          {t('purchases.filters.from')}
          <input
            type="date"
            value={dateFrom}
            onChange={(event) => change(setDateFrom)(event.target.value)}
            className={DATE_INPUT}
          />
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          {t('purchases.filters.to')}
          <input
            type="date"
            value={dateTo}
            onChange={(event) => change(setDateTo)(event.target.value)}
            className={DATE_INPUT}
          />
        </label>
        {rangeInvalid && <p role="alert" className="text-sm font-medium text-red-700">{t('purchases.filters.rangeInvalid')}</p>}
      </div>

      {purchases.isError && <QueryError onRetry={() => void purchases.refetch()} />}
      {purchases.isPending && !rangeInvalid && <Spinner />}

      {data && data.items.length === 0 && (
        <EmptyState
          title={isFiltering ? t('purchases.noMatch') : t('purchases.empty')}
          hint={isFiltering ? undefined : t('purchases.emptyHint')}
          action={!isFiltering && addButton}
        />
      )}

      {data && data.items.length > 0 && (
        <>
          <PurchaseTable items={data.items} />
          <PurchaseCards items={data.items} />
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
          <div className="space-y-4 border-t border-slate-200 pt-4">
            <div>
              <p className="mb-2 text-sm font-medium text-slate-700">{t('purchases.exportList')}</p>
              <ExportButtons kind="purchases" filters={exportFilters} />
            </div>
            <div>
              <p className="mb-2 text-sm font-medium text-slate-700">{t('purchases.exportItems')}</p>
              <ExportButtons kind="purchase-items" filters={exportFilters} />
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function PurchaseTable({ items }: { items: PurchaseSummary[] }) {
  const { t } = useTranslation()
  const heading = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'
  const draftText = (id: number) => t('purchases.draftLabel', { id })

  return (
    <div className="hidden overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm md:block">
      <table className="min-w-full divide-y divide-slate-200">
        <thead className="bg-slate-50">
          <tr>
            <th className={heading}>{t('purchases.columns.number')}</th>
            <th className={heading}>{t('purchases.columns.date')}</th>
            <th className={heading}>{t('purchases.columns.supplier')}</th>
            <th className={`${heading} hidden xl:table-cell`}>{t('purchases.columns.invoice')}</th>
            <th className={`${heading} text-right`}>{t('purchases.columns.items')}</th>
            <th className={`${heading} text-right`}>{t('purchases.columns.total')}</th>
            <th className={heading}>{t('purchases.columns.status')}</th>
            <th className={`${heading} hidden xl:table-cell`}>{t('purchases.columns.createdBy')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {items.map((purchase) => (
            <tr key={purchase.id} className={`hover:bg-slate-50 ${purchase.status === 'VOID' ? 'text-slate-500' : ''}`}>
              <td className="whitespace-nowrap px-4 py-3">
                <Link to={`/purchases/${purchase.id}`} className="font-medium text-emerald-800 hover:underline">
                  {purchaseLabel(purchase, draftText)}
                </Link>
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm">{formatDate(purchase.purchase_date)}</td>
              <td className="min-w-40 px-4 py-3">{purchase.supplier_name}</td>
              <td className="hidden px-4 py-3 text-sm xl:table-cell">{purchase.supplier_invoice_no ?? NOT_SET}</td>
              <td className="px-4 py-3 text-right">{purchase.item_count}</td>
              <td className="whitespace-nowrap px-4 py-3 text-right font-semibold">{formatMoney(purchase.total_amount)}</td>
              <td className="px-4 py-3">
                <PurchaseStatusBadge status={purchase.status} />
              </td>
              <td className="hidden whitespace-nowrap px-4 py-3 text-sm xl:table-cell">{purchase.created_by_name}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The same purchases as tappable cards, for phones. */
function PurchaseCards({ items }: { items: PurchaseSummary[] }) {
  const { t } = useTranslation()
  const draftText = (id: number) => t('purchases.draftLabel', { id })
  return (
    <ul className="space-y-3 md:hidden">
      {items.map((purchase) => (
        <li key={purchase.id}>
          <Link
            to={`/purchases/${purchase.id}`}
            className={`block rounded-xl border border-slate-200 bg-white p-4 shadow-sm ${purchase.status === 'VOID' ? 'opacity-70' : ''}`}
          >
            <div className="flex items-start justify-between gap-3">
              <p className="font-semibold text-slate-900">{purchaseLabel(purchase, draftText)}</p>
              <PurchaseStatusBadge status={purchase.status} />
            </div>
            <p className="mt-1 text-slate-700">{purchase.supplier_name}</p>
            <div className="mt-2 flex items-center justify-between text-sm text-slate-600">
              <span>{formatDate(purchase.purchase_date)}</span>
              <span>
                {t('purchases.itemsCount', { count: purchase.item_count })}
              </span>
              <span className="font-semibold text-slate-900">{formatMoney(purchase.total_amount)}</span>
            </div>
          </Link>
        </li>
      ))}
    </ul>
  )
}
