import { useMutation, useQueryClient } from '@tanstack/react-query'
import { PackagePlus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import { createOpeningStock } from '@/api/inventory'
import type { Product } from '@/api/types'
import { TextField } from '@/components/fields'
import { Alert, Button } from '@/components/ui'
import { checkDecimal, isWhole, isZero } from '@/lib/decimal'

import { MONEY_DECIMALS, QUANTITY_DECIMALS } from './productForm'

type Errors = Partial<Record<'quantity' | 'cost', string>>

/** Records the starting stock of a product that has no stock history yet. */
export function OpeningStockForm({ product }: { product: Product }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [quantity, setQuantity] = useState('')
  const [cost, setCost] = useState(product.purchase_price ?? '') // a suggestion; the user may clear it
  const [note, setNote] = useState('')
  const [errors, setErrors] = useState<Errors>({})
  const [formError, setFormError] = useState<string | null>(null)

  const validation = (key: string, params?: Record<string, string | number>) =>
    t(`products.form.validation.${key as 'required'}`, params)

  const save = useMutation({
    mutationFn: () =>
      createOpeningStock({
        product_id: product.id,
        quantity: quantity.trim(),
        unit_cost: cost.trim() === '' ? null : cost.trim(),
        note: note.trim() === '' ? null : note.trim(),
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['product', product.id] }),
        queryClient.invalidateQueries({ queryKey: ['history', product.id] }),
        queryClient.invalidateQueries({ queryKey: ['products'] }),
        queryClient.invalidateQueries({ queryKey: ['inventory'] }),
      ])
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        setErrors({ quantity: error.fieldErrors.quantity, cost: error.fieldErrors.unit_cost })
        setFormError(error.fieldErrors.quantity || error.fieldErrors.unit_cost ? null : error.message)
      } else {
        setFormError(t('common.genericError'))
      }
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    const next: Errors = {}

    const quantityCheck = checkDecimal(quantity, QUANTITY_DECIMALS)
    if (quantityCheck === 'empty' || (quantityCheck === 'ok' && isZero(quantity))) next.quantity = validation('required')
    else if (quantityCheck === 'invalid') next.quantity = validation('invalidNumber')
    else if (quantityCheck === 'tooManyDecimals') next.quantity = validation('tooManyDecimals', { places: QUANTITY_DECIMALS })
    else if (!product.unit_allows_decimal && !isWhole(quantity)) next.quantity = validation('wholeNumber', { unit: product.unit_name })

    const costCheck = checkDecimal(cost, MONEY_DECIMALS)
    if (costCheck === 'invalid') next.cost = validation('invalidNumber')
    else if (costCheck === 'tooManyDecimals') next.cost = validation('tooManyDecimals', { places: MONEY_DECIMALS })

    setErrors(next)
    if (Object.keys(next).length === 0) save.mutate()
  }

  return (
    <section className="rounded-xl border border-emerald-200 bg-emerald-50/40 p-5 shadow-sm">
      <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
        <PackagePlus aria-hidden="true" className="size-5 text-emerald-700" />
        {t('products.openingStock.heading')}
      </h2>
      <p className="mt-1 text-sm text-slate-600">{t('products.openingStock.description')}</p>

      <form onSubmit={submit} noValidate className="mt-4 space-y-4">
        {formError && <Alert tone="error">{formError}</Alert>}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField
            label={t('products.openingStock.quantity')}
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
            error={errors.quantity}
            inputMode="decimal"
            suffix={product.unit_code}
            autoFocus
          />
          <TextField
            label={t('products.openingStock.costPerUnit')}
            hint={t('products.openingStock.costHint')}
            optional
            value={cost}
            onChange={(event) => setCost(event.target.value)}
            error={errors.cost}
            inputMode="decimal"
            prefix="₹"
          />
          <div className="sm:col-span-2">
            <TextField
              label={t('products.openingStock.note')}
              optional
              value={note}
              onChange={(event) => setNote(event.target.value)}
              maxLength={500}
            />
          </div>
        </div>
        <Button type="submit" loading={save.isPending}>
          {save.isPending ? t('common.saving') : t('products.openingStock.submit')}
        </Button>
      </form>
    </section>
  )
}
