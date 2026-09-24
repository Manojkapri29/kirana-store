import { useMutation, useQuery } from '@tanstack/react-query'
import { Minus, Plus, ShoppingBasket } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { type Fulfilment, getPublicProducts, getPublicStore, type PayHow, placePublicOrder, type PublicProduct } from '@/api/onlineStore'
import { LanguageSwitcher } from '@/components/LanguageSwitcher'
import { SearchInput } from '@/components/SearchInput'
import { TextAreaField, TextField } from '@/components/fields'
import { buttonClasses } from '@/components/buttonStyles'
import { Alert, Spinner } from '@/components/ui'
import { ApiError } from '@/api/client'
import { useIdempotencyKey } from '@/lib/idempotency'
import { formatMoney, formatQuantity } from '@/lib/format'

// The cart lives in this browser only. Prices shown here are for display: the server prices the order again from its own data.
type Cart = Record<number, { product: PublicProduct; quantity: number }>
const cartKey = (slug: string) => `kirana.store.cart:${slug}`

function loadCart(slug: string): Cart {
  try {
    const raw = localStorage.getItem(cartKey(slug))
    return raw ? (JSON.parse(raw) as Cart) : {}
  } catch {
    return {}
  }
}
function saveCart(slug: string, cart: Cart) {
  try {
    localStorage.setItem(cartKey(slug), JSON.stringify(cart))
  } catch {
    // The cart is a convenience; without storage it simply lasts until the page closes.
  }
}

/** The total the customer sees while shopping, in whole paise so no rounding drift shows up on screen. */
function cartTotalPaise(cart: Cart): number {
  return Object.values(cart).reduce((sum, { product, quantity }) => sum + Math.round(Number(product.price) * 100 * quantity), 0)
}
const rupees = (paise: number) => (paise / 100).toFixed(2)

function PublicShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-3">
          <h1 className="flex items-center gap-2 text-lg font-semibold"><ShoppingBasket aria-hidden="true" className="size-6 text-emerald-600" />{title}</h1>
          <LanguageSwitcher />
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">{children}</main>
    </div>
  )
}

