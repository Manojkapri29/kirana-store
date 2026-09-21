import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { listSuppliers } from '@/api/suppliers'
import type { StatusFilter, Supplier } from '@/api/types'
import { FilterSelect } from '@/components/fields'
import { Pagination } from '@/components/Pagination'
import { SearchInput } from '@/components/SearchInput'
import { Badge, EmptyState, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useDebounced } from '@/hooks/useDebounced'
import { NOT_SET } from '@/lib/format'

const PAGE_SIZE = 25

export function SuppliersPage() {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<StatusFilter>('active')
  const [offset, setOffset] = useState(0)
  const q = useDebounced(search.trim())

  const filters = { q, status }
  const suppliers = useQuery({
    queryKey: ['suppliers', filters, offset],
    queryFn: () => listSuppliers({ ...filters, limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
  })

  const data = suppliers.data
  const isFiltering = q !== '' || status !== 'active'
  const addButton = (
    <LinkButton to="/suppliers/new">
      <Plus aria-hidden="true" className="size-5" />
      {t('suppliers.add')}
    </LinkButton>
  )

  return (
    <div className="space-y-6">
      <PageHeader title={t('suppliers.title')} subtitle={t('suppliers.subtitle')} actions={addButton} />

      <div className="flex flex-col gap-3 sm:flex-row">
        <SearchInput
          value={search}
          onChange={(value) => {
            setSearch(value)
            setOffset(0)
          }}
          placeholder={t('suppliers.searchPlaceholder')}
          autoFocus
        />
        <FilterSelect
          aria-label={t('suppliers.columns.status')}
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as StatusFilter)
            setOffset(0)
          }}
        >
          <option value="active">{t('common.active')}</option>
          <option value="inactive">{t('common.inactive')}</option>
          <option value="all">{t('common.all')}</option>
        </FilterSelect>
      </div>

      {suppliers.isError && <QueryError error={suppliers.error} onRetry={() => void suppliers.refetch()} />}
      {suppliers.isPending && <Spinner />}

      {data && data.items.length === 0 && (
        <EmptyState
          title={isFiltering ? t('suppliers.noMatch') : t('suppliers.empty')}
          hint={isFiltering ? undefined : t('suppliers.emptyHint')}
          action={!isFiltering && addButton}
        />
      )}

      {data && data.items.length > 0 && (
        <>
          <SupplierTable items={data.items} />
          <SupplierCards items={data.items} />
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
        </>
      )}
    </div>
  )
}

function StatusBadge({ active }: { active: boolean }) {
  const { t } = useTranslation()
  return <Badge tone={active ? 'green' : 'slate'}>{active ? t('common.active') : t('common.inactive')}</Badge>
}

function SupplierTable({ items }: { items: Supplier[] }) {
  const { t } = useTranslation()
  const heading = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'

  return (
    <div className="hidden overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm md:block">
      <table className="min-w-full divide-y divide-slate-200">
        <thead className="bg-slate-50">
          <tr>
            <th className={heading}>{t('suppliers.columns.name')}</th>
            <th className={heading}>{t('suppliers.columns.phone')}</th>
            <th className={`${heading} hidden xl:table-cell`}>{t('suppliers.columns.email')}</th>
            <th className={heading}>{t('suppliers.columns.gstin')}</th>
            <th className={`${heading} text-right`}>{t('suppliers.columns.products')}</th>
            <th className={heading}>{t('suppliers.columns.status')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {items.map((supplier) => (
            <tr key={supplier.id} className={`hover:bg-slate-50 ${supplier.is_active ? '' : 'text-slate-500'}`}>
              <td className="min-w-48 px-4 py-3">
                <Link to={`/suppliers/${supplier.id}`} className="font-medium text-emerald-800 hover:underline">
                  {supplier.name}
                </Link>
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm">{supplier.phone ?? NOT_SET}</td>
              <td className="hidden px-4 py-3 text-sm xl:table-cell">{supplier.email ?? NOT_SET}</td>
              <td className="whitespace-nowrap px-4 py-3 font-mono text-sm">{supplier.gstin ?? NOT_SET}</td>
              <td className="px-4 py-3 text-right font-semibold">{supplier.product_count}</td>
              <td className="px-4 py-3">
                <StatusBadge active={supplier.is_active} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The same suppliers as tappable cards, for phones. */
function SupplierCards({ items }: { items: Supplier[] }) {
  const { t } = useTranslation()
  return (
    <ul className="space-y-3 md:hidden">
      {items.map((supplier) => (
        <li key={supplier.id}>
          <Link
            to={`/suppliers/${supplier.id}`}
            className={`block rounded-xl border border-slate-200 bg-white p-4 shadow-sm ${supplier.is_active ? '' : 'opacity-70'}`}
          >
            <div className="flex items-start justify-between gap-3">
              <p className="font-semibold text-slate-900">{supplier.name}</p>
              <StatusBadge active={supplier.is_active} />
            </div>
            <div className="mt-2 space-y-0.5 text-sm text-slate-600">
              {supplier.phone && <p>{supplier.phone}</p>}
              {supplier.email && <p className="truncate">{supplier.email}</p>}
            </div>
            <p className="mt-3 text-sm text-slate-500">
              {t('suppliers.columns.products')}: <span className="font-semibold text-slate-800">{supplier.product_count}</span>
            </p>
          </Link>
        </li>
      ))}
    </ul>
  )
}
