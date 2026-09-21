import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Ban, CircleCheck, Pencil } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { getShop } from '@/api/catalog'
import { getQuickSale, postQuickSale, voidQuickSale } from '@/api/quickSales'
import type { QuickSale } from '@/api/types'
import { ExportButtons } from '@/components/ExportButtons'
import { TextAreaField } from '@/components/fields'
import { Alert, Button, LinkButton, QueryError, Spinner } from '@/components/ui'
import { formatDate, formatDateTime, formatMoney, NOT_SET } from '@/lib/format'
import { PaymentPanel } from '@/features/sales/PaymentPanel'
import { SaleStatusBadge } from '@/features/sales/SaleStatusBadge'
import { emptyPayment, partAmountProblem, paymentPayload, type PaymentValue } from '@/features/sales/saleForm'

import { invalidateQuickSaleData } from './invalidate'

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-2">
      <dt className="text-slate-600">{label}</dt>
      <dd className="text-right font-medium text-slate-900">{children}</dd>
    </div>
  )
}

export function QuickSaleDetailPage() {
  const { t } = useTranslation()
  const id = Number(useParams().id)
  const validId = Number.isInteger(id) && id > 0
  const [panel, setPanel] = useState<'post' | 'void' | null>(null)
  const sale = useQuery({ queryKey: ['quickSale', id], queryFn: () => getQuickSale(id), enabled: validId })

  if (!validId || sale.isError) {
    const notFound = !validId || (sale.error instanceof ApiError && sale.error.status === 404)
    return (
      <div className="space-y-6">
        {notFound ? <Alert tone="error">{t('quickSales.detail.notFound')}</Alert> : <QueryError onRetry={() => void sale.refetch()} />}
        <LinkButton to="/quick-sales" variant="secondary">{t('quickSales.detail.backToList')}</LinkButton>
      </div>
    )
  }
  if (!sale.data) return <Spinner />
  const s = sale.data
  const isDraft = s.status === 'DRAFT'
  const title = s.quick_no ?? t('quickSales.draftLabel', { id: s.id })

  return (
    <div className="space-y-6">
      <Link to="/quick-sales" className="inline-flex min-h-10 items-center gap-2 text-emerald-800 hover:underline">
        <ArrowLeft aria-hidden="true" className="size-5" />
        {t('quickSales.detail.backToList')}
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold text-slate-900">{title}</h1>
            <SaleStatusBadge status={s.status} />
          </div>
          <p className="mt-1 text-slate-600">
            {formatDate(s.sale_date)} · {s.customer_name ?? t('sales.walkIn')}
          </p>
        </div>
        {panel === null && s.status !== 'VOID' && (
          <div className="flex flex-wrap gap-3">
            {isDraft && (
              <>
                <Button onClick={() => setPanel('post')}>
                  <CircleCheck aria-hidden="true" className="size-5" />
                  {t('quickSales.detail.post')}
                </Button>
                <LinkButton to={`/quick-sales/${s.id}/edit`} variant="secondary">
                  <Pencil aria-hidden="true" className="size-5" />
                  {t('quickSales.detail.edit')}
                </LinkButton>
              </>
            )}
            <Button variant="danger" onClick={() => setPanel('void')}>
              <Ban aria-hidden="true" className="size-5" />
              {isDraft ? t('quickSales.detail.discard') : t('quickSales.detail.void')}
            </Button>
          </div>
        )}
      </div>

      {isDraft && <Alert tone="info">{t('quickSales.detail.draftNotice')}</Alert>}
      {s.status === 'VOID' && (
        <Alert tone="warning">
          {t('quickSales.detail.voidNotice')} {s.void_reason && `${t('quickSales.detail.voidReason')}: ${s.void_reason}`}
        </Alert>
      )}
      <Alert tone="info">{t('quickSales.notice')}</Alert>

      {panel === 'post' && <PostPanel sale={s} onDone={() => setPanel(null)} />}
      {panel === 'void' && <VoidPanel sale={s} onDone={() => setPanel(null)} />}

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <dl className="divide-y divide-slate-100">
          <Row label={t('quickSales.detail.amount')}>{formatMoney(s.gross_amount)}</Row>
          <Row label={t('quickSales.detail.discount')}>{s.discount === '0.00' ? NOT_SET : `− ${formatMoney(s.discount)}`}</Row>
          <Row label={t('quickSales.detail.total')}>
            <span className="text-2xl font-bold">{formatMoney(s.total_amount)}</span>
          </Row>
          {s.payment_type && (
            <>
              <Row label={t('quickSales.detail.payment')}>{t(`sales.paymentType.${s.payment_type}`)}{s.payment_method ? ` · ${t(`sales.payment.methods.${s.payment_method}`)}` : ''}</Row>
              <Row label={t('quickSales.detail.paid')}>{formatMoney(s.amount_paid)}</Row>
              {s.credit_amount !== '0.00' && <Row label={t('quickSales.detail.onKhata')}>{formatMoney(s.credit_amount)}</Row>}
            </>
          )}
          <Row label={t('quickSales.profit')}>
            <span className="text-slate-600">{t('quickSales.profitNotAvailable')}</span>
          </Row>
        </dl>
        {s.note && <p className="mt-3 border-t border-slate-100 pt-3 text-slate-700">{s.note}</p>}
        <p className="mt-3 text-sm text-slate-500">{t('quickSales.detail.createdBy', { name: s.created_by_name, when: formatDateTime(s.created_at) })}</p>
        {s.posted_by_name && s.posted_at && (
          <p className="text-sm text-slate-500">{t('quickSales.detail.postedBy', { name: s.posted_by_name, when: formatDateTime(s.posted_at) })}</p>
        )}
      </section>

      <div className="border-t border-slate-200 pt-4">
        <p className="mb-2 text-sm font-medium text-slate-700">{t('quickSales.detail.exportThis')}</p>
        <ExportButtons kind="quick-sales" filters={{ q: s.quick_no ?? undefined }} />
      </div>
    </div>
  )
}

