import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { getPurchase } from '@/api/purchases'
import { calculatePurchaseReturn, createPurchaseReturn, type PurchaseReturnItemPayload, type SupplierCreditMode } from '@/api/returns'
import type { Purchase } from '@/api/types'
import { SelectField, TextAreaField, TextField } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import { Alert, Button, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useDebounced } from '@/hooks/useDebounced'
import { needsNotice, useFailure } from '@/hooks/useFailure'
import { checkDecimal, isZero } from '@/lib/decimal'
import { formatMoney, formatQuantity } from '@/lib/format'
import { useIdempotencyKey } from '@/lib/idempotency'

import { todayText } from '../sales/saleForm'
import { invalidateReturnData } from './invalidate'

/** Send goods back to a supplier from a posted purchase. Only what is still on the shelf can go back. */
export function PurchaseReturnFormPage() {
  const { t } = useTranslation()
  const purchaseId = Number(useParams().id)
  const valid = Number.isInteger(purchaseId) && purchaseId > 0
  const purchase = useQuery({ queryKey: ['purchase', purchaseId], queryFn: () => getPurchase(purchaseId), enabled: valid })

  if (!valid || purchase.isError) {
    const notFound = !valid || (purchase.error instanceof ApiError && purchase.error.status === 404)
    return (
      <div className="space-y-6">
        {notFound ? <Alert tone="error">{t('purchases.detail.notFound')}</Alert> : <QueryError error={purchase.error} onRetry={() => void purchase.refetch()} />}
        <LinkButton to="/purchases" variant="secondary">
          {t('common.back')}
        </LinkButton>
      </div>
    )
  }
  if (!purchase.data) return <Spinner />
  if (purchase.data.status !== 'POSTED') {
    return (
      <div className="space-y-6">
        <Alert tone="warning">{t('returns.form.notReturnable')}</Alert>
        <LinkButton to={`/purchases/${purchase.data.id}`} variant="secondary">
          {t('common.back')}
        </LinkButton>
      </div>
    )
  }
  return <Form purchase={purchase.data} />
}

function Form({ purchase }: { purchase: Purchase }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [quantities, setQuantities] = useState<Record<number, string>>({})
  const [mode, setMode] = useState<SupplierCreditMode>('SUPPLIER_CREDIT')
  const [date, setDate] = useState(todayText())
  const [reason, setReason] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const { failure, setFailure } = useFailure()
  const idem = useIdempotencyKey()

  const items: PurchaseReturnItemPayload[] = purchase.items
    .filter((item) => (quantities[item.id] ?? '').trim() !== '' && !isZero(quantities[item.id] ?? '0'))
    .map((item) => ({ purchase_item_id: item.id, quantity: (quantities[item.id] ?? '').trim() }))
  const badFormat = purchase.items.some((item) => {
    const text = (quantities[item.id] ?? '').trim()
    return text !== '' && checkDecimal(text, 3) !== 'ok'
  })
  const request = useDebounced(JSON.stringify({ purchase_id: purchase.id, items }), 250)
  const preview = useQuery({
    queryKey: ['purchaseReturnCalc', request],
    queryFn: () => calculatePurchaseReturn(JSON.parse(request) as Parameters<typeof calculatePurchaseReturn>[0]),
    placeholderData: keepPreviousData,
    enabled: items.length > 0 && !badFormat,
  })
  const priced = preview.data
  const lineFor = (id: number) => priced?.lines.find((line) => line.purchase_item_id === id)
  const problems = priced !== undefined && priced.lines.some((line) => line.errors.length > 0)

  const post = useMutation({
    mutationFn: () => {
      const body = { purchase_id: purchase.id, credit_mode: mode, return_date: date, reason: reason.trim() === '' ? null : reason.trim(), items }
      return createPurchaseReturn(body, { idempotencyKey: idem.keyFor(JSON.stringify(body)) })
    },
    onSuccess: async (created) => {
      idem.renew()
      await invalidateReturnData(queryClient)
      void navigate(`/returns/purchases/${created.id}`, { state: { done: true } })
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
      <PageHeader title={t('returns.form.purchaseTitle', { no: purchase.purchase_no ?? '' })} subtitle={t('returns.form.subtitle')} />
      {localError && <Alert tone="error">{localError}</Alert>}
      {failure !== null && (
        <ErrorNotice error={failure} context="return" safeToRepeat retry={submit} go_back={() => void navigate(`/purchases/${purchase.id}`)} />
      )}
      <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <ul className="space-y-4">
          {purchase.items.map((item) => {
            const line = lineFor(item.id)
            const text = quantities[item.id] ?? ''
            const formatProblem = text.trim() !== '' && checkDecimal(text.trim(), 3) !== 'ok'
            return (
              <li key={item.id} className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="font-semibold text-slate-900">{item.product_name}</p>
                  <p className="text-sm text-slate-600">
                    {t('returns.form.bought')}: {formatQuantity(item.quantity)} {item.unit_code}
                    {line?.already_returned && !isZero(line.already_returned) && (
                      <> · {t('returns.form.alreadyReturned')}: {formatQuantity(line.already_returned)}</>
                    )}
                    {line?.in_stock && <> · {t('returns.form.inStock')}: {formatQuantity(line.in_stock)}</>}
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
                    {t('returns.form.credit')}: <span className="text-lg font-bold text-slate-900">{line?.credit ? formatMoney(line.credit) : '—'}</span>
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      </section>
      <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <SelectField label={t('returns.form.creditBy')} value={mode} onChange={(event) => setMode(event.target.value as SupplierCreditMode)}>
            {(['SUPPLIER_CREDIT', 'CASH', 'UPI'] as const).map((value) => (
              <option key={value} value={value}>
                {t(`returns.creditModes.${value}`)}
              </option>
            ))}
          </SelectField>
          <div>
            <label htmlFor="preturn-date" className="mb-1.5 block text-sm font-medium text-slate-800">
              {t('returns.form.date')}
            </label>
            <input
              id="preturn-date"
              type="date"
              value={date}
              max={todayText()}
              onChange={(event) => setDate(event.target.value)}
              className="block min-h-12 w-full rounded-lg border border-slate-300 bg-white px-3 text-base focus-visible:outline-2 focus-visible:outline-emerald-600"
            />
          </div>
        </div>
        {mode === 'SUPPLIER_CREDIT' && <p className="text-sm text-slate-600">{t('returns.form.supplierCreditNote')}</p>}
        <TextAreaField label={t('returns.form.reason')} value={reason} onChange={(event) => setReason(event.target.value)} rows={2} maxLength={500} optional />
        <div className="flex items-baseline justify-between border-t border-slate-200 pt-3">
          <span className="text-lg font-semibold text-slate-900">{t('returns.form.totalCredit')}</span>
          <span className="text-3xl font-bold text-slate-900">{priced ? formatMoney(priced.total_credit) : '—'}</span>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button loading={post.isPending} disabled={items.length === 0 || badFormat || problems} onClick={submit}>
            {post.isPending ? t('returns.form.submitting') : t('returns.form.submit')}
          </Button>
          <LinkButton to={`/purchases/${purchase.id}`} variant="secondary">
            {t('common.cancel')}
          </LinkButton>
        </div>
      </section>
    </div>
  )
}
