import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import {
  createPurchase,
  getPurchase,
  postPurchase,
  replacePurchaseItems,
  updatePurchaseHeader,
} from '@/api/purchases'
import { listSupplierOptions } from '@/api/suppliers'
import type { Purchase } from '@/api/types'
import { SelectField, TextAreaField, TextField } from '@/components/fields'
import { Alert, Button, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { CURRENCY_SYMBOL, formatMoney, formatQuantity } from '@/lib/format'
import { paiseToText } from '@/lib/money'

import { invalidatePurchaseData } from './invalidate'
import { ProductPicker } from './ProductPicker'
import {
  emptyHeader,
  emptyLine,
  grandTotalPaise,
  headerFromPurchase,
  headerPayload,
  itemPayloads,
  lineGrossPaise,
  linesFromPurchase,
  lineTotalPaise,
  todayText,
  validateHeader,
  validateLine,
  type HeaderErrors,
  type HeaderField,
  type HeaderValues,
  type LineErrors,
  type LineField,
  type LineValues,
} from './purchaseForm'

const HEADER_API_FIELDS: Record<string, HeaderField> = {
  supplier_id: 'supplier_id',
  purchase_date: 'purchase_date',
  supplier_invoice_no: 'supplier_invoice_no',
  notes: 'notes',
}
const LINE_API_FIELDS: Record<string, LineField> = {
  product_id: 'product',
  quantity: 'quantity',
  unit_cost: 'unit_cost',
  discount: 'discount',
}

export function PurchaseFormPage({ mode }: { mode: 'create' | 'edit' }) {
  const { t } = useTranslation()
  const purchaseId = Number(useParams().id)
  const validId = mode === 'create' || (Number.isInteger(purchaseId) && purchaseId > 0)
  const purchase = useQuery({
    queryKey: ['purchase', purchaseId],
    queryFn: () => getPurchase(purchaseId),
    enabled: mode === 'edit' && validId,
    gcTime: 0, // always edit the latest saved draft, never a cached older copy
  })

  const title = mode === 'create' ? t('purchases.form.addTitle') : t('purchases.form.editTitle')

  if (!validId || purchase.isError) {
    const notFound = !validId || (purchase.error instanceof ApiError && purchase.error.status === 404)
    return (
      <div className="space-y-6">
        <PageHeader title={title} />
        {notFound ? <Alert tone="error">{t('purchases.detail.notFound')}</Alert> : <QueryError onRetry={() => void purchase.refetch()} />}
        <LinkButton to="/purchases" variant="secondary">
          {t('purchases.detail.backToList')}
        </LinkButton>
      </div>
    )
  }
  if (mode === 'edit' && !purchase.data) return <Spinner />
  // Only a draft can be edited: a posted or void purchase is shown, not changed.
  if (purchase.data && purchase.data.status !== 'DRAFT') return <Navigate to={`/purchases/${purchase.data.id}`} replace />

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader title={title} subtitle={t('purchases.form.subtitle')} />
      <PurchaseForm purchase={purchase.data} />
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{title}</h2>
      <div className="mt-4">{children}</div>
    </section>
  )
}

