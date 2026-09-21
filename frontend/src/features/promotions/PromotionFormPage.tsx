import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { getShop, listCategories } from '@/api/catalog'
import { createPromotion, getPromotion, updatePromotion } from '@/api/promotions'
import { listProducts } from '@/api/products'
import type { Promotion, PromotionAudience, PromotionScope, PromotionType } from '@/api/types'
import { SelectField, TextAreaField, TextField } from '@/components/fields'
import { SearchInput } from '@/components/SearchInput'
import { ErrorNotice } from '@/components/ErrorNotice'
import { RestoreBanner } from '@/components/RestoreBanner'
import { useFailure, needsNotice } from '@/hooks/useFailure'
import { useFormBackup } from '@/hooks/useFormBackup'
import { useIdempotencyKey } from '@/lib/idempotency'
import { Alert, Button, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { CustomerPicker } from '@/features/sales/CustomerPicker'
import { useEntitlements } from '@/features/subscription/useEntitlements'
import { useDebounced } from '@/hooks/useDebounced'
import { CURRENCY_SYMBOL } from '@/lib/format'

import {
  emptyPromotion,
  numberProblems,
  promotionPayload,
  valuesFromPromotion,
  type Named,
  type PromotionFormValues,
} from './promotionForm'

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{title}</h2>
      {children}
    </section>
  )
}

function Chips({ items, onRemove, empty }: { items: Named[]; onRemove: (id: number) => void; empty: string }) {
  const { t } = useTranslation()
  if (items.length === 0) return <p className="text-sm text-slate-500">{empty}</p>
  return (
    <ul className="flex flex-wrap gap-2">
      {items.map((item) => (
        <li key={item.id} className="flex items-center gap-1 rounded-full bg-emerald-100 py-1 pl-3 pr-1 text-sm text-emerald-900">
          {item.name}
          <button type="button" onClick={() => onRemove(item.id)} aria-label={`${t('promotions.form.remove')} ${item.name}`} className="flex size-7 items-center justify-center rounded-full hover:bg-emerald-200">
            <X aria-hidden="true" className="size-4" />
          </button>
        </li>
      ))}
    </ul>
  )
}

function ProductAdder({ chosen, onAdd }: { chosen: Named[]; onAdd: (p: Named) => void }) {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const q = useDebounced(search.trim(), 250)
  const results = useQuery({ queryKey: ['products', { promoPicker: q }], queryFn: () => listProducts({ q, status: 'active', limit: 6 }), enabled: q !== '' })
  return (
    <div>
      <SearchInput value={search} onChange={setSearch} placeholder={t('promotions.form.pickProducts')} />
      {results.data && results.data.items.length > 0 && (
        <ul className="mt-2 divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
          {results.data.items
            .filter((p) => !chosen.some((c) => c.id === p.id))
            .map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  onClick={() => {
                    onAdd({ id: p.id, name: p.name })
                    setSearch('')
                  }}
                  className="flex min-h-11 w-full items-center justify-between px-3 py-2 text-left hover:bg-emerald-50"
                >
                  <span className="font-medium">{p.name}</span>
                  <span className="font-mono text-xs text-slate-500">{p.sku}</span>
                </button>
              </li>
            ))}
        </ul>
      )}
    </div>
  )
}

export function PromotionFormPage({ mode }: { mode: 'create' | 'edit' }) {
  const { t } = useTranslation()
  const id = Number(useParams().id)
  const validId = mode === 'create' || (Number.isInteger(id) && id > 0)
  const promotion = useQuery({ queryKey: ['promotion', id], queryFn: () => getPromotion(id), enabled: mode === 'edit' && validId, gcTime: 0 })
  const shop = useQuery({ queryKey: ['shop'], queryFn: getShop })
  const title = mode === 'create' ? t('promotions.form.addTitle') : t('promotions.form.editTitle')

  if (!validId || promotion.isError) {
    const notFound = !validId || (promotion.error instanceof ApiError && promotion.error.status === 404)
    return (
      <div className="space-y-6">
        <PageHeader title={title} />
        {notFound ? <Alert tone="error">{t('promotions.detail.notFound')}</Alert> : <QueryError error={promotion.error} onRetry={() => void promotion.refetch()} />}
        <LinkButton to="/promotions" variant="secondary">{t('promotions.detail.backToList')}</LinkButton>
      </div>
    )
  }
  if ((mode === 'edit' && !promotion.data) || !shop.data) return <Spinner />
  return (
    <div className="space-y-6">
      <PageHeader title={title} subtitle={t('promotions.form.subtitle')} />
      <Form promotion={promotion.data} timeZone={shop.data.timezone} />
    </div>
  )
}

