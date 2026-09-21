import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import { addAdjustment, addOpeningBalance, addPayment, reverseEntry } from '@/api/customers'
import type { KhataEntryResult, LedgerEntry, PaymentMethod } from '@/api/types'
import { SelectField, TextField } from '@/components/fields'
import { Alert, Button } from '@/components/ui'
import { CURRENCY_SYMBOL, formatMoney } from '@/lib/format'
import { paiseToText, toPaise } from '@/lib/money'

import { amountError } from './customerForm'

export type FormKind = 'payment' | 'opening' | 'adjustment'

/** Every khata write changes the balance and the history, so the screens that show either refetch. */
function useRefreshAfterWrite(customerId: number) {
  const queryClient = useQueryClient()
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['customers'] }),
      queryClient.invalidateQueries({ queryKey: ['customer', customerId] }),
      queryClient.invalidateQueries({ queryKey: ['customerLedger', customerId] }),
    ])
}

const today = (): string => {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

const DATE_INPUT =
  'block min-h-12 w-full rounded-lg border border-slate-300 bg-white px-3 text-base focus-visible:outline-2 focus-visible:outline-emerald-600'

function serverMessage(error: unknown, generic: string): { form: string; fields: Record<string, string> } {
  if (error instanceof ApiError) return { form: error.message, fields: error.fieldErrors }
  return { form: generic, fields: {} }
}

const Panel = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <div className="space-y-4 rounded-xl border border-emerald-200 bg-emerald-50/50 p-5">
    <h3 className="text-base font-semibold text-slate-900">{title}</h3>
    {children}
  </div>
)

interface FormProps {
  customerId: number
  onDone: () => void
}

/** Take a payment. Paying more than is owed is fine: the extra becomes an advance. */
export function PaymentForm({ customerId, outstanding, onDone }: FormProps & { outstanding: string }) {
  const { t } = useTranslation()
  const refresh = useRefreshAfterWrite(customerId)
  const [amount, setAmount] = useState('')
  const [date, setDate] = useState(today())
  const [method, setMethod] = useState<PaymentMethod | ''>('CASH')
  const [reference, setReference] = useState('')
  const [note, setNote] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () =>
      addPayment(customerId, {
        amount: amount.trim(),
        entry_date: date || null,
        payment_method: method || null,
        payment_reference: reference.trim() || null,
        note: note.trim() || null,
      }),
    onSuccess: async () => {
      await refresh()
      onDone()
    },
    onError: (error) => {
      const { form, fields } = serverMessage(error, t('common.genericError'))
      setErrors(fields)
      setFormError(Object.keys(fields).length ? t('customers.khata.fixErrors') : form)
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    const problem = amountError(amount)
    if (problem) {
      setErrors({ amount: t(`customers.khata.validation.${problem}`) })
      return
    }
    setErrors({})
    // More than is owed is allowed and becomes an advance, but the shopkeeper should mean it.
    const paid = toPaise(amount)
    const owed = toPaise(outstanding)
    if (paid !== null && owed !== null && paid > owed) {
      const extra = formatMoney(paiseToText(paid - owed))
      if (!window.confirm(t('customers.khata.overpayConfirm', { extra }))) return
    }
    save.mutate()
  }

  return (
    <Panel title={t('customers.khata.paymentTitle')}>
      <form onSubmit={submit} noValidate className="space-y-4">
        {formError && <Alert tone="error">{formError}</Alert>}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField
            label={t('customers.khata.fields.amount')}
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            error={errors.amount}
            hint={t('customers.khata.hints.paymentAmount')}
            prefix={CURRENCY_SYMBOL}
            inputMode="decimal"
            autoFocus
          />
          <div>
            <label className="mb-1.5 block text-sm font-medium text-slate-800" htmlFor="payment-date">
              {t('customers.khata.fields.date')}
            </label>
            <input id="payment-date" type="date" value={date} max={today()} onChange={(event) => setDate(event.target.value)} className={DATE_INPUT} />
            {errors.entry_date && <p role="alert" className="mt-1.5 text-sm font-medium text-red-700">{errors.entry_date}</p>}
          </div>
          <SelectField
            label={t('customers.khata.fields.method')}
            value={method}
            onChange={(event) => setMethod(event.target.value as PaymentMethod | '')}
            error={errors.payment_method}
          >
            <option value="CASH">{t('customers.khata.methods.CASH')}</option>
            <option value="UPI">{t('customers.khata.methods.UPI')}</option>
            <option value="OTHER">{t('customers.khata.methods.OTHER')}</option>
            <option value="">{t('customers.khata.methods.none')}</option>
          </SelectField>
          <TextField
            label={t('customers.khata.fields.reference')}
            value={reference}
            onChange={(event) => setReference(event.target.value)}
            error={errors.payment_reference}
            maxLength={100}
            optional
          />
          <div className="sm:col-span-2">
            <TextField
              label={t('customers.khata.fields.note')}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              error={errors.note}
              maxLength={500}
              optional
            />
          </div>
        </div>
        <FormButtons pending={save.isPending} submitLabel={t('customers.khata.savePayment')} onCancel={onDone} />
      </form>
    </Panel>
  )
}

