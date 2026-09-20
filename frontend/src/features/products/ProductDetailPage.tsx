import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Pencil, Power } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { getProductHistory } from '@/api/inventory'
import { getProduct, setProductActive } from '@/api/products'
import type { Product } from '@/api/types'
import { Alert, Badge, Button, LinkButton, QueryError, Spinner } from '@/components/ui'
import { StockStatusBadge } from '@/features/inventory/StockStatusBadge'
import { formatMoney, formatQuantity, NOT_SET } from '@/lib/format'

import { OpeningStockForm } from './OpeningStockForm'
import { StockHistory } from './StockHistory'

export function ProductDetailPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const location = useLocation()
  const productId = Number(useParams().id)
  const warnings = (location.state as { warnings?: string[] } | null)?.warnings ?? []

  const product = useQuery({ queryKey: ['product', productId], queryFn: () => getProduct(productId) })
  // The opening-stock form is offered only while the product has no stock history at all.
  const history = useQuery({
    queryKey: ['history', productId, 'exists'],
    queryFn: () => getProductHistory(productId, { limit: 1 }),
  })

  const toggleActive = useMutation({
    mutationFn: (active: boolean) => setProductActive(productId, active),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['product', productId] }),
        queryClient.invalidateQueries({ queryKey: ['products'] }),
        queryClient.invalidateQueries({ queryKey: ['inventory'] }),
      ])
    },
  })

  if (product.isError) {
    const notFound = product.error instanceof ApiError && product.error.status === 404
    return (
      <div className="space-y-6">
        {notFound ? <Alert tone="error">{t('products.detail.notFound')}</Alert> : <QueryError onRetry={() => void product.refetch()} />}
        <LinkButton to="/products" variant="secondary">
          {t('products.detail.backToList')}
        </LinkButton>
      </div>
    )
  }
  if (!product.data) return <Spinner />

  const p = product.data
  const hasNoHistory = history.data?.total === 0

  function confirmAndToggle() {
    if (p.is_active && !window.confirm(t('products.detail.deactivateConfirm'))) return
    toggleActive.mutate(!p.is_active)
  }

  return (
    <div className="space-y-6">
      <Link to="/products" className="inline-flex min-h-10 items-center gap-2 text-emerald-800 hover:underline">
        <ArrowLeft aria-hidden="true" className="size-5" />
        {t('products.detail.backToList')}
      </Link>

      {warnings.length > 0 && (
        <Alert tone="warning">
          <p className="font-medium">{t('products.detail.savedWithWarnings')}</p>
          <ul className="list-disc pl-5">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Alert>
      )}
      {!p.is_active && <Alert tone="warning">{t('products.detail.inactiveNotice')}</Alert>}
      {toggleActive.isError && <Alert tone="error">{t('common.genericError')}</Alert>}

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold text-slate-900">{p.name}</h1>
            <Badge tone={p.is_active ? 'green' : 'slate'}>{p.is_active ? t('common.active') : t('common.inactive')}</Badge>
          </div>
          <p className="mt-1 text-slate-600">
            <span className="font-mono">{p.sku}</span>
            {p.brand && <> · {p.brand}</>}
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <LinkButton to={`/products/${p.id}/edit`} variant="secondary">
            <Pencil aria-hidden="true" className="size-5" />
            {t('common.edit')}
          </LinkButton>
          <Button variant={p.is_active ? 'danger' : 'primary'} loading={toggleActive.isPending} onClick={confirmAndToggle}>
            <Power aria-hidden="true" className="size-5" />
            {p.is_active ? t('products.detail.deactivate') : t('products.detail.activate')}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <StockCard product={p} />
        <DetailsCard product={p} />
      </div>

      {hasNoHistory && p.is_active && <OpeningStockForm product={p} />}
      <StockHistory product={p} />
    </div>
  )
}

function StockCard({ product }: { product: Product }) {
  const { t } = useTranslation()
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t('products.detail.stockHeading')}</h2>
      <p className="mt-3 text-4xl font-bold text-slate-900">
        {formatQuantity(product.current_stock)} <span className="text-xl font-medium text-slate-500">{product.unit_code}</span>
      </p>
      <div className="mt-3">
        <StockStatusBadge status={product.stock_status} />
      </div>
      <p className="mt-4 text-sm text-slate-500">
        {t('products.columns.reorderLevel')}: {formatQuantity(product.reorder_level)} {product.unit_code}
      </p>
      <p className="mt-1 text-sm text-slate-500">{t('products.detail.stockNote')}</p>
    </section>
  )
}

function DetailsCard({ product }: { product: Product }) {
  const { t } = useTranslation()
  const rows: [string, string][] = [
    [t('products.columns.category'), product.category_name],
    [t('products.form.fields.brand'), product.brand ?? NOT_SET],
    [t('products.form.fields.barcode'), product.barcode ?? NOT_SET],
    [t('products.columns.unit'), `${product.unit_name} (${product.unit_code})`],
    [t('products.columns.mrp'), formatMoney(product.mrp)],
    [t('products.columns.sellingPrice'), formatMoney(product.selling_price)],
    [t('products.columns.purchasePrice'), formatMoney(product.purchase_price)],
    [`${t('products.detail.avgCost')} (${t('products.detail.avgCostNote').toLowerCase()})`, formatMoney(product.avg_cost)],
    [t('products.form.fields.defaultSupplier'), NOT_SET],
  ]
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t('products.detail.details')}</h2>
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="text-sm text-slate-500">{label}</dt>
            <dd className="font-medium text-slate-900">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
