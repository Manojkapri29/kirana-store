import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { getShop } from '@/api/catalog'
import { getCustomer } from '@/api/customers'
import { createSale, getSale, postSale, replaceSaleItems, updateSale } from '@/api/sales'
import type { Product, Sale, SaleHeaderPayload } from '@/api/types'
import { TextAreaField, TextField } from '@/components/fields'
import { Alert, Button, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { CURRENCY_SYMBOL, formatMoney, formatQuantity } from '@/lib/format'

import { CustomerPicker, type PickedCustomer } from './CustomerPicker'
import { invalidateSaleData } from './invalidate'
import { PaymentPanel } from './PaymentPanel'
import { ProductSearchBox } from './ProductSearchBox'
import {
  amountPaidFor,
  emptyPayment,
  isSendable,
  itemPayload,
  lineFormatErrors,
  lineFromProduct,
  linesFromSale,
  partAmountProblem,
  paymentPayload,
  todayText,
  type CartLine,
  type LineErrors,
  type LineField,
  type PaymentValue,
} from './saleForm'
import { useSalePreview } from './useSalePreview'

const LINE_API_FIELDS: Record<string, LineField> = { quantity: 'quantity', unit_price: 'unit_price', discount: 'discount' }

export function SaleFormPage({ mode }: { mode: 'create' | 'edit' }) {
  const { t } = useTranslation()
  const saleId = Number(useParams().id)
  const validId = mode === 'create' || (Number.isInteger(saleId) && saleId > 0)
  const sale = useQuery({
    queryKey: ['sale', saleId],
    queryFn: () => getSale(saleId),
    enabled: mode === 'edit' && validId,
    gcTime: 0, // always edit the latest saved draft, never a cached older copy
  })
  const title = mode === 'create' ? t('sales.form.addTitle') : t('sales.form.editTitle')

  if (!validId || sale.isError) {
    const notFound = !validId || (sale.error instanceof ApiError && sale.error.status === 404)
    return (
      <div className="space-y-6">
        <PageHeader title={title} />
        {notFound ? <Alert tone="error">{t('sales.detail.notFound')}</Alert> : <QueryError onRetry={() => void sale.refetch()} />}
        <LinkButton to="/sales" variant="secondary">
          {t('sales.detail.backToList')}
        </LinkButton>
      </div>
    )
  }
  if (mode === 'edit' && !sale.data) return <Spinner />
  // Only a draft can be edited: a posted or void sale is shown, not changed.
  if (sale.data && sale.data.status !== 'DRAFT') return <Navigate to={`/sales/${sale.data.id}`} replace />

  return (
    <div className="space-y-6">
      <PageHeader title={title} subtitle={t('sales.form.subtitle')} />
      <Billing sale={sale.data} />
    </div>
  )
}

function Billing({ sale }: { sale: Sale | undefined }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const shop = useQuery({ queryKey: ['shop'], queryFn: getShop })

  const [draftId, setDraftId] = useState<number | undefined>(sale?.id)
  const [lines, setLines] = useState<CartLine[]>(() => (sale ? linesFromSale(sale) : []))
  // `undefined` means "not chosen yet": the customer asked for in the address (if any) is used until then.
  const [customerChoice, setCustomerChoice] = useState<PickedCustomer | null | undefined>(
    sale ? (sale.customer_id ? { id: sale.customer_id, name: sale.customer_name ?? '', phone: null } : null) : undefined,
  )
  const [saleDate, setSaleDate] = useState(sale?.sale_date ?? todayText())
  const [notes, setNotes] = useState(sale?.notes ?? '')
  const [billDiscount, setBillDiscount] = useState(sale && sale.discount !== '0.00' ? sale.discount : '')
  const [payment, setPayment] = useState<PaymentValue>(emptyPayment())
  const [lineErrors, setLineErrors] = useState<Record<string, LineErrors>>({})
  const [serverLines, setServerLines] = useState<Record<number, Partial<Record<LineField | 'product_id', string>>>>({})
  const [serverFields, setServerFields] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)

  // A customer can be asked for straight from the customer screen: /sales/new?customer=7
  const wantedId = Number(searchParams.get('customer'))
  const wanted = useQuery({
    queryKey: ['customer', wantedId],
    queryFn: () => getCustomer(wantedId),
    enabled: !sale && Number.isInteger(wantedId) && wantedId > 0,
  })
  const customer: PickedCustomer | null =
    customerChoice !== undefined ? customerChoice : wanted.data ? { id: wanted.data.id, name: wanted.data.name, phone: wanted.data.phone } : null

  const sendable = lines.map((line, index) => ({ line, index })).filter(({ line }) => isSendable(line))
  const discountText = billDiscount.trim() === '' ? null : billDiscount.trim()
  const preview = useSalePreview(
    sendable.map(({ line }) => itemPayload(line)),
    discountText,
    amountPaidFor(payment),
  )
  const priced = preview.data
  const previewFor = (index: number) => {
    const position = sendable.findIndex((entry) => entry.index === index)
    return position === -1 ? undefined : priced?.lines[position]
  }

  function addProduct(product: Product) {
    setLines((current) => {
      // Scanning the same item again adds one more of it, like a shop till.
      const existing = current.find((line) => line.product.id === product.id && line.discount === '')
      if (existing && existing.product.allows_decimal === false && /^\d+$/.test(existing.quantity)) {
        return current.map((line) => (line === existing ? { ...line, quantity: String(BigInt(line.quantity) + 1n) } : line))
      }
      return [...current, lineFromProduct(product)]
    })
    setServerLines({})
  }

  function setLine(key: string, changes: Partial<CartLine>) {
    setLines((current) => current.map((line) => (line.key === key ? { ...line, ...changes } : line)))
    setLineErrors((current) => ({ ...current, [key]: {} }))
    setServerLines({})
  }

  const lineMessage = (line: CartLine, index: number, field: LineField): string | undefined => {
    const code = lineErrors[line.key]?.[field]
    if (code) return t(`sales.form.validation.${code}`)
    const fromPreview = previewFor(index)?.errors.find((problem) => problem.field === field)
    return serverLines[index]?.[field] ?? fromPreview?.message
  }

  function showServerError(error: unknown) {
    if (!(error instanceof ApiError)) {
      setFormError(t('common.genericError'))
      return
    }
    const nextLines: Record<number, Partial<Record<LineField | 'product_id', string>>> = {}
    const nextFields: Record<string, string> = {}
    for (const [path, message] of Object.entries(error.pathErrors)) {
      const parts = path.split('.')
      if (parts[0] === 'items' && parts.length === 3) {
        const field = parts[2] === 'product_id' ? 'product_id' : LINE_API_FIELDS[parts[2]]
        if (field) (nextLines[Number(parts[1])] ??= {})[field] = message
      } else if (parts.length === 1) {
        nextFields[parts[0]] = message
      }
    }
    setServerLines(nextLines)
    setServerFields(nextFields)
    setFormError(error.message)
  }

  const save = useMutation({
    mutationFn: async (postAfter: boolean) => {
      const header: SaleHeaderPayload = {
        customer_id: customer?.id ?? null,
        sale_date: saleDate,
        notes: notes.trim() === '' ? null : notes.trim(),
        discount: discountText,
      }
      const items = lines.map(itemPayload)
      let saved: Sale
      if (draftId !== undefined) {
        await updateSale(draftId, header)
        saved = await replaceSaleItems(draftId, items)
      } else {
        saved = await createSale({ ...header, items })
        setDraftId(saved.id) // a failed post below must not create a second draft on retry
      }
      if (!postAfter) return saved
      return postSale(saved.id, paymentPayload(payment))
    },
    onSuccess: async (saved) => {
      await invalidateSaleData(queryClient)
      void navigate(`/sales/${saved.id}`)
    },
    onError: showServerError,
  })

  function submit(postAfter: boolean) {
    setFormError(null)
    setServerLines({})
    setServerFields({})
    if (lines.length === 0) {
      setFormError(t('sales.form.addSomething'))
      return
    }
    const errors: Record<string, LineErrors> = {}
    for (const line of lines) errors[line.key] = lineFormatErrors(line)
    setLineErrors(errors)
    if (Object.values(errors).some((e) => Object.keys(e).length > 0) || (postAfter && partAmountProblem(payment) !== null)) {
      setFormError(t('sales.form.fixErrors'))
      return
    }
    save.mutate(postAfter)
  }

  const needsCustomer = priced !== undefined && !/^0+(\.0+)?$/.test(priced.credit) && customer === null
  const shortLines = priced?.lines.filter((line) => line.short).length ?? 0

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
        {priced?.warnings.map((warning) => (
          <Alert key={warning} tone="warning">
            {warning}
          </Alert>
        ))}

        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">{t('sales.form.itemsHeading')}</h2>
          <ProductSearchBox onPick={addProduct} autoFocus />

          {lines.length === 0 && <p className="mt-6 text-center text-slate-500">{t('sales.form.emptyCart')}</p>}

          <ul className="mt-4 space-y-4">
            {lines.map((line, index) => {
              const p = previewFor(index)
              const productError = serverLines[index]?.product_id ?? p?.errors.find((e) => e.field === 'product_id')?.message
              return (
                <li key={line.key} className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                  <div className="mb-3 flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-semibold text-slate-900">{line.product.name}</p>
                      <p className="font-mono text-xs text-slate-500">{line.product.sku}</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => setLines((current) => current.filter((l) => l.key !== line.key))}
                      aria-label={t('sales.form.removeLine', { name: line.product.name })}
                      className="flex min-h-10 shrink-0 items-center gap-1.5 rounded-lg px-2 text-sm text-red-700 hover:bg-red-50"
                    >
                      <Trash2 aria-hidden="true" className="size-4" />
                      {t('sales.form.remove')}
                    </button>
                  </div>
                  {productError && <p role="alert" className="mb-2 text-sm font-medium text-red-700">{productError}</p>}
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                    <TextField
                      label={t('sales.form.fields.quantity')}
                      value={line.quantity}
                      inputMode="decimal"
                      onChange={(event) => setLine(line.key, { quantity: event.target.value })}
                      error={lineMessage(line, index, 'quantity')}
                      suffix={line.product.unit_code}
                    />
                    <TextField
                      label={t('sales.form.fields.price')}
                      value={line.unit_price}
                      inputMode="decimal"
                      onChange={(event) => setLine(line.key, { unit_price: event.target.value })}
                      error={lineMessage(line, index, 'unit_price')}
                      prefix={CURRENCY_SYMBOL}
                    />
                    <TextField
                      label={t('sales.form.fields.discount')}
                      value={line.discount}
                      inputMode="decimal"
                      onChange={(event) => setLine(line.key, { discount: event.target.value })}
                      error={lineMessage(line, index, 'discount')}
                      prefix={CURRENCY_SYMBOL}
                      optional
                    />
                  </div>
                  <div className="mt-3 flex flex-wrap items-baseline justify-between gap-2 border-t border-slate-200 pt-3">
                    <span className={`text-sm ${p?.short ? 'font-semibold text-red-700' : 'text-slate-600'}`}>
                      {p?.available != null &&
                        (p.short
                          ? t('sales.form.onlyAvailable', { stock: formatQuantity(p.available), unit: line.product.unit_code })
                          : t('sales.form.inStock', { stock: formatQuantity(p.available), unit: line.product.unit_code }))}
                    </span>
                    <span className="text-slate-700">
                      {t('sales.form.fields.lineTotal')}:{' '}
                      <span className="text-lg font-bold text-slate-900">{p?.line_total ? formatMoney(p.line_total) : '—'}</span>
                    </span>
                  </div>
                </li>
              )
            })}
          </ul>
        </section>
      </div>

      <aside className="space-y-6 lg:col-span-1">
        <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <CustomerPicker
            label={t('sales.form.customer')}
            value={customer}
            onChange={setCustomerChoice}
            error={serverFields.customer_id ?? (needsCustomer ? t('sales.payment.needsCustomer') : undefined)}
          />
          <div>
            <label htmlFor="sale-date" className="mb-1.5 block text-sm font-medium text-slate-800">
              {t('sales.form.fields.date')}
            </label>
            <input
              id="sale-date"
              type="date"
              value={saleDate}
              max={todayText()}
              onChange={(event) => setSaleDate(event.target.value)}
              className="block min-h-12 w-full rounded-lg border border-slate-300 bg-white px-3 text-base focus-visible:outline-2 focus-visible:outline-emerald-600"
            />
            {serverFields.sale_date && <p role="alert" className="mt-1.5 text-sm font-medium text-red-700">{serverFields.sale_date}</p>}
          </div>
          <TextAreaField label={t('sales.form.fields.notes')} value={notes} onChange={(event) => setNotes(event.target.value)} rows={2} maxLength={2000} optional />
        </section>

        <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t('sales.form.totalsHeading')}</h2>
          <TextField
            label={t('sales.form.fields.billDiscount')}
            hint={t('sales.form.hints.billDiscount')}
            value={billDiscount}
            inputMode="decimal"
            onChange={(event) => setBillDiscount(event.target.value)}
            error={serverFields.discount ?? priced?.errors.find((e) => e.field === 'discount')?.message}
            prefix={CURRENCY_SYMBOL}
            optional
          />
          <dl className="space-y-1.5">
            <div className="flex justify-between text-slate-700">
              <dt>{t('sales.form.subtotal')}</dt>
              <dd>{priced ? formatMoney(priced.subtotal) : '—'}</dd>
            </div>
            {priced && priced.discount !== '0.00' && (
              <div className="flex justify-between text-slate-700">
                <dt>{t('sales.form.billDiscountLine')}</dt>
                <dd>− {formatMoney(priced.discount)}</dd>
              </div>
            )}
            <div className="flex items-baseline justify-between border-t border-slate-200 pt-2">
              <dt className="text-lg font-semibold text-slate-900">{t('sales.form.total')}</dt>
              <dd className="text-3xl font-bold text-slate-900">{priced ? formatMoney(priced.total) : '—'}</dd>
            </div>
          </dl>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <PaymentPanel
            value={payment}
            onChange={setPayment}
            credit={priced?.credit ?? null}
            upiId={shop.data?.upi_id ?? null}
            amountError={serverFields.amount_paid ?? priced?.errors.find((e) => e.field === 'amount_paid')?.message}
            methodError={serverFields.payment_method}
            needsCustomer={needsCustomer}
          />
        </section>

        {shortLines > 0 && <Alert tone="warning">{t('sales.form.shortWarning')}</Alert>}

        <div className="flex flex-col gap-3">
          <Button loading={save.isPending && save.variables === true} disabled={save.isPending || lines.length === 0} onClick={() => submit(true)}>
            {t('sales.form.completeSale')}
          </Button>
          <Button
            type="submit"
            variant="secondary"
            loading={save.isPending && save.variables === false}
            disabled={save.isPending}
          >
            {t('sales.form.saveDraft')}
          </Button>
          <LinkButton to={sale ? `/sales/${sale.id}` : '/sales'} variant="secondary">
            {t('common.cancel')}
          </LinkButton>
        </div>
      </aside>
    </form>
  )
}