export function PublicStorePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { slug = '' } = useParams()
  const store = useQuery({ queryKey: ['public-store', slug], queryFn: () => getPublicStore(slug), retry: false })
  const [q, setQ] = useState('')
  const [pages, setPages] = useState(1)
  const products = useQuery({ queryKey: ['public-products', slug, q, pages], queryFn: () => getPublicProducts(slug, q, 0).then(async (first) => {
    // "Show more" re-reads the first N pages: simple, and the lists are small.
    const items = [...first.items]
    for (let p = 1; p < pages; p += 1) items.push(...(await getPublicProducts(slug, q, p * first.limit)).items)
    return { ...first, items }
  }), enabled: store.isSuccess })
  const [cart, setCart] = useState<Cart>(() => loadCart(slug))
  useEffect(() => saveCart(slug, cart), [slug, cart])
  const lines = Object.values(cart)
  const total = cartTotalPaise(cart)
  const [checkout, setCheckout] = useState(false)
  const [form, setForm] = useState({ name: '', phone: '', fulfilment: 'DELIVERY' as Fulfilment, payment: 'COD' as PayHow, address: '', notes: '' })
  const s = store.data
  // The chosen options are corrected while rendering (not in an effect) when the shop does not offer them.
  const fulfilment: Fulfilment = s && form.fulfilment === 'DELIVERY' && !s.delivery_enabled ? 'PICKUP' : s && form.fulfilment === 'PICKUP' && !s.pickup_enabled ? 'DELIVERY' : form.fulfilment
  const payment: PayHow = s && form.payment === 'COD' && !s.accepts_cod ? 'UPI' : s && form.payment === 'UPI' && !s.accepts_upi ? 'COD' : form.payment
  // One key per distinct order, so pressing the button twice (or a lost reply) can never make two orders.
  const { keyFor, renew } = useIdempotencyKey()
  const body = useMemo(() => ({
    customer_name: form.name.trim(), customer_phone: form.phone.trim(), fulfilment, payment,
    delivery_address: fulfilment === 'DELIVERY' ? form.address.trim() : undefined, notes: form.notes.trim() || undefined,
    items: lines.map(({ product, quantity }) => ({ product_id: product.id, quantity: String(quantity) })),
  }), [form, fulfilment, payment, lines])
  const place = useMutation({
    mutationFn: () => placePublicOrder(slug, body, keyFor(JSON.stringify(body))),
    onSuccess: (order) => {
      renew()
      setCart({})
      navigate(`/store/${encodeURIComponent(slug)}/order/${encodeURIComponent(order.reference)}?token=${encodeURIComponent(order.tracking_token ?? '')}`)
    },
  })

  if (store.isPending) return <PublicShell title=""><Spinner /></PublicShell>
  if (store.isError || !s) return <PublicShell title=""><Alert tone="error">{t('onlineStore.public.notFound')}</Alert></PublicShell>

  const setQty = (p: PublicProduct, quantity: number) => {
    const next = { ...cart }
    if (quantity <= 0) delete next[p.id]
    else next[p.id] = { product: p, quantity }
    setCart(next)
  }
  const step = (p: PublicProduct) => (p.allows_decimal ? 0.5 : 1)
  const belowMinimum = total < Math.round(Number(s.min_order_amount) * 100)
  const err = place.error instanceof ApiError ? place.error : null
  const canPlace = lines.length > 0 && !belowMinimum && s.is_open && form.name.trim().length >= 2 && form.phone.trim().length >= 6 && (fulfilment === 'PICKUP' || form.address.trim().length >= 5)

  return (
    <PublicShell title={s.name}>
      {s.announcement && <div className="mb-4"><Alert tone="info">{s.announcement}</Alert></div>}
      {!s.is_open && <div className="mb-4"><Alert tone="warning">{t('onlineStore.public.closed')}</Alert></div>}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <section className="space-y-4 lg:col-span-2" aria-label="products">
          <SearchInput value={q} onChange={(v) => { setQ(v); setPages(1) }} placeholder={t('onlineStore.public.search')} />
          {products.isPending ? <Spinner /> : (
            <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {products.data?.items.map((p) => {
                const inCart = cart[p.id]?.quantity ?? 0
                return (
                  <li key={p.id} className="flex flex-col justify-between gap-3 rounded-xl border border-slate-200 bg-white p-4" data-testid={`product-${p.id}`}>
                    <div>
                      <p className="font-medium">{p.name}</p>
                      <p className="text-sm text-slate-600">{formatMoney(p.price)} / {p.unit} · {p.in_stock ? t('onlineStore.public.inStock') : t('onlineStore.public.outOfStock')}</p>
                    </div>
                    {inCart > 0 ? (
                      <div className="flex items-center gap-3">
                        <button type="button" aria-label={t('onlineStore.public.remove')} className={buttonClasses('secondary')} onClick={() => setQty(p, inCart - step(p))}><Minus aria-hidden="true" className="size-5" /></button>
                        <span className="min-w-12 text-center font-semibold" aria-label={t('onlineStore.public.qty')}>{formatQuantity(String(inCart))}</span>
                        <button type="button" aria-label={t('onlineStore.public.add')} className={buttonClasses('secondary')} onClick={() => setQty(p, inCart + step(p))}><Plus aria-hidden="true" className="size-5" /></button>
                      </div>
                    ) : (
                      <button type="button" className={buttonClasses()} disabled={!p.in_stock || !s.is_open} onClick={() => setQty(p, 1)}>{t('onlineStore.public.add')}</button>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
          {products.data && products.data.items.length < products.data.total && (
            <button type="button" className={buttonClasses('secondary')} onClick={() => setPages(pages + 1)}>{t('onlineStore.public.loadMore')}</button>
          )}
        </section>

        <aside className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 lg:sticky lg:top-4 lg:self-start" aria-label={t('onlineStore.public.cart')}>
          <h2 className="text-lg font-semibold">{t('onlineStore.public.cart')}</h2>
          {lines.length === 0 ? <p className="text-slate-600">{t('onlineStore.public.cartEmpty')}</p> : (
            <ul className="space-y-2 text-sm">
              {lines.map(({ product, quantity }) => (
                <li key={product.id} className="flex justify-between gap-3"><span>{product.name} × {formatQuantity(String(quantity))}</span><span>{formatMoney(rupees(Math.round(Number(product.price) * 100 * quantity)))}</span></li>
              ))}
            </ul>
          )}
          <p className="flex justify-between border-t border-slate-100 pt-3 text-base font-semibold"><span>{t('onlineStore.public.total')}</span><span>{formatMoney(rupees(total))}</span></p>
          {belowMinimum && lines.length > 0 && <Alert tone="warning">{t('onlineStore.public.minimum', { amount: formatMoney(s.min_order_amount) })}</Alert>}
          {!checkout ? (
            <button type="button" className={`${buttonClasses()} w-full`} disabled={lines.length === 0 || belowMinimum || !s.is_open} onClick={() => setCheckout(true)}>{t('onlineStore.public.checkout')}</button>
          ) : (
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (canPlace) place.mutate() }}>
              <TextField label={t('onlineStore.public.yourName')} value={form.name} autoComplete="name" onChange={(e) => setForm({ ...form, name: e.target.value })} required />
              <TextField label={t('onlineStore.public.yourPhone')} value={form.phone} inputMode="tel" autoComplete="tel" onChange={(e) => setForm({ ...form, phone: e.target.value })} required error={err?.fieldErrors.customer_phone} />
              <fieldset className="space-y-1"><legend className="text-sm font-medium">{t('onlineStore.public.how')}</legend>
                {s.delivery_enabled && <label className="flex min-h-11 items-center gap-2"><input type="radio" name="how" checked={fulfilment === 'DELIVERY'} onChange={() => setForm({ ...form, fulfilment: 'DELIVERY' })} />{t('onlineStore.public.delivery')}</label>}
                {s.pickup_enabled && <label className="flex min-h-11 items-center gap-2"><input type="radio" name="how" checked={fulfilment === 'PICKUP'} onChange={() => setForm({ ...form, fulfilment: 'PICKUP' })} />{t('onlineStore.public.pickup')}</label>}
              </fieldset>
              {fulfilment === 'DELIVERY' && <TextAreaField label={t('onlineStore.public.address')} value={form.address} autoComplete="street-address" onChange={(e) => setForm({ ...form, address: e.target.value })} required error={err?.fieldErrors.delivery_address} />}
              <fieldset className="space-y-1"><legend className="text-sm font-medium">{t('onlineStore.public.payHow')}</legend>
                {s.accepts_cod && <label className="flex min-h-11 items-center gap-2"><input type="radio" name="pay" checked={payment === 'COD'} onChange={() => setForm({ ...form, payment: 'COD' })} />{t('onlineStore.public.payCod')}</label>}
                {s.accepts_upi && <label className="flex min-h-11 items-center gap-2"><input type="radio" name="pay" checked={payment === 'UPI'} onChange={() => setForm({ ...form, payment: 'UPI' })} />{t('onlineStore.public.payUpi')}</label>}
              </fieldset>
              <p className="text-xs text-slate-600">{t('onlineStore.public.payAtDoor')}</p>
              <TextAreaField label={t('onlineStore.public.notes')} optional value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
              {err && <Alert tone="error">{err.message}</Alert>}
              <button type="submit" className={`${buttonClasses()} w-full`} disabled={!canPlace || place.isPending}>{place.isPending ? t('onlineStore.public.placing') : t('onlineStore.public.place')}</button>
            </form>
          )}
        </aside>
      </div>
      <p className="mt-6 text-center text-sm text-slate-500">
        {s.contact_phone && <a className="underline" href={`tel:${s.contact_phone}`}>{t('onlineStore.public.callShop')}: {s.contact_phone}</a>}
      </p>
      <Link to={`/store/${encodeURIComponent(slug)}`} className="sr-only">{t('onlineStore.public.backToStore')}</Link>
    </PublicShell>
  )
}
