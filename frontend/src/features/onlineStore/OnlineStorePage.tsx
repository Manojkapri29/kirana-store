import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import { getListings, getStoreSettings, saveStoreSettings, setListing, type StoreSettings } from '@/api/onlineStore'
import { TextField } from '@/components/fields'
import { Pagination } from '@/components/Pagination'
import { SearchInput } from '@/components/SearchInput'
import { Alert, Badge, Button, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useErrorText } from '@/features/finance/financeUtils'
import { formatMoney } from '@/lib/format'

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex min-h-11 items-center gap-3 text-base">
      <input type="checkbox" className="size-5" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  )
}

function SettingsForm({ initial, onSaved }: { initial: StoreSettings | null; onSaved: () => void }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const [form, setForm] = useState({
    slug: initial?.slug ?? '', display_name: initial?.display_name ?? '', is_open: initial?.is_open ?? false,
    accepts_cod: initial?.accepts_cod ?? true, accepts_upi: initial?.accepts_upi ?? true,
    delivery_enabled: initial?.delivery_enabled ?? true, pickup_enabled: initial?.pickup_enabled ?? true,
    min_order_amount: initial?.min_order_amount ?? '0', contact_phone: initial?.contact_phone ?? '', announcement: initial?.announcement ?? '',
  })
  const [done, setDone] = useState(false)
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => { setDone(false); setForm({ ...form, [key]: value }) }
  const save = useMutation({
    mutationFn: () => saveStoreSettings({ ...form, min_order_amount: form.min_order_amount || '0', contact_phone: form.contact_phone || null, announcement: form.announcement || null }),
    onSuccess: () => { setDone(true); onSaved() },
  })
  const noPay = !form.accepts_cod && !form.accepts_upi
  const noWay = !form.delivery_enabled && !form.pickup_enabled
  const fields = save.error instanceof ApiError ? save.error.fieldErrors : {}
  return (
    <form className="space-y-4 rounded-xl border border-slate-200 bg-white p-4" onSubmit={(e) => { e.preventDefault(); if (!noPay && !noWay) save.mutate() }} aria-label={t('onlineStore.setup.title')}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField label={t('onlineStore.setup.address')} hint={t('onlineStore.setup.addressHint')} value={form.slug} error={fields.slug} onChange={(e) => set('slug', e.target.value.toLowerCase())} required />
        <TextField label={t('onlineStore.setup.name')} value={form.display_name} error={fields.display_name} onChange={(e) => set('display_name', e.target.value)} required />
        <TextField label={t('onlineStore.setup.minOrder')} inputMode="decimal" value={form.min_order_amount} error={fields.min_order_amount} onChange={(e) => set('min_order_amount', e.target.value)} />
        <TextField label={t('onlineStore.setup.phone')} inputMode="tel" optional value={form.contact_phone} error={fields.contact_phone} onChange={(e) => set('contact_phone', e.target.value)} />
      </div>
      <TextField label={t('onlineStore.setup.announcement')} optional value={form.announcement} onChange={(e) => set('announcement', e.target.value)} />
      <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
        <Check label={t('onlineStore.setup.open')} checked={form.is_open} onChange={(v) => set('is_open', v)} />
        <Check label={t('onlineStore.setup.delivery')} checked={form.delivery_enabled} onChange={(v) => set('delivery_enabled', v)} />
        <Check label={t('onlineStore.setup.cod')} checked={form.accepts_cod} onChange={(v) => set('accepts_cod', v)} />
        <Check label={t('onlineStore.setup.pickup')} checked={form.pickup_enabled} onChange={(v) => set('pickup_enabled', v)} />
        <Check label={t('onlineStore.setup.upi')} checked={form.accepts_upi} onChange={(v) => set('accepts_upi', v)} />
      </div>
      <p className="text-sm text-slate-600">{t('onlineStore.setup.payNote')}</p>
      {noPay && <Alert tone="warning">{t('onlineStore.setup.noPayment')}</Alert>}
      {noWay && <Alert tone="warning">{t('onlineStore.setup.noWay')}</Alert>}
      {save.isError && <Alert tone="error">{errorText(save.error)}</Alert>}
      {done && <Alert tone="success">{t('onlineStore.setup.saved')}</Alert>}
      <Button type="submit" loading={save.isPending} disabled={noPay || noWay}>{t('onlineStore.setup.save')}</Button>
    </form>
  )
}

