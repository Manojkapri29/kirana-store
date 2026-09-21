import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Camera, Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { listCategories } from '@/api/catalog'
import { listProducts } from '@/api/products'
import type { Product, StatusFilter } from '@/api/types'
import { ExportButtons } from '@/components/ExportButtons'
import { FilterSelect } from '@/components/fields'
import { Pagination } from '@/components/Pagination'
import { SearchInput } from '@/components/SearchInput'
import { Badge, EmptyState, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { StockStatusBadge } from '@/features/inventory/StockStatusBadge'
import { useEntitlements } from '@/features/subscription/useEntitlements'
import { useDebounced } from '@/hooks/useDebounced'
import { useOpenOnSingleMatch } from '@/hooks/useOpenOnSingleMatch'
import { formatMoney, formatQuantity } from '@/lib/format'

const PAGE_SIZE = 25

export function ProductsPage() {
  const { t } = useTranslation()
  const { allows } = useEntitlements()

  const [search, setSearch] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [status, setStatus] = useState<StatusFilter>('active')
  const [offset, setOffset] = useState(0)
  const q = useDebounced(search.trim())

  const categories = useQuery({ queryKey: ['categories'], queryFn: listCategories })
  const filters = { q, category_id: categoryId ? Number(categoryId) : null, status }
  const products = useQuery({
    queryKey: ['products', filters, offset],
    queryFn: () => listProducts({ ...filters, limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
  })

  // Filters change the result set, so go back to the first page.
  const change = <T,>(setter: (value: T) => void) => (value: T) => {
    setter(value)
    setOffset(0)
  }

  const openIfSingleMatch = useOpenOnSingleMatch()

  const data = products.data
  const isFiltering = q !== '' || categoryId !== '' || status !== 'active'

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('products.title')}
        subtitle={t('products.subtitle')}
        actions={
          <>
            {allows('image_intelligence') !== false && (
              <LinkButton to="/products/from-photo" variant="secondary">
                <Camera aria-hidden="true" className="size-5" />
                {t('photo.addFromPhoto')}
              </LinkButton>
            )}
            <LinkButton to="/products/new">
              <Plus aria-hidden="true" className="size-5" />
              {t('products.add')}
            </LinkButton>
          </>
        }
      />

      <div className="space-y-2">
        <div className="flex flex-col gap-3 lg:flex-row">
          <SearchInput
            value={search}
            onChange={change(setSearch)}
            onEnter={() => void openIfSingleMatch(search)}
            placeholder={t('products.searchPlaceholder')}
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
        <p className="text-sm text-slate-500">{t('products.searchHint')}</p>
      </div>

      {products.isError && <QueryError error={products.error} onRetry={() => void products.refetch()} />}
      {products.isPending && <Spinner />}

      {data && data.items.length === 0 && (
        <EmptyState
          title={isFiltering ? t('products.noMatch') : t('products.empty')}
          hint={isFiltering ? undefined : t('products.emptyHint')}
          action={
            !isFiltering && (
              <LinkButton to="/products/new">
                <Plus aria-hidden="true" className="size-5" />
                {t('products.add')}
              </LinkButton>
            )
          }
        />
      )}

      {data && data.items.length > 0 && (
        <>
          <ProductTable items={data.items} />
          <ProductCards items={data.items} />
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
          <div className="border-t border-slate-200 pt-4">
            <ExportButtons kind="products" filters={filters} />
          </div>
        </>
      )}
    </div>
  )
}

function StatusBadge({ active }: { active: boolean }) {
  const { t } = useTranslation()
  return <Badge tone={active ? 'green' : 'slate'}>{active ? t('common.active') : t('common.inactive')}</Badge>
}

function ProductTable({ items }: { items: Product[] }) {
  const { t } = useTranslation()
  const heading = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'
  const right = 'text-right'

  return (
    <div className="hidden overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm md:block">
      <table className="min-w-full divide-y divide-slate-200">
        <thead className="bg-slate-50">
          <tr>
            <th className={heading}>{t('products.columns.sku')}</th>
            <th className={heading}>{t('products.columns.product')}</th>
            <th className={heading}>{t('products.columns.category')}</th>
            <th className={heading}>{t('products.columns.unit')}</th>
            <th className={`${heading} ${right}`}>{t('products.columns.mrp')}</th>
            <th className={`${heading} ${right}`}>{t('products.columns.sellingPrice')}</th>
            <th className={`${heading} ${right}`}>{t('products.columns.purchasePrice')}</th>
            <th className={`${heading} ${right}`}>{t('products.columns.currentStock')}</th>
            <th className={`${heading} ${right}`}>{t('products.columns.reorderLevel')}</th>
            <th className={heading}>{t('products.columns.status')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {items.map((product) => (
            <tr key={product.id} className={`hover:bg-slate-50 ${product.is_active ? '' : 'text-slate-500'}`}>
              <td className="whitespace-nowrap px-4 py-3 font-mono text-sm">{product.sku}</td>
              <td className="min-w-44 px-4 py-3">
                <Link to={`/products/${product.id}`} className="font-medium text-emerald-800 hover:underline">
                  {product.name}
                </Link>
                {product.brand && <div className="text-sm text-slate-500">{product.brand}</div>}
              </td>
              <td className="px-4 py-3 text-sm">{product.category_name}</td>
              <td className="px-4 py-3 text-sm">{product.unit_code}</td>
              <td className={`whitespace-nowrap px-4 py-3 ${right}`}>{formatMoney(product.mrp)}</td>
              <td className={`whitespace-nowrap px-4 py-3 font-medium ${right}`}>{formatMoney(product.selling_price)}</td>
              <td className={`whitespace-nowrap px-4 py-3 ${right}`}>{formatMoney(product.purchase_price)}</td>
              <td className={`whitespace-nowrap px-4 py-3 ${right}`}>
                <div className="flex items-center justify-end gap-2">
                  <span className="font-semibold">{formatQuantity(product.current_stock)}</span>
                  <StockStatusBadge status={product.stock_status} />
                </div>
              </td>
              <td className={`whitespace-nowrap px-4 py-3 ${right}`}>{formatQuantity(product.reorder_level)}</td>
              <td className="px-4 py-3">
                <StatusBadge active={product.is_active} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The same products as tappable cards, for phones. */
function ProductCards({ items }: { items: Product[] }) {
  const { t } = useTranslation()
  return (
    <ul className="space-y-3 md:hidden">
      {items.map((product) => (
        <li key={product.id}>
          <Link
            to={`/products/${product.id}`}
            className={`block rounded-xl border border-slate-200 bg-white p-4 shadow-sm ${product.is_active ? '' : 'opacity-70'}`}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-semibold text-slate-900">{product.name}</p>
                <p className="text-sm text-slate-500">
                  <span className="font-mono">{product.sku}</span> · {product.category_name}
                </p>
              </div>
              <StatusBadge active={product.is_active} />
            </div>
            <div className="mt-3 flex items-end justify-between gap-3">
              <div>
                <p className="text-xs text-slate-500">{t('products.columns.sellingPrice')}</p>
                <p className="text-lg font-semibold">{formatMoney(product.selling_price)}</p>
                {product.mrp && (
                  <p className="text-xs text-slate-500">
                    {t('products.columns.mrp')} {formatMoney(product.mrp)}
                  </p>
                )}
              </div>
              <div className="text-right">
                <p className="text-xs text-slate-500">{t('products.columns.currentStock')}</p>
                <p className="text-lg font-semibold">
                  {formatQuantity(product.current_stock)} <span className="text-sm font-normal">{product.unit_code}</span>
                </p>
                <StockStatusBadge status={product.stock_status} />
              </div>
            </div>
          </Link>
        </li>
      ))}
    </ul>
  )
}
