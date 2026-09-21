import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Ban, CircleCheck, Copy, Pencil, Undo2 } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { correctPurchase, getPurchase, postPurchase, voidPurchase } from '@/api/purchases'
import type { Purchase, PurchaseItem } from '@/api/types'
import { ExportButtons } from '@/components/ExportButtons'
import { TextAreaField } from '@/components/fields'
import { Alert, Button, LinkButton, QueryError, Spinner } from '@/components/ui'
import { formatDate, formatDateTime, formatMoney, formatQuantity, NOT_SET } from '@/lib/format'

import { ReturnsOfDocument } from '../returns/ReturnsOfDocument'
import { invalidatePurchaseData } from './invalidate'
import { PurchaseStatusBadge } from './PurchaseStatusBadge'

export function PurchaseDetailPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const purchaseId = Number(useParams().id)
  const validId = Number.isInteger(purchaseId) && purchaseId > 0
  const postErrorFromForm = (location.state as { postError?: string } | null)?.postError
  const [showVoid, setShowVoid] = useState(false)

  const purchase = useQuery({ queryKey: ['purchase', purchaseId], queryFn: () => getPurchase(purchaseId), enabled: validId })

  const post = useMutation({
    mutationFn: () => postPurchase(purchaseId),
    onSuccess: () => invalidatePurchaseData(queryClient),
  })
  const correct = useMutation({
    mutationFn: () => correctPurchase(purchaseId),
    onSuccess: async (copy) => {
      await invalidatePurchaseData(queryClient)
      void navigate(`/purchases/${copy.id}/edit`)
    },
  })

  if (!validId || purchase.isError) {
    const notFound = !validId || (purchase.error instanceof ApiError && purchase.error.status === 404)
    return (
      <div className="space-y-6">
        {notFound ? <Alert tone="error">{t('purchases.detail.notFound')}</Alert> : <QueryError error={purchase.error} onRetry={() => void purchase.refetch()} />}
        <LinkButton to="/purchases" variant="secondary">
          {t('purchases.detail.backToList')}
        </LinkButton>
      </div>
    )
  }
  if (!purchase.data) return <Spinner />

  const p = purchase.data
  const title = p.purchase_no ?? t('purchases.draftLabel', { id: p.id })
  const mutationError = post.error ?? correct.error
  const problem = postErrorFromForm ?? (mutationError instanceof ApiError ? mutationError.message : mutationError ? t('common.genericError') : null)

  function confirmAndPost() {
    if (window.confirm(t('purchases.form.postConfirm'))) post.mutate()
  }

  return (
    <div className="space-y-6">
      <Link to="/purchases" className="inline-flex min-h-10 items-center gap-2 text-emerald-800 hover:underline">
        <ArrowLeft aria-hidden="true" className="size-5" />
        {t('purchases.detail.backToList')}
      </Link>

      {problem && (
        <Alert tone="error">
          <p className="font-medium">{postErrorFromForm ? t('purchases.detail.draftSavedNotPosted') : t('purchases.detail.actionFailed')}</p>
          <p>{problem}</p>
        </Alert>
      )}
      {p.status === 'DRAFT' && <Alert tone="info">{t('purchases.detail.draftNotice')}</Alert>}
      {p.status === 'VOID' && (
        <Alert tone="warning">
          <p className="font-medium">{p.purchase_no ? t('purchases.detail.voidNotice') : t('purchases.detail.discardedNotice')}</p>
          <p>
            {t('purchases.detail.voidReason')}: {p.void_reason}
          </p>
          {p.replaced_by_id && (
            <p>
              <Link to={`/purchases/${p.replaced_by_id}`} className="font-medium underline">
                {t('purchases.detail.viewCorrectedCopy')}
              </Link>
            </p>
          )}
        </Alert>
      )}
      {p.replaces_id && (
        <Alert tone="info">
          <Link to={`/purchases/${p.replaces_id}`} className="font-medium underline">
            {t('purchases.detail.viewOriginal')}
          </Link>
        </Alert>
      )}

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold text-slate-900">{title}</h1>
          <PurchaseStatusBadge status={p.status} />
        </div>
        <div className="flex flex-wrap gap-3">
          {p.status === 'DRAFT' && (
            <>
              <LinkButton to={`/purchases/${p.id}/edit`} variant="secondary">
                <Pencil aria-hidden="true" className="size-5" />
                {t('common.edit')}
              </LinkButton>
              <Button requires="PURCHASE_POST" loading={post.isPending} disabled={p.items.length === 0} onClick={confirmAndPost}>
                <CircleCheck aria-hidden="true" className="size-5" />
                {t('purchases.detail.post')}
              </Button>
              <Button requires="PURCHASE_VOID" variant="danger" onClick={() => setShowVoid((open) => !open)}>
                <Ban aria-hidden="true" className="size-5" />
                {t('purchases.detail.discard')}
              </Button>
            </>
          )}
          {p.status === 'POSTED' && (
            <LinkButton to={`/purchases/${p.id}/return`} variant="secondary">
              <Undo2 aria-hidden="true" className="size-5" />
              {t('returns.returnItems')}
            </LinkButton>
          )}
          {p.status === 'POSTED' && (
            <Button requires="PURCHASE_VOID" variant="danger" onClick={() => setShowVoid((open) => !open)}>
              <Ban aria-hidden="true" className="size-5" />
              {t('purchases.detail.void')}
            </Button>
          )}
          {p.status === 'VOID' && p.purchase_no && !p.replaced_by_id && (
            <Button loading={correct.isPending} onClick={() => correct.mutate()}>
              <Copy aria-hidden="true" className="size-5" />
              {t('purchases.detail.correct')}
            </Button>
          )}
        </div>
      </div>

      {showVoid && <VoidForm purchase={p} onDone={() => setShowVoid(false)} />}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card title={t('purchases.detail.summary')}>
          <Row label={t('purchases.columns.supplier')}>
            <Link to={`/suppliers/${p.supplier_id}`} className="text-emerald-800 hover:underline">
              {p.supplier_name}
            </Link>
          </Row>
          <Row label={t('purchases.form.fields.date')}>{formatDate(p.purchase_date)}</Row>
          <Row label={t('purchases.form.fields.invoiceNo')}>{p.supplier_invoice_no ?? NOT_SET}</Row>
          <Row label={t('purchases.form.fields.notes')}>{p.notes ?? NOT_SET}</Row>
        </Card>
        <Card title={t('purchases.detail.record')}>
          <Row label={t('purchases.detail.createdBy')}>
            {p.created_by_name}, {formatDateTime(p.created_at)}
          </Row>
          <Row label={t('purchases.detail.postedBy')}>
            {p.posted_at ? `${p.posted_by_name ?? ''}, ${formatDateTime(p.posted_at)}` : NOT_SET}
          </Row>
          <Row label={t('purchases.detail.total')}>
            <span className="text-xl font-bold">{formatMoney(p.total_amount)}</span>
          </Row>
        </Card>
      </div>

      <ItemsSection purchase={p} />
      {p.status === 'POSTED' && <ReturnsOfDocument kind="purchase" id={p.id} />}

      {p.status !== 'DRAFT' && (
        <div className="border-t border-slate-200 pt-4">
          <p className="mb-2 text-sm font-medium text-slate-700">{t('purchases.detail.exportThis')}</p>
          <ExportButtons kind={`purchases/${p.id}`} />
        </div>
      )}
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

/** Ask for the reason, then void (a posted purchase) or discard (a draft). */
function VoidForm({ purchase, onDone }: { purchase: Purchase; onDone: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const isDraft = purchase.status === 'DRAFT'

  const run = useMutation({
    mutationFn: () => voidPurchase(purchase.id, reason.trim()),
    onSuccess: async () => {
      await invalidatePurchaseData(queryClient)
      onDone()
    },
    onError: (failure) => setError(failure instanceof ApiError ? (failure.fieldErrors.reason ?? failure.message) : t('common.genericError')),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (reason.trim() === '') {
      setError(t('purchases.detail.reasonRequired'))
      return
    }
    run.mutate()
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4 rounded-xl border border-red-200 bg-red-50 p-5">
      <p className="font-medium text-red-900">{isDraft ? t('purchases.detail.discardWarning') : t('purchases.detail.voidWarning')}</p>
      <TextAreaField
        label={t('purchases.detail.reasonLabel')}
        value={reason}
        onChange={(event) => {
          setReason(event.target.value)
          setError(null)
        }}
        error={error}
        maxLength={500}
        autoFocus
      />
      <div className="flex flex-wrap gap-3">
        <Button type="submit" variant="danger" loading={run.isPending}>
          {isDraft ? t('purchases.detail.confirmDiscard') : t('purchases.detail.confirmVoid')}
        </Button>
        <Button variant="secondary" onClick={onDone} disabled={run.isPending}>
          {t('common.cancel')}
        </Button>
      </div>
    </form>
  )
}

function ItemsSection({ purchase }: { purchase: Purchase }) {
  const { t } = useTranslation()
  const posted = purchase.status !== 'DRAFT' && purchase.purchase_no !== null
  const heading = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-slate-900">
        {t('purchases.detail.items')} ({purchase.items.length})
      </h2>
      {purchase.items.length === 0 && <Alert tone="info">{t('purchases.detail.noItems')}</Alert>}
      {purchase.items.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200">
            <thead className="bg-slate-50">
              <tr>
                <th className={heading}>{t('purchases.form.fields.product')}</th>
                <th className={`${heading} text-right`}>{t('purchases.form.fields.quantity')}</th>
                <th className={`${heading} text-right`}>{t('purchases.form.fields.price')}</th>
                <th className={`${heading} text-right`}>{t('purchases.form.fields.discount')}</th>
                <th className={`${heading} text-right`}>{t('purchases.form.fields.lineTotal')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {purchase.items.map((item) => (
                <tr key={item.id}>
                  <td className="min-w-48 px-4 py-3">
                    <Link to={`/products/${item.product_id}`} className="font-medium text-emerald-800 hover:underline">
                      {item.product_name}
                    </Link>
                    <div className="font-mono text-xs text-slate-500">{item.sku}</div>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right">
                    {formatQuantity(item.quantity)} {item.unit_code}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right">{formatMoney(item.unit_cost)}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-right">{formatMoney(item.discount)}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-right font-semibold">{formatMoney(item.line_total)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="bg-slate-50">
                <td colSpan={4} className="px-4 py-3 text-right font-medium text-slate-700">
                  {t('purchases.detail.total')}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-right text-lg font-bold">{formatMoney(purchase.total_amount)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {posted && purchase.items.length > 0 && <Effects purchase={purchase} />}
    </section>
  )
}

/** What posting did to stock and to the average cost of each product. */
function Effects({ purchase }: { purchase: Purchase }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-base font-semibold text-slate-900">{t('purchases.detail.effectsHeading')}</h3>
        <p className="text-sm text-slate-500">{t('purchases.detail.effectsHint')}</p>
      </div>
      <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white shadow-sm">
        {purchase.items.map((item) => (
          <li key={item.id} className="space-y-1 px-4 py-3">
            <p className="font-medium text-slate-900">{item.product_name}</p>
            <EffectLines item={item} />
          </li>
        ))}
      </ul>
    </div>
  )
}

function EffectLines({ item }: { item: PurchaseItem }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-0.5 text-sm text-slate-700">
      <p>
        {t('purchases.detail.stockEffect')}: {formatQuantity(item.stock_before)} → {formatQuantity(item.stock_after)} {item.unit_code}
      </p>
      <p>
        {t('purchases.detail.costEffect')}: {formatMoney(item.avg_cost_before)} → {formatMoney(item.avg_cost_after)}
        {item.avg_cost_after === null && <span className="ml-2 text-slate-500">({t('purchases.detail.costUnknown')})</span>}
      </p>
      <p className="text-slate-500">
        {item.inventory_effects
          .map((e) => `${t(`history.types.${e.txn_type}`)} ${e.qty_delta.startsWith('-') ? '' : '+'}${formatQuantity(e.qty_delta)} ${item.unit_code}`)
          .join(' · ')}
      </p>
    </div>
  )
}
