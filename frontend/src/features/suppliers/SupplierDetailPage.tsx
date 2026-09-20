import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Pencil, Power } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { listProducts } from '@/api/products'
import { getSupplier, setSupplierActive } from '@/api/suppliers'
import type { Supplier } from '@/api/types'
import { Alert, Badge, Button, LinkButton, QueryError, Spinner } from '@/components/ui'
import { StockStatusBadge } from '@/features/inventory/StockStatusBadge'
import { formatMoney, formatQuantity, NOT_SET } from '@/lib/format'

export function SupplierDetailPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const location = useLocation()
  const supplierId = Number(useParams().id)
  const validId = Number.isInteger(supplierId) && supplierId > 0
  const warnings = (location.state as { warnings?: string[] } | null)?.warnings ?? []

  const supplier = useQuery({
    queryKey: ['supplier', supplierId],
    queryFn: () => getSupplier(supplierId),
    enabled: validId,
  })

  const toggleActive = useMutation({
    mutationFn: (active: boolean) => setSupplierActive(supplierId, active),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['supplier', supplierId] }),
        queryClient.invalidateQueries({ queryKey: ['suppliers'] }),
        queryClient.invalidateQueries({ queryKey: ['supplierOptions'] }),
      ])
    },
  })

  if (!validId || supplier.isError) {
    const notFound = !validId || (supplier.error instanceof ApiError && supplier.error.status === 404)
    return (
      <div className="space-y-6">
        {notFound ? <Alert tone="error">{t('suppliers.detail.notFound')}</Alert> : <QueryError onRetry={() => void supplier.refetch()} />}
        <LinkButton to="/suppliers" variant="secondary">
          {t('suppliers.detail.backToList')}
        </LinkButton>
      </div>
    )
  }
  if (!supplier.data) return <Spinner />

  const s = supplier.data

  function confirmAndToggle() {
    if (s.is_active && !window.confirm(t('suppliers.detail.deactivateConfirm'))) return
    toggleActive.mutate(!s.is_active)
  }

  return (
    <div className="space-y-6">
      <Link to="/suppliers" className="inline-flex min-h-10 items-center gap-2 text-emerald-800 hover:underline">
        <ArrowLeft aria-hidden="true" className="size-5" />
        {t('suppliers.detail.backToList')}
      </Link>

      {warnings.length > 0 && (
        <Alert tone="warning">
          <p className="font-medium">{t('suppliers.detail.savedWithWarnings')}</p>
          <ul className="list-disc pl-5">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Alert>
      )}
      {!s.is_active && <Alert tone="warning">{t('suppliers.detail.inactiveNotice')}</Alert>}
      {toggleActive.isError && <Alert tone="error">{t('common.genericError')}</Alert>}

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold text-slate-900">{s.name}</h1>
          <Badge tone={s.is_active ? 'green' : 'slate'}>{s.is_active ? t('common.active') : t('common.inactive')}</Badge>
        </div>
        <div className="flex flex-wrap gap-3">
          <LinkButton to={`/suppliers/${s.id}/edit`} variant="secondary">
            <Pencil aria-hidden="true" className="size-5" />
            {t('common.edit')}
          </LinkButton>
          <Button variant={s.is_active ? 'danger' : 'primary'} loading={toggleActive.isPending} onClick={confirmAndToggle}>
            <Power aria-hidden="true" className="size-5" />
            {s.is_active ? t('suppliers.detail.deactivate') : t('suppliers.detail.activate')}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <ContactCard supplier={s} />
        <BusinessCard supplier={s} />
      </div>

      <SuppliedProducts supplier={s} />
      <UpcomingSections />
    </div>
  )
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{title}</h2>
      <dl className="mt-3 space-y-3">{children}</dl>
    </section>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-sm text-slate-500">{label}</dt>
      <dd className="whitespace-pre-line font-medium text-slate-900">{children}</dd>
    </div>
  )
}