function Listings() {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const client = useQueryClient()
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const list = useQuery({ queryKey: ['store-listings', q, offset], queryFn: () => getListings(q, offset) })
  const toggle = useMutation({
    mutationFn: ({ id, visible }: { id: number; visible: boolean }) => setListing(id, visible),
    onSuccess: () => client.invalidateQueries({ queryKey: ['store-listings'] }),
  })
  return (
    <section className="space-y-3" aria-labelledby="store-products">
      <div>
        <h2 id="store-products" className="text-lg font-semibold">{t('onlineStore.setup.productsTitle')}</h2>
        <p className="text-sm text-slate-600">{t('onlineStore.setup.productsHint')}</p>
      </div>
      <SearchInput value={q} onChange={(v) => { setQ(v); setOffset(0) }} placeholder={t('onlineStore.setup.search')} />
      {toggle.isError && <Alert tone="error">{errorText(toggle.error)}</Alert>}
      {list.isPending ? <Spinner /> : list.isError ? <QueryError error={list.error} onRetry={() => void list.refetch()} /> : list.data.items.length === 0 ? (
        <p className="text-slate-600">{t('onlineStore.setup.noProducts')}</p>
      ) : (
        <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
          {list.data.items.map((p) => (
            <li key={p.product_id} className="flex items-center justify-between gap-3 p-3" data-testid={`listing-${p.product_id}`}>
              <div className="min-w-0">
                <p className="truncate font-medium">{p.name}</p>
                <p className="text-sm text-slate-600">{p.category} · {formatMoney(p.price)} / {p.unit}</p>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                {!p.is_active ? <Badge tone="slate">{t('onlineStore.setup.inactive')}</Badge> : <Badge tone={p.is_visible ? 'green' : 'slate'}>{p.is_visible ? t('onlineStore.setup.shown') : t('onlineStore.setup.hidden')}</Badge>}
                {p.is_active && (
                  <Button variant="secondary" loading={toggle.isPending && toggle.variables?.id === p.product_id} onClick={() => toggle.mutate({ id: p.product_id, visible: !p.is_visible })}>
                    {p.is_visible ? t('onlineStore.setup.hide') : t('onlineStore.setup.show')}
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {list.data && <Pagination total={list.data.total} limit={list.data.limit} offset={list.data.offset} onChange={setOffset} />}
    </section>
  )
}

export function OnlineStorePage() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const settings = useQuery({ queryKey: ['store-settings'], queryFn: getStoreSettings })
  const store = settings.data?.store ?? null
  const link = store ? `${window.location.origin}${store.public_path}` : null
  return (
    <div className="space-y-6">
      <PageHeader title={t('onlineStore.setup.title')} subtitle={t('onlineStore.setup.subtitle')} />
      {settings.isPending ? <Spinner /> : settings.isError ? <QueryError error={settings.error} onRetry={() => void settings.refetch()} /> : (
        <>
          {link && (
            <p className="text-sm">
              {t('onlineStore.setup.shareLink')}: <a className="break-all font-medium text-emerald-700 underline" href={store!.public_path} target="_blank" rel="noreferrer">{link}</a>
              {' '}<Badge tone={store!.is_open ? 'green' : 'amber'}>{store!.is_open ? t('onlineStore.setup.open') : t('onlineStore.public.closed')}</Badge>
            </p>
          )}
          <SettingsForm key={store?.slug ?? 'new'} initial={store} onSaved={() => void client.invalidateQueries({ queryKey: ['store-settings'] })} />
          {store && <Listings />}
        </>
      )}
    </div>
  )
}