function PostPanel({ sale, onDone }: { sale: QuickSale; onDone: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const shop = useQuery({ queryKey: ['shop'], queryFn: getShop })
  const [payment, setPayment] = useState<PaymentValue>(emptyPayment())
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const run = useMutation({
    mutationFn: () => postQuickSale(sale.id, paymentPayload(payment)),
    onSuccess: async () => {
      await invalidateQuickSaleData(queryClient)
      onDone()
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        setErrors(error.fieldErrors)
        setFormError(error.message)
      } else setFormError(t('common.genericError'))
    },
  })
  function submit(event: FormEvent) {
    event.preventDefault()
    if (partAmountProblem(payment) !== null) return
    setFormError(null)
    setErrors({})
    run.mutate()
  }
  return (
    <form onSubmit={submit} noValidate className="space-y-4 rounded-xl border border-emerald-200 bg-emerald-50 p-5">
      {formError && <Alert tone="error">{formError}</Alert>}
      <p className="text-lg font-semibold text-slate-900">{t('quickSales.detail.total')}: {formatMoney(sale.total_amount)}</p>
      <PaymentPanel
        value={payment}
        onChange={setPayment}
        credit={null}
        upiId={shop.data?.upi_id ?? null}
        amountError={errors.amount_paid}
        methodError={errors.payment_method}
        needsCustomer={sale.customer_id === null && errors.customer_id !== undefined}
      />
      <div className="flex flex-wrap gap-3">
        <Button type="submit" loading={run.isPending}>{t('quickSales.detail.confirmPost')}</Button>
        <Button variant="secondary" onClick={onDone} disabled={run.isPending}>{t('common.cancel')}</Button>
      </div>
    </form>
  )
}

function VoidPanel({ sale, onDone }: { sale: QuickSale; onDone: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const isDraft = sale.status === 'DRAFT'
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const run = useMutation({
    mutationFn: () => voidQuickSale(sale.id, reason.trim()),
    onSuccess: async () => {
      await invalidateQuickSaleData(queryClient)
      onDone()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : t('common.genericError')),
  })
  function submit(event: FormEvent) {
    event.preventDefault()
    if (reason.trim() === '') {
      setError(t('quickSales.detail.reasonRequired'))
      return
    }
    run.mutate()
  }
  return (
    <form onSubmit={submit} noValidate className="space-y-4 rounded-xl border border-red-200 bg-red-50 p-5">
      <p className="font-medium text-red-900">{isDraft ? t('quickSales.detail.discardWarning') : t('quickSales.detail.voidWarning')}</p>
      <TextAreaField
        label={t('quickSales.detail.reasonLabel')}
        value={reason}
        onChange={(e) => {
          setReason(e.target.value)
          setError(null)
        }}
        error={error}
        maxLength={500}
        autoFocus
      />
      <div className="flex flex-wrap gap-3">
        <Button type="submit" variant="danger" loading={run.isPending}>
          {isDraft ? t('quickSales.detail.confirmDiscard') : t('quickSales.detail.confirmVoid')}
        </Button>
        <Button variant="secondary" onClick={onDone} disabled={run.isPending}>{t('common.cancel')}</Button>
      </div>
    </form>
  )
}
