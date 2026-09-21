import { useTranslation } from 'react-i18next'

import type { PaymentMethod } from '@/api/types'
import { SelectField, TextField } from '@/components/fields'
import { CURRENCY_SYMBOL, formatMoney } from '@/lib/format'

import { partAmountProblem, receivesMoney, type PaymentValue } from './saleForm'

interface PaymentPanelProps {
  value: PaymentValue
  onChange: (value: PaymentValue) => void
  /** How much would go on the customer's khata, as computed by the server for this payment. */
  credit: string | null
  /** The shop's UPI id, shown while taking a UPI payment. */
  upiId: string | null
  /** The server's message about the amount, if it refused it. */
  amountError?: string
  methodError?: string
  /** Show this when the customer is required but not chosen. */
  needsCustomer: boolean
}

/** How the customer pays: in full, or part now and the rest on credit. Amounts are the server's, not ours. */
export function PaymentPanel({ value, onChange, credit, upiId, amountError, methodError, needsCustomer }: PaymentPanelProps) {
  const { t } = useTranslation()
  const set = (changes: Partial<PaymentValue>) => onChange({ ...value, ...changes })
  const problem = partAmountProblem(value)
  const localAmountError = problem === null ? undefined : t(`sales.payment.validation.${problem}`)
  const hasCredit = credit !== null && !/^0+(\.0+)?$/.test(credit)

  return (
    <fieldset className="space-y-4">
      <legend className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t('sales.payment.heading')}</legend>
      <div className="flex flex-wrap gap-3">
        {(['full', 'part'] as const).map((mode) => (
          <label
            key={mode}
            className={`flex min-h-12 cursor-pointer items-center gap-2 rounded-lg border px-4 ${value.mode === mode ? 'border-emerald-600 bg-emerald-50' : 'border-slate-300 bg-white'}`}
          >
            <input type="radio" name="payment-mode" checked={value.mode === mode} onChange={() => set({ mode })} className="size-5 accent-emerald-600" />
            <span className="font-medium">{t(`sales.payment.modes.${mode}`)}</span>
          </label>
        ))}
      </div>

      {value.mode === 'part' && (
        <TextField
          label={t('sales.payment.amountNow')}
          hint={t('sales.payment.amountNowHint')}
          value={value.amount}
          onChange={(event) => set({ amount: event.target.value })}
          error={localAmountError ?? amountError}
          prefix={CURRENCY_SYMBOL}
          inputMode="decimal"
        />
      )}

      {hasCredit && (
        <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
          {t('sales.payment.onKhata', { amount: formatMoney(credit) })}
          {needsCustomer && <strong className="mt-1 block">{t('sales.payment.needsCustomer')}</strong>}
        </p>
      )}

      {receivesMoney(value) && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <SelectField
            label={t('sales.payment.method')}
            value={value.method}
            onChange={(event) => set({ method: event.target.value as PaymentMethod | '' })}
            error={methodError}
          >
            <option value="CASH">{t('sales.payment.methods.CASH')}</option>
            <option value="UPI">{t('sales.payment.methods.UPI')}</option>
            <option value="OTHER">{t('sales.payment.methods.OTHER')}</option>
          </SelectField>
          <TextField
            label={t('sales.payment.reference')}
            value={value.reference}
            onChange={(event) => set({ reference: event.target.value })}
            maxLength={100}
            optional
          />
          {value.method === 'UPI' && upiId && (
            <p className="text-sm text-slate-600 sm:col-span-2">{t('sales.payment.shopUpi', { upi: upiId })}</p>
          )}
        </div>
      )}
    </fieldset>
  )
}