function PurchaseForm({ purchase }: { purchase: Purchase | undefined }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const suppliers = useQuery({ queryKey: ['supplierOptions'], queryFn: listSupplierOptions })

  const [header, setHeader] = useState<HeaderValues>(() =>
    purchase ? headerFromPurchase(purchase) : emptyHeader(searchParams.get('supplier') ?? ''),
  )
  const [lines, setLines] = useState<LineValues[]>(() => (purchase?.items.length ? linesFromPurchase(purchase) : [emptyLine()]))
  const [headerErrors, setHeaderErrors] = useState<HeaderErrors>({})
  const [lineErrors, setLineErrors] = useState<Record<string, LineErrors>>({})
  const [serverHeader, setServerHeader] = useState<Partial<Record<HeaderField, string>>>({})
  const [serverLines, setServerLines] = useState<Record<number, Partial<Record<LineField, string>>>>({})
  const [formError, setFormError] = useState<string | null>(null)

  const total = grandTotalPaise(lines)

  function setHeaderField(field: HeaderField, value: string) {
    setHeader((current) => ({ ...current, [field]: value }))
    setHeaderErrors((current) => ({ ...current, [field]: undefined }))
    setServerHeader((current) => ({ ...current, [field]: undefined }))
  }

  /** Change one line. Server messages are index-based, so they are cleared for the whole form when lines move. */
  function setLine(key: string, changes: Partial<LineValues>) {
    setLines((current) => current.map((line) => (line.key === key ? { ...line, ...changes } : line)))
    setLineErrors((current) => ({ ...current, [key]: {} }))
    setServerLines({})
  }

  function pickProduct(key: string, product: LineValues['product']) {
    // A newly chosen product suggests its last purchase price, if it has one. Always editable.
    const changes: Partial<LineValues> = { product }
    const current = lines.find((line) => line.key === key)
    if (product?.purchase_price && current && current.unit_cost === '') changes.unit_cost = product.purchase_price
    setLine(key, changes)
  }

  function removeLine(key: string) {
    setLines((current) => (current.length > 1 ? current.filter((line) => line.key !== key) : [emptyLine()]))
    setServerLines({})
  }

  const headerError = (field: HeaderField): string | undefined => {
    const code = headerErrors[field]
    if (code) return t(`purchases.form.validation.${code}`)
    return serverHeader[field]
  }
  const lineError = (line: LineValues, index: number, field: LineField): string | undefined => {
    const code = lineErrors[line.key]?.[field]
    if (code) return t(`purchases.form.validation.${code}`)
    return serverLines[index]?.[field]
  }

  function showServerError(error: unknown) {
    if (!(error instanceof ApiError)) {
      setFormError(t('common.genericError'))
      return
    }
    const nextHeader: Partial<Record<HeaderField, string>> = {}
    const nextLines: Record<number, Partial<Record<LineField, string>>> = {}
    for (const [path, message] of Object.entries(error.pathErrors)) {
      const parts = path.split('.')
      if (parts[0] === 'items' && parts.length === 3) {
        const field = LINE_API_FIELDS[parts[2]]
        if (field) (nextLines[Number(parts[1])] ??= {})[field] = message
      } else if (parts.length === 1 && HEADER_API_FIELDS[parts[0]]) {
        nextHeader[HEADER_API_FIELDS[parts[0]]] = message
      }
    }
    setServerHeader(nextHeader)
    setServerLines(nextLines)
    const placed = Object.keys(nextHeader).length + Object.keys(nextLines).length > 0
    setFormError(placed ? t('purchases.form.fixErrors') : error.message)
  }

  const save = useMutation({
    mutationFn: async (postAfter: boolean) => {
      let saved: Purchase
      if (purchase) {
        await updatePurchaseHeader(purchase.id, headerPayload(header))
        saved = await replacePurchaseItems(purchase.id, itemPayloads(lines))
      } else {
        saved = await createPurchase({ ...headerPayload(header), items: itemPayloads(lines) })
      }
      if (!postAfter) return { purchase: saved, postError: null as string | null }
      try {
        return { purchase: await postPurchase(saved.id), postError: null as string | null }
      } catch (error) {
        // The draft is saved; only the posting failed. Say so, and show the draft.
        return { purchase: saved, postError: error instanceof ApiError ? error.message : t('common.genericError') }
      }
    },
    onSuccess: async ({ purchase: saved, postError }) => {
      await invalidatePurchaseData(queryClient)
      void navigate(`/purchases/${saved.id}`, { state: postError ? { postError } : undefined })
    },
    onError: showServerError,
  })

  function submit(postAfter: boolean) {
    setFormError(null)
    setServerHeader({})
    setServerLines({})

    const nextHeaderErrors = validateHeader(header, todayText())
    const nextLineErrors: Record<string, LineErrors> = {}
    for (const line of lines) nextLineErrors[line.key] = validateLine(line)
    setHeaderErrors(nextHeaderErrors)
    setLineErrors(nextLineErrors)

    const hasErrors =
      Object.keys(nextHeaderErrors).length > 0 || Object.values(nextLineErrors).some((e) => Object.keys(e).length > 0)
    if (hasErrors) {
      setFormError(t('purchases.form.fixErrors'))
      return
    }
    if (postAfter && !window.confirm(t('purchases.form.postConfirm'))) return
    save.mutate(postAfter)
  }

  return (
    <form
      onSubmit={(event: FormEvent) => {
        event.preventDefault()
        submit(false)
      }}
      noValidate
      className="space-y-6"
    >
      {formError && <Alert tone="error">{formError}</Alert>}

      <Section title={t('purchases.form.headerSection')}>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <SelectField
            label={t('purchases.form.fields.supplier')}
            value={header.supplier_id}
            onChange={(event) => setHeaderField('supplier_id', event.target.value)}
            error={headerError('supplier_id')}
          >
            <option value="">{t('purchases.form.chooseSupplier')}</option>
            {suppliers.data?.map((supplier) => (
              <option key={supplier.id} value={supplier.id}>
                {supplier.name}
              </option>
            ))}
          </SelectField>
          <TextField
            label={t('purchases.form.fields.date')}
            type="date"
            value={header.purchase_date}
            max={todayText()}
            onChange={(event) => setHeaderField('purchase_date', event.target.value)}
            error={headerError('purchase_date')}
          />
          <TextField
            label={t('purchases.form.fields.invoiceNo')}
            value={header.supplier_invoice_no}
            onChange={(event) => setHeaderField('supplier_invoice_no', event.target.value)}
            error={headerError('supplier_invoice_no')}
            hint={t('purchases.form.hints.invoiceNo')}
            maxLength={50}
            optional
          />
          <div className="sm:col-span-2">
            <TextAreaField
              label={t('purchases.form.fields.notes')}
              value={header.notes}
              onChange={(event) => setHeaderField('notes', event.target.value)}
              error={headerError('notes')}
              maxLength={2000}
              optional
            />
          </div>
        </div>
      </Section>

      <Section title={t('purchases.form.itemsSection')}>
        <ul className="space-y-4">
          {lines.map((line, index) => {
            const gross = lineGrossPaise(line)
            const lineTotal = lineTotalPaise(line)
            const unit = line.product?.unit_code
            return (
              <li key={line.key} className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <p className="text-sm font-semibold text-slate-700">{t('purchases.form.lineNumber', { n: index + 1 })}</p>
                  <button
                    type="button"
                    onClick={() => removeLine(line.key)}
                    aria-label={t('purchases.form.removeLine', { n: index + 1 })}
                    className="flex min-h-10 items-center gap-1.5 rounded-lg px-2 text-sm text-red-700 hover:bg-red-50"
                  >
                    <Trash2 aria-hidden="true" className="size-4" />
                    {t('purchases.form.remove')}
                  </button>
                </div>
                <div className="space-y-4">
                  <ProductPicker
                    label={t('purchases.form.fields.product')}
                    value={line.product}
                    onChange={(product) => pickProduct(line.key, product)}
                    error={lineError(line, index, 'product')}
                    autoFocus={index === lines.length - 1 && lines.length > 1}
                  />
                  {line.product && line.product.current_stock !== '' && (
                    <p className="text-sm text-slate-600">
                      {t('purchases.form.productInfo', {
                        stock: formatQuantity(line.product.current_stock),
                        unit: line.product.unit_code,
                        cost: formatMoney(line.product.avg_cost),
                      })}
                    </p>
                  )}
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                    <TextField
                      label={t('purchases.form.fields.quantity')}
                      value={line.quantity}
                      inputMode="decimal"
                      onChange={(event) => setLine(line.key, { quantity: event.target.value })}
                      error={lineError(line, index, 'quantity')}
                      suffix={unit}
                    />
                    <TextField
                      label={t('purchases.form.fields.price')}
                      value={line.unit_cost}
                      inputMode="decimal"
                      onChange={(event) => setLine(line.key, { unit_cost: event.target.value })}
                      error={lineError(line, index, 'unit_cost')}
                      prefix={CURRENCY_SYMBOL}
                      hint={t('purchases.form.hints.price')}
                    />
                    <TextField
                      label={t('purchases.form.fields.discount')}
                      value={line.discount}
                      inputMode="decimal"
                      onChange={(event) => setLine(line.key, { discount: event.target.value })}
                      error={lineError(line, index, 'discount')}
                      prefix={CURRENCY_SYMBOL}
                      hint={t('purchases.form.hints.discount')}
                      optional
                    />
                  </div>
                  <p className="flex flex-wrap items-baseline justify-between gap-2 border-t border-slate-200 pt-3">
                    <span className="text-sm text-slate-600">
                      {gross !== null && lineTotal !== null && gross !== lineTotal
                        ? t('purchases.form.beforeDiscount', { amount: formatMoney(paiseToText(gross)) })
                        : ''}
                    </span>
                    <span className="text-slate-700">
                      {t('purchases.form.fields.lineTotal')}:{' '}
                      <span className="text-lg font-bold text-slate-900">
                        {lineTotal === null ? '—' : formatMoney(paiseToText(lineTotal))}
                      </span>
                    </span>
                  </p>
                </div>
              </li>
            )
          })}
        </ul>
        <div className="mt-4">
          <Button variant="secondary" onClick={() => setLines((current) => [...current, emptyLine()])}>
            <Plus aria-hidden="true" className="size-5" />
            {t('purchases.form.addLine')}
          </Button>
        </div>
        <div className="mt-6 flex items-baseline justify-between gap-3 border-t border-slate-200 pt-4">
          <span className="text-slate-700">{t('purchases.form.grandTotal')}</span>
          <span className="text-2xl font-bold text-slate-900">{formatMoney(paiseToText(total))}</span>
        </div>
      </Section>

      <Alert tone="info">{t('purchases.form.draftNote')}</Alert>

      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
        <LinkButton to={purchase ? `/purchases/${purchase.id}` : '/purchases'} variant="secondary">
          {t('common.cancel')}
        </LinkButton>
        <Button type="submit" variant="secondary" loading={save.isPending && save.variables === false} disabled={save.isPending}>
          {t('purchases.form.saveDraft')}
        </Button>
        <Button loading={save.isPending && save.variables === true} disabled={save.isPending} onClick={() => submit(true)}>
          {t('purchases.form.savePost')}
        </Button>
      </div>
    </form>
  )
}
