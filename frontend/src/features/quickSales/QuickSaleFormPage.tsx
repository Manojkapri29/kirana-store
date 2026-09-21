import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, useNavigate, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { getShop } from '@/api/catalog'
import { createQuickSale, getQuickSale, postQuickSale, updateQuickSale } from '@/api/quickSales'
import type { QuickSale, QuickSalePayload } from '@/api/types'
import { TextAreaField, TextField } from '@/components/fields'
import { Alert, Button, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { checkDecimal, isZero } from '@/lib/decimal'
import { CURRENCY_SYMBOL } from '@/lib/format'
import { CustomerPicker, type PickedCustomer } from '@/features/sales/CustomerPicker'
import { PaymentPanel } from '@/features/sales/PaymentPanel'
import { emptyPayment, partAmountProblem, paymentPayload, todayText, type PaymentValue } from '@/features/sales/saleForm'

import { invalidateQuickSaleData } from './invalidate'

export function QuickSaleFormPage({ mode }: { mode: 'create' | 'edit' }) {
  const { t } = useTranslation()
  const id = Number(useParams().id)
  const validId = mode === 'create' || (Number.isInteger(id) && id > 0)
  const sale = useQuery({ queryKey: ['quickSale', id], queryFn: () => getQuickSale(id), enabled: mode === 'edit' && validId, gcTime: 0 })
  const title = mode === 'create' ? t('quickSales.form.addTitle') : t('quickSales.form.editTitle')

  if (!validId || sale.isError) {
    const notFound = !validId || (sale.error instanceof ApiError && sale.error.status === 404)
    return (
      <div className="space-y-6">
        <PageHeader title={title} />
        {notFound ? <Alert tone="error">{t('quickSales.detail.notFound')}</Alert> : <QueryError onRetry={() => void sale.refetch()} />}
        <LinkButton to="/quick-sales" variant="secondary">{t('quickSales.detail.backToList')}</LinkButton>
      </div>
    )
  }
  if (mode === 'edit' && !sale.data) return <Spinner />
  if (sale.data && sale.data.status !== 'DRAFT') return <Navigate to={`/quick-sales/${sale.data.id}`} replace />

  return (
    <div className="space-y-6">
      <PageHeader title={title} />
      <Alert tone="info">{t('quickSales.notice')}</Alert>
      <Form sale={sale.data} />
    </div>
  )
}

function Form({ sale }: { sale: QuickSale | undefined }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const shop = useQuery({ queryKey: ['shop'], queryFn: getShop })
  const [draftId, setDraftId] = useState<number | undefined>(sale?.id)
  const [amount, setAmount] = useState(sale?.gross_amount ?? '')
  const [discount, setDiscount] = useState(sale && !isZero(sale.discount) ? sale.discount : '')
  const [customer, setCustomer] = useState<PickedCustomer | null>(
    sale?.customer_id ? { id: sale.customer_id, name: sale.customer_name ?? '', phone: null } : null,
  )
  const [date, setDate] = useState(sale?.sale_date ?? todayText())
  const [note, setNote] = useState(sale?.note ?? '')
  const [payment, setPayment] = useState<PaymentValue>(emptyPayment())
  const [localErrors, setLocalErrors] = useState<Record<string, string>>({})
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: async (complete: boolean) => {
      const body: QuickSalePayload = {
        gross_amount: amount.trim(),
        discount: discount.trim() === '' ? null : discount.trim(),
        customer_id: customer?.id ?? null,
        sale_date: date,
        note: note.trim() === '' ? null : note.trim(),
      }
      let saved: QuickSale
      if (draftId !== undefined) saved = await updateQuickSale(draftId, body)
      else {
        saved = await createQuickSale(body)
        setDraftId(saved.id) // a failed completion below must not create a second draft on retry
      }
      return complete ? postQuickSale(saved.id, paymentPayload(payment)) : saved
    },
    onSuccess: async (saved) => {
      await invalidateQuickSaleData(queryClient)
      void navigate(`/quick-sales/${saved.id}`)
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        setServerErrors(error.fieldErrors)
        setFormError(error.message)
      } else setFormError(t('common.genericError'))
    },
  })

  function submit(complete: boolean) {
    setFormError(null)
    setServerErrors({})
    const errors: Record<string, string> = {}
    const gross = checkDecimal(amount, 2)
    if (gross === 'empty') errors.amount = t('quickSales.form.amountRequired')
    else if (gross !== 'ok') errors.amount = t('quickSales.form.amountInvalid')
    if (discount.trim() !== '' && checkDecimal(discount, 2) !== 'ok') errors.discount = t('quickSales.form.discountInvalid')
    setLocalErrors(errors)
    if (Object.keys(errors).length > 0 || (complete && partAmountProblem(payment) !== null)) {
      setFormError(t('quickSales.form.fixErrors'))
      return
    }
    save.mutate(complete)
  }

  return (
    <form
      onSubmit={(event: FormEvent) => {
        event.preventDefault()
        submit(false)
      }}
      noValidate
      className="grid grid-cols-1 gap-6 lg:grid-cols-3"
    >
      <div className="space-y-6 lg:col-span-2">
        {formError && <Alert tone="error">{formError}</Alert>}
        <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <TextField
            label={t('quickSales.form.amount')}
            value={amount}
            inputMode="decimal"
            onChange={(e) => setAmount(e.target.value)}
            error={localErrors.amount ?? serverErrors.gross_amount}
            prefix={CURRENCY_SYMBOL}
            autoFocus
          />
          <TextField
            label={t('quickSales.form.discount')}
            hint={t('quickSales.form.discountHint')}
            value={discount}
            inputMode="decimal"
            onChange={(e) => setDiscount(e.target.value)}
            error={localErrors.discount ?? serverErrors.discount}
            prefix={CURRENCY_SYMBOL}
            optional
          />
          <TextAreaField label={t('quickSales.form.note')} value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={2000} optional />
        </section>
      </div>
      <aside className="space-y-6">
        <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <CustomerPicker label={t('quickSales.form.customer')} value={customer} onChange={setCustomer} error={serverErrors.customer_id} />
          <p className="text-sm text-slate-500">{t('quickSales.form.customerHint')}</p>
          <div>
            <label htmlFor="qs-date" className="mb-1.5 block text-sm font-medium text-slate-800">{t('quickSales.form.date')}</label>
            <input
              id="qs-date"
              type="date"
              value={date}
              max={todayText()}
              onChange={(e) => setDate(e.target.value)}
              className="block min-h-12 w-full rounded-lg border border-slate-300 bg-white px-3 text-base focus-visible:outline-2 focus-visible:outline-emerald-600"
            />
            {serverErrors.sale_date && <p role="alert" className="mt-1.5 text-sm font-medium text-red-700">{serverErrors.sale_date}</p>}
          </div>
        </section>
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <PaymentPanel
            value={payment}
            onChange={setPayment}
            credit={null}
            upiId={shop.data?.upi_id ?? null}
            amountError={serverErrors.amount_paid}
            methodError={serverErrors.payment_method}
            needsCustomer={false}
          />
        </section>
        <div className="flex flex-col gap-3">
          <Button loading={save.isPending && save.variables === true} disabled={save.isPending} onClick={() => submit(true)}>
            {t('quickSales.form.saveAndPost')}
          </Button>
          <Button type="submit" variant="secondary" loading={save.isPending && save.variables === false} disabled={save.isPending}>
            {t('quickSales.form.saveDraft')}
          </Button>
          <LinkButton to={sale ? `/quick-sales/${sale.id}` : '/quick-sales'} variant="secondary">{t('common.cancel')}</LinkButton>
        </div>
      </aside>
    </form>
  )
}
