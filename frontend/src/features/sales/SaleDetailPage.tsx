import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Ban, CircleCheck, Copy, Pencil } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { getShop } from '@/api/catalog'
import { correctSale, getSale, postSale, voidSale } from '@/api/sales'
import type { Sale, SaleItem } from '@/api/types'
import { ExportButtons } from '@/components/ExportButtons'
import { TextAreaField } from '@/components/fields'
import { Alert, Button, LinkButton, QueryError, Spinner } from '@/components/ui'
import { formatDate, formatDateTime, formatMoney, formatQuantity, NOT_SET } from '@/lib/format'

import { invalidateSaleData } from './invalidate'
import { PaymentPanel } from './PaymentPanel'
import { SaleStatusBadge } from './SaleStatusBadge'
import { amountPaidFor, emptyPayment, itemPayload, paymentPayload, partAmountProblem, type PaymentValue } from './saleForm'
import { useSalePreview } from './useSalePreview'

export function SaleDetailPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const saleId = Number(useParams().id)
  const validId = Number.isInteger(saleId) && saleId > 0
  const [panel, setPanel] = useState<'post' | 'void' | null>(null)

  const sale = useQuery({ queryKey: ['sale', saleId], queryFn: () => getSale(saleId), enabled: validId })
  const correct = useMutation({
    mutationFn: () => correctSale(saleId),
    onSuccess: async (copy) => {
      await invalidateSaleData(queryClient)
      void navigate(`/sales/${copy.id}/edit`)
    },
  })

  if (!validId || sale.isError) {
    const notFound = !validId || (sale.error instanceof ApiError && sale.error.status === 404)
    return (
      <div className="space-y-6">
        {notFound ? <Alert tone="error">{t('sales.detail.notFound')}</Alert> : <QueryError onRetry={() => void sale.refetch()} />}
        <LinkButton to="/sales" variant="secondary">
          {t('sales.detail.backToList')}
        </LinkButton>
      </div>
    )
  }
  if (!sale.data) return <Spinner />

  const s = sale.data
  const title = s.invoice_no ?? t('sales.draftLabel', { id: s.id })
  const actionError = correct.error instanceof ApiError ? correct.error.message : correct.error ? t('common.genericError') : null

  return (
    <div className="space-y-6">
      <Link to="/sales" className="inline-flex min-h-10 items-center gap-2 text-emerald-800 hover:underline">
        <ArrowLeft aria-hidden="true" className="size-5" />
        {t('sales.detail.backToList')}
      </Link>

      {actionError && <Alert tone="error">{actionError}</Alert>}
      {s.warnings.map((warning) => (
        <Alert key={warning} tone="warning">
          {warning}
        </Alert>
      ))}
      {s.status === 'DRAFT' && <Alert tone="info">{t('sales.detail.draftNotice')}</Alert>}
      {s.status === 'DRAFT' && s.promotions_out_of_date && <Alert tone="warning">{t('billing.outOfDate')}</Alert>}
      {s.status === 'VOID' && (
        <Alert tone="warning">
          <p className="font-medium">{s.invoice_no ? t('sales.detail.voidNotice') : t('sales.detail.discardedNotice')}</p>
          <p>
            {t('sales.detail.voidReason')}: {s.void_reason}
          </p>
          {s.replaced_by_id && (
            <p>
              <Link to={`/sales/${s.replaced_by_id}`} className="font-medium underline">
                {t('sales.detail.viewCorrectedCopy')}
              </Link>
            </p>
          )}
        </Alert>
      )}
      {s.replaces_id && (
        <Alert tone="info">
          <Link to={`/sales/${s.replaces_id}`} className="font-medium underline">
            {t('sales.detail.viewOriginal')}
          </Link>
        </Alert>
      )}

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold text-slate-900">{title}</h1>
          <SaleStatusBadge status={s.status} />
        </div>
        <div className="flex flex-wrap gap-3">
          {s.status === 'DRAFT' && (
            <>
              <LinkButton to={`/sales/${s.id}/edit`} variant="secondary">
                <Pencil aria-hidden="true" className="size-5" />
                {t('common.edit')}
              </LinkButton>
              <Button disabled={s.items.length === 0} onClick={() => setPanel(panel === 'post' ? null : 'post')}>
                <CircleCheck aria-hidden="true" className="size-5" />
                {t('sales.detail.post')}
              </Button>
              <Button variant="danger" onClick={() => setPanel(panel === 'void' ? null : 'void')}>
                <Ban aria-hidden="true" className="size-5" />
                {t('sales.detail.discard')}
              </Button>
            </>
          )}
          {s.status === 'POSTED' && (
            <Button variant="danger" onClick={() => setPanel(panel === 'void' ? null : 'void')}>
              <Ban aria-hidden="true" className="size-5" />
              {t('sales.detail.void')}
            </Button>
          )}
          {s.status === 'VOID' && s.invoice_no && !s.replaced_by_id && (
            <Button loading={correct.isPending} onClick={() => correct.mutate()}>
              <Copy aria-hidden="true" className="size-5" />
              {t('sales.detail.correct')}
            </Button>
          )}
        </div>
      </div>

      {panel === 'post' && <PostPanel sale={s} onDone={() => setPanel(null)} />}
      {panel === 'void' && <VoidForm sale={s} onDone={() => setPanel(null)} />}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card title={t('sales.detail.summary')}>
          <Row label={t('sales.columns.customer')}>
            {s.customer_id ? (
              <Link to={`/customers/${s.customer_id}`} className="text-emerald-800 hover:underline">
                {s.customer_name}
              </Link>
            ) : (
              t('sales.walkIn')
            )}
          </Row>
          <Row label={t('sales.form.fields.date')}>{formatDate(s.sale_date)}</Row>
          <Row label={t('sales.form.fields.notes')}>{s.notes ?? NOT_SET}</Row>
          <Row label={t('sales.detail.record')}>
            {t('sales.detail.createdBy', { name: s.created_by_name, when: formatDateTime(s.created_at) })}
            {s.posted_at && (
              <>
                <br />
                {t('sales.detail.postedBy', { name: s.posted_by_name ?? '', when: formatDateTime(s.posted_at) })}
              </>
            )}
          </Row>
        </Card>

        <Card title={t('sales.detail.paymentHeading')}>
          {s.payment_type === null ? (
            <Row label={t('sales.payment.heading')}>{t('sales.detail.notPaidYet')}</Row>
          ) : (
            <>
              <Row label={t('sales.columns.payment')}>{t(`sales.paymentType.${s.payment_type}`)}</Row>
              <Row label={t('sales.detail.amountPaid')}>
                {formatMoney(s.amount_paid)}
                {s.payment_method && ` · ${t(`sales.payment.methods.${s.payment_method}`)}`}
                {s.payment_reference && ` · ${s.payment_reference}`}
              </Row>
              {s.customer_id && s.credit_amount !== '0.00' && (
                <Row label={t('sales.detail.onKhata')}>
                  <Link to={`/customers/${s.customer_id}#ledger`} className="text-emerald-800 hover:underline">
                    {formatMoney(s.credit_amount)}
                  </Link>
                </Row>
              )}
            </>
          )}
        </Card>
      </div>

      <ItemsSection sale={s} />

      {s.status !== 'DRAFT' && (
        <div className="border-t border-slate-200 pt-4">
          <p className="mb-2 text-sm font-medium text-slate-700">{t('sales.detail.exportThis')}</p>
          <ExportButtons kind={`sales/${s.id}`} />
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

/** Take the payment for a saved draft and post it. */
function PostPanel({ sale, onDone }: { sale: Sale; onDone: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const shop = useQuery({ queryKey: ['shop'], queryFn: getShop })
  const [payment, setPayment] = useState<PaymentValue>(emptyPayment())
  const [error, setError] = useState<string | null>(null)
  const [fields, setFields] = useState<Record<string, string>>({})
  const preview = useSalePreview(sale.items.map((item) => itemPayload(itemToLine(item))), sale.discount === '0.00' ? null : sale.discount, amountPaidFor(payment), {
    customerId: sale.customer_id,
    couponCode: sale.coupon_code,
  })
  const needsCustomer = preview.data !== undefined && !/^0+(\.0+)?$/.test(preview.data.credit) && sale.customer_id === null

  const post = useMutation({
    mutationFn: () => postSale(sale.id, paymentPayload(payment)),
    onSuccess: async () => {
      await invalidateSaleData(queryClient)
      onDone()
    },
    onError: (failure) => {
      if (failure instanceof ApiError) {
        setFields(failure.fieldErrors)
        setError(failure.message)
      } else setError(t('common.genericError'))
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setFields({})
    if (partAmountProblem(payment) !== null) return
    post.mutate()
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4 rounded-xl border border-emerald-200 bg-emerald-50/50 p-5">
      <p className="text-lg">
        {t('sales.detail.toPay')} <span className="text-2xl font-bold">{formatMoney(sale.total_amount)}</span>
      </p>
      {error && <Alert tone="error">{error}</Alert>}
      <PaymentPanel
        value={payment}
        onChange={setPayment}
        credit={preview.data?.credit ?? null}
        upiId={shop.data?.upi_id ?? null}
        amountError={fields.amount_paid}
        methodError={fields.payment_method}
        needsCustomer={needsCustomer}
      />
      {needsCustomer && <Alert tone="warning">{t('sales.detail.editToAddCustomer')}</Alert>}
      <div className="flex flex-wrap gap-3">
        <Button type="submit" loading={post.isPending}>
          {t('sales.detail.confirmPost')}
        </Button>
        <Button variant="secondary" onClick={onDone} disabled={post.isPending}>
          {t('common.cancel')}
        </Button>
      </div>
    </form>
  )
}

const itemToLine = (item: SaleItem) => ({
  key: String(item.id),
  product: { id: item.product_id, sku: item.sku, name: item.product_name, unit_code: item.unit_code, allows_decimal: item.unit_allows_decimal },
  quantity: item.quantity,
  unit_price: item.unit_price,
  discount: item.discount === '0.00' ? '' : item.discount,
})

/** Ask for the reason, then void (a posted sale) or discard (a draft). */
function VoidForm({ sale, onDone }: { sale: Sale; onDone: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const isDraft = sale.status === 'DRAFT'

  const run = useMutation({
    mutationFn: () => voidSale(sale.id, reason.trim()),
    onSuccess: async () => {
      await invalidateSaleData(queryClient)
      onDone()
    },
    onError: (failure) => setError(failure instanceof ApiError ? (failure.fieldErrors.reason ?? failure.message) : t('common.genericError')),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (reason.trim() === '') {
      setError(t('sales.detail.reasonRequired'))
      return
    }
    run.mutate()
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4 rounded-xl border border-red-200 bg-red-50 p-5">
      <p className="font-medium text-red-900">{isDraft ? t('sales.detail.discardWarning') : t('sales.detail.voidWarning')}</p>
      <TextAreaField
        label={t('sales.detail.reasonLabel')}
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
          {isDraft ? t('sales.detail.confirmDiscard') : t('sales.detail.confirmVoid')}
        </Button>
        <Button variant="secondary" onClick={onDone} disabled={run.isPending}>
          {t('common.cancel')}
        </Button>
      </div>
    </form>
  )
}

function ItemsSection({ sale }: { sale: Sale }) {
  const { t } = useTranslation()
  const posted = sale.invoice_no !== null
  const hasOffers = sale.promotions.length > 0
  const extra = (posted ? 2 : 0) + (hasOffers ? 1 : 0)
  const heading = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-slate-900">
        {t('sales.detail.items')} ({sale.items.length})
      </h2>
      {sale.items.length === 0 && <Alert tone="info">{t('sales.detail.noItems')}</Alert>}
      {sale.items.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200">
            <thead className="bg-slate-50">
              <tr>
                <th className={heading}>{t('sales.form.fields.product')}</th>
                <th className={`${heading} text-right`}>{t('sales.form.fields.quantity')}</th>
                <th className={`${heading} text-right`}>{t('sales.form.fields.price')}</th>
                <th className={`${heading} text-right`}>{t('sales.form.fields.discount')}</th>
                <th className={`${heading} text-right`}>{t('sales.form.fields.lineTotal')}</th>
                {hasOffers && <th className={`${heading} text-right`}>{t('billing.offers')}</th>}
                {posted && <th className={`${heading} text-right`}>{t('sales.detail.cost')}</th>}
                {posted && <th className={`${heading} text-right`}>{t('sales.detail.profit')}</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {sale.items.map((item) => (
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
                  <td className="whitespace-nowrap px-4 py-3 text-right">{formatMoney(item.unit_price)}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-right">{formatMoney(item.discount)}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-right font-semibold">{formatMoney(item.line_total)}</td>
                  {hasOffers && (
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-emerald-800">
                      {item.promotion_discount === '0.00' ? '—' : `− ${formatMoney(item.promotion_discount)}`}
                    </td>
                  )}
                  {posted && <td className="whitespace-nowrap px-4 py-3 text-right text-sm">{item.cogs_amount ? formatMoney(item.cogs_amount) : t('sales.detail.notAvailable')}</td>}
                  {posted && (
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm font-medium">
                      {item.profit === null ? <span className="text-slate-500">{t('sales.detail.notAvailable')}</span> : formatMoney(item.profit)}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
            <tfoot className="bg-slate-50 text-right">
              <tr>
                <td colSpan={4 + extra} className="px-4 py-2 text-slate-700">{t('billing.subtotal')}</td>
                <td className="whitespace-nowrap px-4 py-2 font-medium">{formatMoney(sale.subtotal)}</td>
              </tr>
              {sale.discount !== '0.00' && (
                <tr>
                  <td colSpan={4 + extra} className="px-4 py-2 text-slate-700">{t('billing.billDiscount')}</td>
                  <td className="whitespace-nowrap px-4 py-2 font-medium">− {formatMoney(sale.discount)}</td>
                </tr>
              )}
              {sale.promotions.map((offer) => (
                <tr key={offer.promotion_id}>
                  <td colSpan={4 + extra} className="px-4 py-2 text-emerald-900">
                    <span className="font-medium">{t('billing.offerLine', { name: offer.name, terms: offer.terms })}</span>
                    {offer.coupon_code && <span className="ml-2 rounded bg-emerald-100 px-1.5 py-0.5 font-mono text-xs">{offer.coupon_code}</span>}
                    <span className="block text-xs text-slate-500">{t('billing.whyApplied', { basis: offer.basis })}</span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-2 font-medium text-emerald-900">− {formatMoney(offer.amount)}</td>
                </tr>
              ))}
              <tr>
                <td colSpan={4 + extra} className="px-4 py-3 font-semibold text-slate-900">{t('billing.total')}</td>
                <td className="whitespace-nowrap px-4 py-3 text-lg font-bold">{formatMoney(sale.total_amount)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {posted && sale.items.length > 0 && <ProfitAndEffects sale={sale} />}
    </section>
  )
}

/** Gross profit (an estimate) when every cost is known, otherwise a plain "not available" and why. */
function ProfitAndEffects({ sale }: { sale: Sale }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 className="text-base font-semibold text-slate-900">{t('sales.detail.profitHeading')}</h3>
        {sale.gross_profit === null ? (
          <p className="mt-1 text-slate-700">
            <span className="font-semibold">{t('sales.detail.notAvailable')}.</span>{' '}
            {t('sales.detail.costMissing', { count: sale.lines_without_cost })}
          </p>
        ) : (
          <p className="mt-1 text-slate-700">
            <span className="text-2xl font-bold text-slate-900">{formatMoney(sale.gross_profit)}</span>{' '}
            {t('sales.detail.profitExplained', { cost: formatMoney(sale.cogs_total) })}
          </p>
        )}
        <p className="mt-2 text-sm text-slate-500">{t('sales.detail.profitEstimate')}</p>
      </div>

      <div>
        <h3 className="text-base font-semibold text-slate-900">{t('sales.detail.effectsHeading')}</h3>
        <p className="text-sm text-slate-500">{t('sales.detail.effectsHint')}</p>
        <ul className="mt-2 divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white shadow-sm">
          {sale.items.map((item) => (
            <li key={item.id} className="px-4 py-3 text-sm">
              <span className="font-medium text-slate-900">{item.product_name}</span>
              <span className="ml-2 text-slate-600">
                {item.inventory_effects
                  .map((e) => `${t(`history.types.${e.txn_type}`)} ${e.qty_delta.startsWith('-') ? '' : '+'}${formatQuantity(e.qty_delta)} ${item.unit_code}`)
                  .join(' · ')}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