function Form({ promotion, timeZone }: { promotion: Promotion | undefined; timeZone: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { allows } = useEntitlements()
  const mode = promotion ? 'edit' : 'create'
  const [v, setV] = useState<PromotionFormValues>(() => (promotion ? valuesFromPromotion(promotion, timeZone) : emptyPromotion()))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const { failure, setFailure } = useFailure()
  const idem = useIdempotencyKey()
  const backup = useFormBackup('promotion.new', v, { enabled: mode === 'create' })
  const categories = useQuery({ queryKey: ['categories'], queryFn: listCategories })
  const set = (changes: Partial<PromotionFormValues>) => setV((current) => ({ ...current, ...changes }))

  const save = useMutation({
    mutationFn: () => {
      const payload = promotionPayload(v, mode)
      if (promotion) return updatePromotion(promotion.id, payload)
      return createPromotion(payload, { idempotencyKey: idem.keyFor(JSON.stringify(payload)) })
    },
    onSuccess: async (saved) => {
      idem.renew()
      backup.clear()
      await Promise.all(['promotions', 'promotion'].map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
      void navigate(`/promotions/${saved.id}`)
    },
    onError: (error) => {
      if (needsNotice(error)) {
        setFailure(error)
        return
      }
      if (error instanceof ApiError) {
        setErrors(error.fieldErrors)
        setFormError(error.status === 403 ? t('promotions.planNeeded') : error.message)
      } else setFormError(t('common.genericError'))
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    setFailure(null)
    const local: Record<string, string> = {}
    for (const field of numberProblems(v)) local[field] = t('promotions.form.needNumber')
    setErrors(local)
    if (Object.keys(local).length > 0) {
      setFormError(t('promotions.form.fixErrors'))
      return
    }
    save.mutate()
  }

  const type = v.promo_type
  const itemsOnly = type === 'OFFER_PRICE' || type === 'BUY_X_GET_Y'
  const fixed = mode === 'edit'
  const err = (name: string) => errors[name]

  return (
    <form onSubmit={submit} noValidate className="grid grid-cols-1 gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        {backup.restorable && (
          <RestoreBanner
            onRestore={() => {
              setV(backup.restorable!)
              backup.dismiss()
            }}
            onDiscard={backup.clear}
          />
        )}
        {formError && <Alert tone="error">{formError}</Alert>}
        {failure !== null && (
          <ErrorNotice error={failure} context="save" safeToRepeat={true} retry={() => save.mutate()} cancel={() => void navigate('/promotions')} />
        )}
        {allows('promotions') === false && <Alert tone="warning">{t('promotions.planNeeded')}</Alert>}
        {fixed && <Alert tone="info">{t('promotions.form.typeFixed')}</Alert>}

        <Section title={t('promotions.form.basics')}>
          <TextField label={t('promotions.form.name')} hint={t('promotions.form.nameHint')} value={v.name} onChange={(e) => set({ name: e.target.value })} error={err('name')} maxLength={120} autoFocus />
          <TextAreaField label={t('promotions.form.description')} value={v.description} onChange={(e) => set({ description: e.target.value })} error={err('description')} rows={2} maxLength={1000} optional />
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <SelectField label={t('promotions.form.kind')} value={v.promo_type} disabled={fixed} onChange={(e) => set({ promo_type: e.target.value as PromotionType, scope: e.target.value === 'OFFER_PRICE' || e.target.value === 'BUY_X_GET_Y' ? (v.scope === 'CART' ? 'PRODUCTS' : v.scope) : v.scope })}>
              {(['PERCENT', 'AMOUNT', 'OFFER_PRICE', 'BUY_X_GET_Y'] as const).map((k) => (
                <option key={k} value={k}>{t(`promotions.types.${k}`)}</option>
              ))}
            </SelectField>
            <SelectField label={t('promotions.form.scope')} value={v.scope} disabled={fixed} error={err('scope')} onChange={(e) => set({ scope: e.target.value as PromotionScope })}>
              {(['CART', 'PRODUCTS', 'CATEGORIES'] as const)
                .filter((s) => !(itemsOnly && s === 'CART'))
                .map((s) => (
                  <option key={s} value={s}>{t(`promotions.scopes.${s}`)}</option>
                ))}
            </SelectField>
          </div>
        </Section>

        <Section title={t('promotions.form.benefit')}>
          {type === 'PERCENT' && <TextField label={t('promotions.form.percent')} hint={t('promotions.form.percentHint')} value={v.percent} inputMode="decimal" onChange={(e) => set({ percent: e.target.value })} error={err('percent')} suffix="%" />}
          {type === 'AMOUNT' && <TextField label={t('promotions.form.amount')} value={v.amount} inputMode="decimal" onChange={(e) => set({ amount: e.target.value })} error={err('amount')} prefix={CURRENCY_SYMBOL} />}
          {type === 'OFFER_PRICE' && <TextField label={t('promotions.form.offerPrice')} hint={t('promotions.form.offerPriceHint')} value={v.offer_price} inputMode="decimal" onChange={(e) => set({ offer_price: e.target.value })} error={err('offer_price')} prefix={CURRENCY_SYMBOL} />}
          {type === 'BUY_X_GET_Y' && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <TextField label={t('promotions.form.buy')} value={v.buy_quantity} inputMode="numeric" onChange={(e) => set({ buy_quantity: e.target.value })} error={err('buy_quantity')} />
              <TextField label={t('promotions.form.get')} value={v.get_quantity} inputMode="numeric" onChange={(e) => set({ get_quantity: e.target.value })} error={err('get_quantity')} />
              <TextField label={t('promotions.form.getPercent')} hint={t('promotions.form.getPercentHint')} value={v.get_percent} inputMode="decimal" onChange={(e) => set({ get_percent: e.target.value })} error={err('get_percent')} suffix="%" optional />
            </div>
          )}
        </Section>

        {v.scope !== 'CART' && (
          <Section title={t('promotions.form.target')}>
            {v.scope === 'PRODUCTS' && (
              <>
                <ProductAdder chosen={v.products} onAdd={(p) => set({ products: [...v.products, p] })} />
                <Chips items={v.products} empty={t('promotions.form.noneChosen')} onRemove={(pid) => set({ products: v.products.filter((p) => p.id !== pid) })} />
                {err('product_ids') && <p role="alert" className="text-sm font-medium text-red-700">{err('product_ids')}</p>}
              </>
            )}
            {v.scope === 'CATEGORIES' && (
              <>
                <fieldset className="flex flex-wrap gap-2">
                  <legend className="sr-only">{t('promotions.form.pickCategories')}</legend>
                  {categories.data?.filter((c) => c.is_active).map((c) => (
                    <label key={c.id} className={`flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3 ${v.category_ids.includes(c.id) ? 'border-emerald-600 bg-emerald-50' : 'border-slate-300'}`}>
                      <input type="checkbox" className="size-5 accent-emerald-600" checked={v.category_ids.includes(c.id)} onChange={(e) => set({ category_ids: e.target.checked ? [...v.category_ids, c.id] : v.category_ids.filter((x) => x !== c.id) })} />
                      {c.name}
                    </label>
                  ))}
                </fieldset>
                {err('category_ids') && <p role="alert" className="text-sm font-medium text-red-700">{err('category_ids')}</p>}
              </>
            )}
          </Section>
        )}

        <Section title={t('promotions.form.conditions')}>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <TextField label={t('promotions.form.minCart')} value={v.min_cart_value} inputMode="decimal" onChange={(e) => set({ min_cart_value: e.target.value })} error={err('min_cart_value')} prefix={CURRENCY_SYMBOL} optional />
            <TextField label={t('promotions.form.minQuantity')} value={v.min_quantity} inputMode="decimal" onChange={(e) => set({ min_quantity: e.target.value })} error={err('min_quantity')} optional />
            <TextField label={t('promotions.form.maxDiscount')} value={v.max_discount} inputMode="decimal" onChange={(e) => set({ max_discount: e.target.value })} error={err('max_discount')} prefix={CURRENCY_SYMBOL} optional />
            <TextField label={t('promotions.form.couponCode')} hint={t('promotions.form.couponHint')} value={v.coupon_code} onChange={(e) => set({ coupon_code: e.target.value.toUpperCase() })} error={err('coupon_code')} maxLength={30} optional />
            <TextField label={t('promotions.form.usageLimit')} value={v.usage_limit} inputMode="numeric" onChange={(e) => set({ usage_limit: e.target.value })} error={err('usage_limit')} optional />
            <TextField label={t('promotions.form.perCustomer')} value={v.per_customer_limit} inputMode="numeric" onChange={(e) => set({ per_customer_limit: e.target.value })} error={err('per_customer_limit')} optional />
          </div>
        </Section>
      </div>

      <aside className="space-y-6">
        <Section title={t('promotions.form.audience')}>
          <SelectField label={t('promotions.form.audience')} value={v.audience} onChange={(e) => set({ audience: e.target.value as PromotionAudience })}>
            {(['ALL', 'NEW_CUSTOMER', 'CUSTOMERS'] as const).map((a) => (
              <option key={a} value={a}>{t(`promotions.audiences.${a}`)}</option>
            ))}
          </SelectField>
          {v.audience === 'CUSTOMERS' && (
            <>
              <CustomerPicker
                label={t('promotions.form.pickCustomers')}
                value={null}
                onChange={(c) => c && !v.customers.some((x) => x.id === c.id) && set({ customers: [...v.customers, { id: c.id, name: c.name }] })}
              />
              <Chips items={v.customers} empty={t('promotions.form.noneChosen')} onRemove={(cid) => set({ customers: v.customers.filter((c) => c.id !== cid) })} />
              {err('customer_ids') && <p role="alert" className="text-sm font-medium text-red-700">{err('customer_ids')}</p>}
            </>
          )}
        </Section>

        <Section title={t('promotions.form.timing')}>
          <TextField label={t('promotions.form.startsAt')} type="datetime-local" value={v.starts_at} onChange={(e) => set({ starts_at: e.target.value })} error={err('starts_at')} optional />
          <TextField label={t('promotions.form.endsAt')} hint={t('promotions.form.datesHint')} type="datetime-local" value={v.ends_at} onChange={(e) => set({ ends_at: e.target.value })} error={err('ends_at')} optional />
          <TextField label={t('promotions.form.priority')} hint={t('promotions.form.priorityHint')} value={v.priority} inputMode="numeric" onChange={(e) => set({ priority: e.target.value })} error={err('priority')} />
          <label className="flex cursor-pointer items-start gap-3">
            <input type="checkbox" className="mt-1 size-5 accent-emerald-600" checked={v.stackable} onChange={(e) => set({ stackable: e.target.checked })} />
            <span>
              <span className="block font-medium text-slate-900">{t('promotions.form.stackable')}</span>
              <span className="block text-sm text-slate-600">{t('promotions.form.stackableHint')}</span>
            </span>
          </label>
        </Section>

        <div className="flex flex-col gap-3">
          <Button type="submit" loading={save.isPending}>{t('promotions.form.save')}</Button>
          <LinkButton to={promotion ? `/promotions/${promotion.id}` : '/promotions'} variant="secondary">{t('common.cancel')}</LinkButton>
        </div>
      </aside>
    </form>
  )
}
