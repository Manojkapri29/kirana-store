import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { getSale } from '@/api/sales'
import { calculateSalesReturn, createSalesReturn, type RefundMode, type SalesReturnItemPayload } from '@/api/returns'
import type { Sale } from '@/api/types'
import { TextAreaField, TextField, SelectField } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import { Alert, Button, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { needsNotice, useFailure } from '@/hooks/useFailure'
import { useDebounced } from '@/hooks/useDebounced'
import { checkDecimal, decimalGreaterThan, isZero } from '@/lib/decimal'
import { formatMoney, formatQuantity } from '@/lib/format'
import { useIdempotencyKey } from '@/lib/idempotency'

import { todayText } from '../sales/saleForm'
import { invalidateReturnData } from './invalidate'

/**
 * Take goods back from a completed sale. The person only says which lines and how many; the refund (net of
 * offers and any bill discount) is worked out by the server, and the screen shows exactly that.
 * "Complete return" is shown as done only after the server has really recorded it.
 */
export function SalesReturnFormPage() {
  const { t } = useTranslation()
  const saleId = Number(useParams().id)
  const valid = Number.isInteger(saleId) && saleId > 0
  const sale = useQuery({ queryKey: ['sale', saleId], queryFn: () => getSale(saleId), enabled: valid })

  if (!valid || sale.isError) {
    const notFound = !valid || (sale.error instanceof ApiError && sale.error.status === 404)
    return (
      <div className="space-y-6">
        {notFound ? <Alert tone="error">{t('sales.detail.notFound')}</Alert> : <QueryError error={sale.error} onRetry={() => void sale.refetch()} />}
        <LinkButton to="/sales" variant="secondary">
          {t('sales.detail.backToList')}
        </LinkButton>
      </div>
    )
  }
  if (!sale.data) return <Spinner />
  if (sale.data.status !== 'POSTED') {
    return (
      <div className="space-y-6">
        <Alert tone="warning">{t('returns.form.notReturnable')}</Alert>
        <LinkButton to={`/sales/${sale.data.id}`} variant="secondary">
          {t('common.back')}
        </LinkButton>
      </div>
    )
  }
  return <Form sale={sale.data} />
}

function Form({ sale }: { sale: Sale }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [quantities, setQuantities] = useState<Record<number, string>>({})
  const [mode, setMode] = useState<RefundMode>('CASH')
  const [date, setDate] = useState(todayText())
  const [reason, setReason] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const { failure, setFailure } = useFailure()
  const idem = useIdempotencyKey()

  const items: SalesReturnItemPayload[] = sale.items
    .filter((item) => (quantities[item.id] ?? '').trim() !== '' && !isZero(quantities[item.id] ?? '0'))
    .map((item) => ({ sale_item_id: item.id, quantity: (quantities[item.id] ?? '').trim() }))
  const badFormat = sale.items.some((item) => {
    const text = (quantities[item.id] ?? '').trim()
    return text !== '' && checkDecimal(text, 3) !== 'ok'
  })
  const request = useDebounced(JSON.stringify({ sale_id: sale.id, refund_mode: mode, items }), 250)
  const preview = useQuery({
    queryKey: ['salesReturnCalc', request],
    queryFn: () => calculateSalesReturn(JSON.parse(request) as Parameters<typeof calculateSalesReturn>[0]),
    placeholderData: keepPreviousData,
    enabled: items.length > 0 && !badFormat,
  })
  const priced = preview.data
  const lineFor = (id: number) => priced?.lines.find((line) => line.sale_item_id === id)

  const cashLimited = priced !== undefined && mode !== 'KHATA' && decimalGreaterThan(priced.total_refund, priced.cash_refundable)
  const needsCustomer = priced !== undefined && mode === 'KHATA' && !priced.khata_allowed
  const problems = priced !== undefined && (priced.errors.length > 0 || priced.lines.some((line) => line.errors.length > 0))

  const post = useMutation({
    mutationFn: () => {
      const body = { sale_id: sale.id, refund_mode: mode, return_date: date, reason: reason.trim() === '' ? null : reason.trim(), items }
      // The same key on every retry: the server records the return once and gives the same answer.
      return createSalesReturn(body, { idempotencyKey: idem.keyFor(JSON.stringify(body)) })
    },
    onSuccess: async (created) => {
      idem.renew()
      await invalidateReturnData(queryClient)
      void navigate(`/returns/sales/${created.id}`, { state: { done: true } })
    },
    onError: (error) => {
      if (needsNotice(error) || !(error instanceof ApiError)) setFailure(error)
      else setLocalError(error.message)
    },
  })

  function submit() {
    setLocalError(null)
    setFailure(null)
    if (items.length === 0) {
      setLocalError(t('returns.form.chooseSomething'))
      return
    }
    post.mutate()
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t('returns.form.salesTitle', { no: sale.invoice_no ?? '' })} subtitle={t('returns.form.subtitle')} />
      {localError && <Alert tone="error">{localError}</Alert>}
      {failure !== null && (
        <ErrorNotice error={failure} context="return" safeToRepeat retry={submit} go_back={() => void navigate(`/sales/${sale.id}`)} />
      )}
      <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <ul className="space-y-4">
          {sale.items.map((item) => {
            const line = lineFor(item.id)
            const text = quantities[item.id] ?? ''
            const formatProblem = text.trim() !== '' && checkDecimal(text.trim(), 3) !== 'ok'
            return (
              <li key={item.id} className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="font-semibold text-slate-900">{item.product_name}</p>
                  <p className="text-sm text-slate-600">
                    {t('returns.form.sold')}: {formatQuantity(item.quantity)} {item.unit_code}
                    {line?.already_returned && !isZero(line.already_returned) && (
                      <> · {t('returns.form.alreadyReturned')}: {formatQuantity(line.already_returned)}</>
                    )}
                    {line?.returnable && <> · {t('returns.form.canReturn')}: {formatQuantity(line.returnable)}</>}
                  </p>
                </div>
                <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <TextField
                    label={t('returns.form.quantity')}
                    value={text}
                    inputMode="decimal"
                    suffix={item.unit_code}
                    onChange={(event) => setQuantities((current) => ({ ...current, [item.id]: event.target.value }))}
                    error={formatProblem ? t('purchases.form.validation.quantityInvalid') : line?.errors[0]?.message}
                    optional
                  />
                  <div className="self-end pb-2 text-slate-700">
                    {t('returns.form.refund')}: <span className="text-lg font-bold text-slate-900">{line?.refund ? formatMoney(line.refund) : '—'}</span>
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      </section>

      <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <SelectField label={t('returns.form.settledBy')} value={mode} onChange={(event) => setMode(event.target.value as RefundMode)}>
            {(['CASH', 'UPI', 'KHATA'] as const).map((value) => (
              <option key={value} value={value}>
                {t(`returns.refundModes.${value}`)}
              </option>
            ))}
          </SelectField>
          <div>
            <label htmlFor="return-date" className="mb-1.5 block text-sm font-medium text-slate-800">
              {t('returns.form.date')}
            </label>
            <input
              id="return-date"
              type="date"
              value={date}
              max={todayText()}
              onChange={(event) => setDate(event.target.value)}
              className="block min-h-12 w-full rounded-lg border border-slate-300 bg-white px-3 text-base focus-visible:outline-2 focus-visible:outline-emerald-600"
            />
          </div>
        </div>
        <TextAreaField label={t('returns.form.reason')} value={reason} onChange={(event) => setReason(event.target.value)} rows={2} maxLength={500} optional />
        {cashLimited && priced && <Alert tone="warning">{t('returns.form.cashLimit', { amount: formatMoney(priced.cash_refundable) })}</Alert>}
        {needsCustomer && <Alert tone="warning">{t('returns.form.noCustomer')}</Alert>}
        <div className="flex items-baseline justify-between border-t border-slate-200 pt-3">
          <span className="text-lg font-semibold text-slate-900">{t('returns.form.totalRefund')}</span>
          <span className="text-3xl font-bold text-slate-900">{priced ? formatMoney(priced.total_refund) : '—'}</span>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button
            loading={post.isPending}
            disabled={items.length === 0 || badFormat || problems || cashLimited || needsCustomer}
            onClick={submit}
          >
            {post.isPending ? t('returns.form.submitting') : t('returns.form.submit')}
          </Button>
          <LinkButton to={`/sales/${sale.id}`} variant="secondary">
            {t('common.cancel')}
          </LinkButton>
        </div>
      </section>
    </div>
  )
}