/** What the customer already owed before the shop started using the system. Once per customer. */
export function OpeningBalanceForm({ customerId, onDone }: FormProps) {
  const { t } = useTranslation()
  const refresh = useRefreshAfterWrite(customerId)
  const [amount, setAmount] = useState('')
  const [date, setDate] = useState('')
  const [note, setNote] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () => addOpeningBalance(customerId, { amount: amount.trim(), entry_date: date || null, note: note.trim() || null }),
    onSuccess: async () => {
      await refresh()
      onDone()
    },
    onError: (error) => {
      const { form, fields } = serverMessage(error, t('common.genericError'))
      setErrors(fields)
      setFormError(Object.keys(fields).length && error instanceof ApiError && error.status === 422 ? t('customers.khata.fixErrors') : form)
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    const problem = amountError(amount)
    if (problem) {
      setErrors({ amount: t(`customers.khata.validation.${problem}`) })
      return
    }
    setErrors({})
    save.mutate()
  }

  return (
    <Panel title={t('customers.khata.openingTitle')}>
      <form onSubmit={submit} noValidate className="space-y-4">
        {formError && <Alert tone="error">{formError}</Alert>}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField
            label={t('customers.khata.fields.amount')}
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            error={errors.amount}
            hint={t('customers.khata.hints.openingAmount')}
            prefix={CURRENCY_SYMBOL}
            inputMode="decimal"
            autoFocus
          />
          <div>
            <label className="mb-1.5 block text-sm font-medium text-slate-800" htmlFor="opening-date">
              {t('customers.khata.fields.date')} <span className="font-normal text-slate-500">({t('common.optional')})</span>
            </label>
            <input id="opening-date" type="date" value={date} max={today()} onChange={(event) => setDate(event.target.value)} className={DATE_INPUT} />
          </div>
          <div className="sm:col-span-2">
            <TextField label={t('customers.khata.fields.note')} value={note} onChange={(event) => setNote(event.target.value)} maxLength={500} optional />
          </div>
        </div>
        <FormButtons pending={save.isPending} submitLabel={t('customers.khata.saveOpening')} onCancel={onDone} />
      </form>
    </Panel>
  )
}

/** A correction with a mandatory reason. Increase = the customer owes more; decrease = owes less. */
export function AdjustmentForm({ customerId, onDone }: FormProps) {
  const { t } = useTranslation()
  const refresh = useRefreshAfterWrite(customerId)
  const [direction, setDirection] = useState<'INCREASE' | 'DECREASE'>('INCREASE')
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () => addAdjustment(customerId, { amount: amount.trim(), direction, reason: reason.trim() }),
    onSuccess: async () => {
      await refresh()
      onDone()
    },
    onError: (error) => {
      const { form, fields } = serverMessage(error, t('common.genericError'))
      setErrors(fields)
      setFormError(Object.keys(fields).length ? t('customers.khata.fixErrors') : form)
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    const next: Record<string, string> = {}
    const problem = amountError(amount)
    if (problem) next.amount = t(`customers.khata.validation.${problem}`)
    if (reason.trim() === '') next.reason = t('customers.khata.validation.reasonRequired')
    setErrors(next)
    if (Object.keys(next).length === 0) save.mutate()
  }

  return (
    <Panel title={t('customers.khata.adjustmentTitle')}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <p className="text-sm text-slate-600">{t('customers.khata.adjustmentHint')}</p>
        {formError && <Alert tone="error">{formError}</Alert>}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <SelectField
            label={t('customers.khata.fields.direction')}
            value={direction}
            onChange={(event) => setDirection(event.target.value as 'INCREASE' | 'DECREASE')}
          >
            <option value="INCREASE">{t('customers.khata.directions.INCREASE')}</option>
            <option value="DECREASE">{t('customers.khata.directions.DECREASE')}</option>
          </SelectField>
          <TextField
            label={t('customers.khata.fields.amount')}
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            error={errors.amount}
            prefix={CURRENCY_SYMBOL}
            inputMode="decimal"
            autoFocus
          />
          <div className="sm:col-span-2">
            <TextField
              label={t('customers.khata.fields.reason')}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              error={errors.reason}
              maxLength={500}
            />
          </div>
        </div>
        <FormButtons pending={save.isPending} submitLabel={t('customers.khata.saveAdjustment')} onCancel={onDone} />
      </form>
    </Panel>
  )
}

/** Undo an entry with an equal and opposite one. The original stays in the history. */
export function ReverseForm({ customerId, entry, onDone }: { customerId: number; entry: LedgerEntry; onDone: () => void }) {
  const { t } = useTranslation()
  const refresh = useRefreshAfterWrite(customerId)
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const save = useMutation<KhataEntryResult>({
    mutationFn: () => reverseEntry(customerId, entry.id, reason.trim()),
    onSuccess: async () => {
      await refresh()
      onDone()
    },
    onError: (failure) => setError(failure instanceof ApiError ? failure.message : t('common.genericError')),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (reason.trim() === '') {
      setError(t('customers.khata.validation.reasonRequired'))
      return
    }
    save.mutate()
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4 rounded-xl border border-red-200 bg-red-50 p-5">
      <p className="font-medium text-red-900">{t('customers.khata.reverseWarning')}</p>
      <TextField
        label={t('customers.khata.fields.reason')}
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
        <Button type="submit" variant="danger" loading={save.isPending}>
          {t('customers.khata.confirmReverse')}
        </Button>
        <Button variant="secondary" onClick={onDone} disabled={save.isPending}>
          {t('common.cancel')}
        </Button>
      </div>
    </form>
  )
}

function FormButtons({ pending, submitLabel, onCancel }: { pending: boolean; submitLabel: string; onCancel: () => void }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
      <Button variant="secondary" onClick={onCancel} disabled={pending}>
        {t('common.cancel')}
      </Button>
      <Button type="submit" loading={pending}>
        {submitLabel}
      </Button>
    </div>
  )
}
