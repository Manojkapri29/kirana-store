import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { History } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'

import { listCategories } from '@/api/catalog'
import { listInventory } from '@/api/inventory'
import { listProducts } from '@/api/products'
import type { InventoryItem, StatusFilter, StockStatus } from '@/api/types'
import { ExportButtons } from '@/components/ExportButtons'
import { FilterSelect } from '@/components/fields'
import { Pagination } from '@/components/Pagination'
import { SearchInput } from '@/components/SearchInput'
import { Badge, EmptyState, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useDebounced } from '@/hooks/useDebounced'
import { formatQuantity } from '@/lib/format'

import { StockStatusBadge } from './StockStatusBadge'

const PAGE_SIZE = 25

export function InventoryPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  const [search, setSearch] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [stockStatus, setStockStatus] = useState<StockStatus | ''>('')
  const [status, setStatus] = useState<StatusFilter>('active')
  const [offset, setOffset] = useState(0)
  const q = useDebounced(search.trim())

  const categories = useQuery({ queryKey: ['categories'], queryFn: listCategories })
  const filters = {
    q,
    category_id: categoryId ? Number(categoryId) : null,
    status,
    stock_status: stockStatus || null,
  }
  const inventory = useQuery({
    queryKey: ['inventory', filters, offset],
    queryFn: () => listInventory({ ...filters, limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
  })

  const change = <T,>(setter: (value: T) => void) => (value: T) => {
    setter(value)
    setOffset(0)
  }

  // A scanned barcode + Enter opens the product when it is the only match.
  async function openIfSingleMatch() {
    const text = search.trim()
    if (!text) return
    const result = await listProducts({ q: text, status: 'all', limit: 2 })
    if (result.total === 1) void navigate(`/products/${result.items[0].id}`)
  }

  const data = inventory.data
  const STATUS_CHIPS: { value: StockStatus | ''; label: string }[] = [
    { value: '', label: t('stock.allLevels') },
    { value: 'IN_STOCK', label: t('stock.inStock') },
    { value: 'LOW_STOCK', label: t('stock.lowStock') },
    { value: 'OUT_OF_STOCK', label: t('stock.outOfStock') },
  ]

  return (
    <div className="space-y-6">
      <PageHeader title={t('inventory.title')} subtitle={t('inventory.subtitle')} />

      <div className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row">
          <SearchInput
            value={search}
            onChange={change(setSearch)}
            onEnter={() => void openIfSingleMatch()}
            placeholder={t('inventory.searchPlaceholder')}
            autoFocus
          />
          <FilterSelect
            aria-label={t('products.columns.category')}
            value={categoryId}
            onChange={(event) => change(setCategoryId)(event.target.value)}
          >
            <option value="">{t('products.allCategories')}</option>
            {categories.data?.map((category) => (
              <option key={category.id} value={category.id}>
                {category.name}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect
            aria-label={t('products.columns.status')}
            value={status}
            onChange={(event) => change(setStatus)(event.target.value as StatusFilter)}
          >
            <option value="active">{t('common.active')}</option>
            <option value="inactive">{t('common.inactive')}</option>
            <option value="all">{t('common.all')}</option>
          </FilterSelect>
        </div>

        <div className="flex flex-wrap gap-2" role="group" aria-label={t('inventory.columns.status')}>
          {STATUS_CHIPS.map((chip) => (
            <button
              key={chip.value}
              type="button"
              aria-pressed={stockStatus === chip.value}
              onClick={() => change(setStockStatus)(chip.value)}
              className={`min-h-11 rounded-full border px-4 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-emerald-600 ${
                stockStatus === chip.value
                  ? 'border-emerald-600 bg-emerald-600 text-white'
                  : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50'
              }`}
            >
              {chip.label}
            </button>
          ))}
        </div>
      </div>

      {inventory.isError && <QueryError onRetry={() => void inventory.refetch()} />}
      {inventory.isPending && <Spinner />}
      {data && data.items.length === 0 && <EmptyState title={t('inventory.empty')} />}

      {data && data.items.length > 0 && (
        <>
          <InventoryTable items={data.items} />
          <InventoryCards items={data.items} />
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
          <div className="border-t border-slate-200 pt-4">
            <ExportButtons kind="inventory" filters={filters} />
          </div>
        </>
      )}
    </div>
  )
}

function InactiveTag({ item }: { item: InventoryItem }) {
  const { t } = useTranslation()
  return item.is_active ? null : <Badge tone="slate">{t('common.inactive')}</Badge>
}

function InventoryTable({ items }: { items: InventoryItem[] }) {
  const { t } = useTranslation()
  const heading = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'

  return (
    <div className="hidden overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm md:block">
      <table className="min-w-full divide-y divide-slate-200">
        <thead className="bg-slate-50">
          <tr>
            <th className={heading}>{t('inventory.columns.product')}</th>
            <th className={heading}>{t('inventory.columns.sku')}</th>
            <th className={`${heading} text-right`}>{t('inventory.columns.currentStock')}</th>
            <th className={heading}>{t('inventory.columns.unit')}</th>
            <th className={`${heading} text-right`}>{t('inventory.columns.reorderLevel')}</th>
            <th className={heading}>{t('inventory.columns.status')}</th>
            <th className={heading} />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {items.map((item) => (
            <tr key={item.product_id} className="hover:bg-slate-50">
              <td className="min-w-44 px-4 py-3">
                <Link to={`/products/${item.product_id}`} className="font-medium text-emerald-800 hover:underline">
                  {item.name}
                </Link>{' '}
                <InactiveTag item={item} />
                {item.brand && <div className="text-sm text-slate-500">{item.brand}</div>}
              </td>
              <td className="whitespace-nowrap px-4 py-3 font-mono text-sm">{item.sku}</td>
              <td className="whitespace-nowrap px-4 py-3 text-right text-lg font-semibold">{formatQuantity(item.current_stock)}</td>
              <td className="px-4 py-3 text-sm">{item.unit_code}</td>
              <td className="whitespace-nowrap px-4 py-3 text-right">{formatQuantity(item.reorder_level)}</td>
              <td className="px-4 py-3">
                <StockStatusBadge status={item.status} />
              </td>
              <td className="px-4 py-3 text-right">
                <Link
                  to={`/products/${item.product_id}#history`}
                  title={t('inventory.viewHistory')}
                  className="inline-flex min-h-10 items-center gap-1.5 rounded-lg px-3 text-sm font-medium text-emerald-800 hover:bg-emerald-50"
                >
                  <History aria-hidden="true" className="size-4" />
                  <span className="sr-only 2xl:not-sr-only">{t('inventory.viewHistory')}</span>
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function InventoryCards({ items }: { items: InventoryItem[] }) {
  const { t } = useTranslation()
  return (
    <ul className="space-y-3 md:hidden">
      {items.map((item) => (
        <li key={item.product_id}>
          <Link
            to={`/products/${item.product_id}#history`}
            className={`block rounded-xl border border-slate-200 bg-white p-4 shadow-sm ${item.is_active ? '' : 'opacity-70'}`}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-semibold text-slate-900">{item.name}</p>
                <p className="font-mono text-sm text-slate-500">{item.sku}</p>
              </div>
              <StockStatusBadge status={item.status} />
            </div>
            <div className="mt-3 flex items-end justify-between">
              <p className="text-2xl font-bold">
                {formatQuantity(item.current_stock)} <span className="text-base font-medium text-slate-500">{item.unit_code}</span>
              </p>
              <p className="text-sm text-slate-500">
                {t('inventory.columns.reorderLevel')}: {formatQuantity(item.reorder_level)}
              </p>
            </div>
          </Link>
        </li>
      ))}
    </ul>
  )
}
