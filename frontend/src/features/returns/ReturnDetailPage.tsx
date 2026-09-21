import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Ban } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { getPurchaseReturn, getSalesReturn, voidPurchaseReturn, voidSalesReturn } from '@/api/returns'
import { TextAreaField } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import { Alert, Button, LinkButton, QueryError, Spinner } from '@/components/ui'
import { needsNotice, useFailure } from '@/hooks/useFailure'
import { formatDate, formatDateTime, formatMoney, formatQuantity, NOT_SET } from '@/lib/format'

import { invalidateReturnData } from './invalidate'
import { ReturnStatusBadge } from './ReturnsPage'

interface Row {
  id: number
  name: string
  sku: string
  unit: string
  quantity: string
  amount: string
  extra: string | null
}

/** One return, sales or purchase. It can be voided (with a reason), never deleted: the record stays. */
export function ReturnDetailPage({ kind }: { kind: 'sales' | 'purchases' }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const location = useLocation()
  const returnId = Number(useParams().id)
  const valid = Number.isInteger(returnId) && returnId > 0
  const [voiding, setVoiding] = useState(false)
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | null>(null)
  const failure = useFailure()

  const record = useQuery({
    queryKey: [kind === 'sales' ? 'salesReturn' : 'purchaseReturn', returnId],
    queryFn: async () => (kind === 'sales' ? { sales: await getSalesReturn(returnId) } : { purchases: await getPurchaseReturn(returnId) }),
    enabled: valid,
  })
  const sales = record.data && 'sales' in record.data ? record.data.sales : null
  const purchase = record.data && 'purchases' in record.data ? record.data.purchases : null

  const voidIt = useMutation({
    mutationFn: async () => {
      if (kind === 'sales') await voidSalesReturn(returnId, reason.trim())
      else await voidPurchaseReturn(returnId, reason.trim())
    },
    onSuccess: async () => {
      setVoiding(false)
      await invalidateReturnData(queryClient)
    },
    onError: (error) => {
      if (needsNotice(error) || !(error instanceof ApiError)) failure.setFailure(error)
      else setReasonError(error.message)
    },
  })

  if (!valid || record.isError) {
    const notFound = !valid || (record.error instanceof ApiError && record.error.status === 404)
    return (
      <div className="space-y-6">
        {notFound ? <Alert tone="error">{t('returns.detail.notFound')}</Alert> : <QueryError error={record.error} onRetry={() => void record.refetch()} />}
        <LinkButton to="/returns" variant="secondary">
          {t('returns.detail.back')}
        </LinkButton>
      </div>
    )
  }
  if (!sales && !purchase) return <Spinner />

  const head = sales ?? purchase!
  const rows: Row[] = sales
    ? sales.items.map((i) => ({
        id: i.id,
        name: i.product_name,
        sku: i.sku,
        unit: i.unit_code,
        quantity: i.quantity,
        amount: i.refund_amount,
        extra: i.cogs_amount,
      }))
    : purchase!.items.map((i) => ({
        id: i.id,
        name: i.product_name,
        sku: i.sku,
        unit: i.unit_code,
        quantity: i.quantity,
        amount: i.line_total,
        extra: null,
      }))
  const total = sales ? sales.total_refund : purchase!.total_amount
  const against = sales ? { to: `/sales/${sales.sale_id}`, label: sales.invoice_no } : { to: `/purchases/${purchase!.purchase_id}`, label: purchase!.purchase_no }
  const party = sales ? sales.customer_name : purchase!.supplier_name
  const mode = sales ? t(`returns.refundModes.${sales.refund_mode}`) : t(`returns.creditModes.${purchase!.credit_mode}`)
  const justDone = (location.state as { done?: boolean } | null)?.done === true

  function confirmVoid() {
    setReasonError(null)
    failure.clear()
    if (reason.trim() === '') {
      setReasonError(t('returns.detail.reasonRequired'))
      return
    }
    voidIt.mutate()
  }

  return (
    <div className="space-y-6">
      <Link to="/returns" className="inline-flex min-h-10 items-center gap-2 text-emerald-800 hover:underline">
        <ArrowLeft aria-hidden="true" className="size-5" />
        {t('returns.detail.back')}
      </Link>
      {/* Shown only because the server already answered: the return exists. */}
      {justDone && head.status === 'POSTED' && <Alert tone="success">{t('returns.detail.done', { no: head.return_no })}</Alert>}
      {head.status === 'VOID' && <Alert tone="warning">{t('returns.detail.voidedNotice')}{head.void_reason ? ` — ${head.void_reason}` : ''}</Alert>}

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold text-slate-900">{head.return_no}</h1>
          <ReturnStatusBadge status={head.status} />
        </div>
        {head.status === 'POSTED' && (
          <Button variant="danger" onClick={() => setVoiding(!voiding)}>
            <Ban aria-hidden="true" className="size-5" />
            {t('returns.detail.voidReturn')}
          </Button>
        )}
      </div>

      {voiding && (
        <section className="space-y-3 rounded-xl border border-red-200 bg-white p-5 shadow-sm">
          <Alert tone="warning">{t('returns.detail.voidWarning')}</Alert>
          <TextAreaField label={t('returns.detail.reason')} value={reason} onChange={(e) => setReason(e.target.value)} rows={2} maxLength={500} error={reasonError ?? undefined} />
          {failure.failure !== null && <ErrorNotice error={failure.failure} context="return" go_back={() => setVoiding(false)} />}
          <Button variant="danger" loading={voidIt.isPending} onClick={confirmVoid}>
            {t('returns.detail.confirmVoid')}
          </Button>
        </section>
      )}

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
          <div>
            <dt className="text-sm text-slate-500">{t('returns.columns.document')}</dt>
            <dd className="font-medium">
              <Link to={against.to} className="text-emerald-800 hover:underline">
                {t('returns.detail.against', { no: against.label ?? '' })}
              </Link>
            </dd>
          </div>
          <div>
            <dt className="text-sm text-slate-500">{t('returns.columns.party')}</dt>
            <dd className="font-medium">{party ?? NOT_SET}</dd>
          </div>
          <div>
            <dt className="text-sm text-slate-500">{t('returns.columns.date')}</dt>
            <dd className="font-medium">{formatDate(head.return_date)}</dd>
          </div>
          <div>
            <dt className="text-sm text-slate-500">{t('returns.columns.mode')}</dt>
            <dd className="font-medium">{mode}</dd>
          </div>
          <div>
            <dt className="text-sm text-slate-500">{t('returns.detail.reason')}</dt>
            <dd className="font-medium">{head.reason ?? NOT_SET}</dd>
          </div>
          {sales && (
            <div>
              <dt className="text-sm text-slate-500">{t('returns.detail.cost')}</dt>
              <dd className="font-medium">{sales.cogs_total === null ? t('returns.detail.notAvailable') : formatMoney(sales.cogs_total)}</dd>
            </div>
          )}
          <div>
            <dt className="text-sm text-slate-500">{formatDateTime(head.created_at)}</dt>
            <dd className="font-medium">{head.created_by_name}</dd>
          </div>
        </dl>
      </section>

      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">{t('returns.form.product')}</th>
              <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-slate-500">{t('returns.form.quantity')}</th>
              <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-slate-500">{sales ? t('returns.form.refund') : t('returns.form.credit')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row) => (
              <tr key={row.id}>
                <td className="px-4 py-3">
                  <p className="font-medium">{row.name}</p>
                  <p className="font-mono text-xs text-slate-500">{row.sku}</p>
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-right">
                  {formatQuantity(row.quantity)} {row.unit}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-right font-semibold">{formatMoney(row.amount)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td className="px-4 py-3 font-semibold" colSpan={2}>
                {sales ? t('returns.form.totalRefund') : t('returns.form.totalCredit')}
              </td>
              <td className="px-4 py-3 text-right text-xl font-bold">{formatMoney(total)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  )
}