function ContactCard({ supplier }: { supplier: Supplier }) {
  const { t } = useTranslation()
  return (
    <Card title={t('suppliers.detail.contact')}>
      <Row label={t('suppliers.form.fields.phone')}>{supplier.phone ?? NOT_SET}</Row>
      <Row label={t('suppliers.form.fields.alternatePhone')}>{supplier.alternate_phone ?? NOT_SET}</Row>
      <Row label={t('suppliers.form.fields.email')}>{supplier.email ?? NOT_SET}</Row>
      <Row label={t('suppliers.form.fields.address')}>{supplier.address ?? NOT_SET}</Row>
    </Card>
  )
}

function BusinessCard({ supplier }: { supplier: Supplier }) {
  const { t } = useTranslation()
  return (
    <Card title={t('suppliers.detail.businessDetails')}>
      <Row label={t('suppliers.form.fields.gstin')}>{supplier.gstin ?? NOT_SET}</Row>
      <Row label={t('suppliers.detail.notes')}>{supplier.notes ?? NOT_SET}</Row>
    </Card>
  )
}

/** Products that name this supplier as their default supplier. Purchases will extend this in a later phase. */
function SuppliedProducts({ supplier }: { supplier: Supplier }) {
  const { t } = useTranslation()
  const products = useQuery({
    queryKey: ['products', { supplier_id: supplier.id, status: 'all' }],
    queryFn: () => listProducts({ supplier_id: supplier.id, status: 'all', limit: 100 }),
  })

  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">
          {t('suppliers.detail.productsHeading')} ({supplier.product_count})
        </h2>
        <p className="text-sm text-slate-500">{t('suppliers.detail.productsHint')}</p>
      </div>

      {products.isError && <QueryError onRetry={() => void products.refetch()} />}
      {products.isPending && <Spinner />}
      {products.data && products.data.items.length === 0 && (
        <p className="rounded-xl border border-dashed border-slate-300 bg-white px-4 py-6 text-center text-slate-600">
          {t('suppliers.detail.noProducts')}
        </p>
      )}
      {products.data && products.data.items.length > 0 && (
        <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white shadow-sm">
          {products.data.items.map((product) => (
            <li key={product.id}>
              <Link to={`/products/${product.id}`} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 hover:bg-slate-50">
                <span className="min-w-0">
                  <span className="block font-medium text-emerald-800">{product.name}</span>
                  <span className="font-mono text-sm text-slate-500">{product.sku}</span>
                  {!product.is_active && <span className="ml-2 text-sm text-slate-500">({t('common.inactive')})</span>}
                </span>
                <span className="flex items-center gap-4 text-sm">
                  <span>{formatMoney(product.purchase_price)}</span>
                  <span className="font-semibold">
                    {formatQuantity(product.current_stock)} {product.unit_code}
                  </span>
                  <StockStatusBadge status={product.stock_status} />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** Sections that later phases will fill in. Nothing is faked: each says plainly that it is not built yet. */
function UpcomingSections() {
  const { t } = useTranslation()
  const sections = [
    { title: t('suppliers.detail.purchaseHistory'), hint: t('suppliers.detail.purchaseHistoryHint'), phase: 5 },
    { title: t('suppliers.detail.purchaseReturns'), hint: t('suppliers.detail.purchaseReturnsHint'), phase: 9 },
    { title: t('suppliers.detail.payments'), hint: t('suppliers.detail.paymentsHint'), phase: 5 },
  ]
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-slate-900">{t('suppliers.detail.upcoming')}</h2>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {sections.map((section) => (
          <div key={section.title} className="rounded-xl border border-dashed border-slate-300 bg-white p-4">
            <p className="font-medium text-slate-800">{section.title}</p>
            <p className="mt-1 text-sm text-slate-500">{section.hint}</p>
            <p className="mt-3 text-xs font-medium text-slate-400">{t('comingSoon.badge', { phase: section.phase })}</p>
          </div>
        ))}
      </div>
    </section>
  )
}
