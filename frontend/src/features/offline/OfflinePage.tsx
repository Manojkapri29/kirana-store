import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { CameraScanButton } from '@/components/CameraScanner'
import { SelectField, TextField } from '@/components/fields'
import { Alert, Badge, type BadgeTone, Button, PageHeader } from '@/components/ui'
import { useOnline } from '@/hooks/useOnline'
import { deleteScope, type Scope } from '@/offline/db'
import { enqueue, type QueuedOp } from '@/offline/queue'
import { ageHours, findByCode, readSnapshot, refreshSnapshots, STALE_AFTER_HOURS, type SnapshotCustomer, type SnapshotProduct } from '@/offline/snapshots'
import { discardOp, retryConflict, syncQueue } from '@/offline/sync'
import { copyEnabled, setCopyEnabled, useQueue, useScope } from '@/offline/useOffline'
import { formatDateTime, formatMoney } from '@/lib/format'
import { grossPaise, paiseToText, toPaise, toThousandths } from '@/lib/money'
import { InstallButton } from '@/pwa/PwaBanners'

const TONE: Record<string, BadgeTone> = { PENDING: 'amber', SYNCING: 'amber', SYNCED: 'green', FAILED: 'red', CONFLICT: 'red', DISCARDED: 'slate' }
const METHODS = ['CASH', 'UPI', 'OTHER'] as const

/** A person's own words for a problem, never a code on its own. */
function useReason() {
  const { t } = useTranslation()
  return (op: QueuedOp) => t(`offline.reasons.${op.errorCode ?? ''}`, { defaultValue: op.message ?? '' })
}

function Snapshots({ scope }: { scope: Scope }) {
  const { t } = useTranslation()
  const online = useOnline()
  const [on, setOn] = useState(copyEnabled(scope))
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const products = useQuery({ queryKey: ['offline', 'snapshot', 'products', scope, on], queryFn: () => readSnapshot<SnapshotProduct>(scope, 'products') })
  const customers = useQuery({ queryKey: ['offline', 'snapshot', 'customers', scope, on], queryFn: () => readSnapshot<SnapshotCustomer>(scope, 'customers') })
  const asOf = products.data?.asOf
  async function refresh() {
    setBusy(true)
    setFailed(false)
    try {
      await refreshSnapshots(scope)
      await Promise.all([products.refetch(), customers.refetch()])
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" aria-labelledby="copy-h">
      <h2 id="copy-h" className="font-semibold text-slate-900">{t('offline.copyTitle')}</h2>
      <p className="text-sm text-slate-600">{t('offline.copyExplain')}</p>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={on} onChange={(e) => { setCopyEnabled(scope, e.target.checked); setOn(e.target.checked); if (e.target.checked && online) void refresh() }} />
        {t('offline.copyKeep')}
      </label>
      {on && (
        <>
          <p className="text-sm" data-testid="last-synced">
            {asOf ? t('offline.lastSynced', { when: formatDateTime(asOf) }) : t('offline.neverSynced')}
            {products.data && <> · {t('offline.counts', { products: products.data.items.length, customers: customers.data?.items.length ?? 0 })}</>}
          </p>
          {asOf && ageHours(asOf) > STALE_AFTER_HOURS && <Alert tone="warning">{t('offline.veryStale')}</Alert>}
          {products.data?.truncated && <Alert tone="info">{t('offline.truncated')}</Alert>}
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" loading={busy} disabled={!online} onClick={() => void refresh()}>{t('offline.refresh')}</Button>
          </div>
          {failed && <Alert tone="error">{t('offline.refreshFailed')}</Alert>}
        </>
      )}
      <div>
        <Button variant="secondary" onClick={() => { if (window.confirm(t('offline.removeConfirm'))) void deleteScope(scope).then(() => { setCopyEnabled(scope, false); setOn(false); window.location.reload() }) }}>
          {t('offline.remove')}
        </Button>
      </div>
    </section>
  )
}

function Till({ scope }: { scope: Scope }) {
  const { t } = useTranslation()
  const [kind, setKind] = useState<'QUICK_SALE' | 'SALE' | 'CUSTOMER_PAYMENT'>('QUICK_SALE')
  const products = useQuery({ queryKey: ['offline', 'snapshot', 'products', scope, 'till'], queryFn: () => readSnapshot<SnapshotProduct>(scope, 'products') })
  const customers = useQuery({ queryKey: ['offline', 'snapshot', 'customers', scope, 'till'], queryFn: () => readSnapshot<SnapshotCustomer>(scope, 'customers') })
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // shared fields
  const [amount, setAmount] = useState('')
  const [method, setMethod] = useState<(typeof METHODS)[number]>('CASH')
  const [customerId, setCustomerId] = useState('')
  const [paid, setPaid] = useState('')
  const [cart, setCart] = useState<{ product: SnapshotProduct; quantity: string }[]>([])
  const [code, setCode] = useState('')

  const list = customers.data?.items ?? []
  const total = kind === 'SALE' ? cart.reduce<bigint | null>((sum, l) => {
    const q = toThousandths(l.quantity), p = toPaise(l.product.selling_price ?? '')
    return sum === null || q === null || p === null || q === 0n ? null : sum + grossPaise(q, p)
  }, 0n) : kind === 'QUICK_SALE' ? toPaise(amount) : null

  function addByCode(text: string) {
    const found = findByCode(products.data?.items ?? [], text)
    if (!found) return setError(t('offline.codeNotFound'))
    setError(null)
    setCart((c) => (c.some((l) => l.product.id === found.id) ? c.map((l) => (l.product.id === found.id ? { ...l, quantity: String(Number(l.quantity) + 1) } : l)) : [...c, { product: found, quantity: '1' }]))
    setCode('')
  }

  async function save() {
    setError(null)
    setMessage(null)
    const cust = customerId ? Number(customerId) : undefined
    const credit = paid.trim() !== ''
    if (credit && paid.trim() !== '' && toPaise(paid) === null) return setError(t('offline.badAmount'))
    if (credit && !cust) return setError(t('offline.creditNeedsCustomer'))
    const payment = { payment_method: method, ...(credit ? { amount_paid: paid.trim() } : {}) }
    if (kind === 'QUICK_SALE') {
      const p = toPaise(amount)
      if (p === null || p === 0n) return setError(t('offline.badAmount'))
      await enqueue(scope, { type: 'QUICK_SALE', label: t('offline.labelQuick'), total: paiseToText(p), payload: { create: { gross_amount: paiseToText(p), sale_date: today(), ...(cust ? { customer_id: cust } : {}) }, payment, expected_total: paiseToText(p) } })
    } else if (kind === 'SALE') {
      if (cart.length === 0 || total === null) return setError(t('offline.badCart'))
      const items = cart.map((l) => ({ product_id: l.product.id, quantity: l.quantity })) // no price: the server uses the product's price NOW, and a difference is a conflict, not a surprise
      await enqueue(scope, { type: 'SALE', label: t('offline.labelSale', { count: cart.length }), total: paiseToText(total), payload: { create: { sale_date: today(), items, ...(cust ? { customer_id: cust } : {}) }, payment, expected_total: paiseToText(total) } })
    } else {
      const p = toPaise(amount)
      if (!cust || p === null || p === 0n) return setError(t('offline.paymentNeeds'))
      await enqueue(scope, { type: 'CUSTOMER_PAYMENT', label: t('offline.labelPayment'), total: paiseToText(p), payload: { customer_id: cust, amount: paiseToText(p), payment_method: method, entry_date: today() } })
    }
    setMessage(t('offline.queued'))
    setAmount(''); setPaid(''); setCart([]); setCustomerId('')
    if (navigator.onLine) void syncQueue(scope)
  }

  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" aria-labelledby="till-h">
      <h2 id="till-h" className="font-semibold text-slate-900">{t('offline.tillTitle')}</h2>
      <p className="text-sm text-slate-600">{t('offline.tillExplain')}</p>
      <SelectField label={t('offline.kind')} value={kind} onChange={(e) => { setKind(e.target.value as typeof kind); setError(null); setMessage(null) }}>
        <option value="QUICK_SALE">{t('offline.kinds.QUICK_SALE')}</option>
        <option value="SALE">{t('offline.kinds.SALE')}</option>
        <option value="CUSTOMER_PAYMENT">{t('offline.kinds.CUSTOMER_PAYMENT')}</option>
      </SelectField>
      {kind === 'SALE' && (
        <div className="space-y-2">
          <div className="flex items-end gap-2">
            <TextField label={t('offline.scanOrType')} value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addByCode(code) } }} className="flex-1" />
            <CameraScanButton onCode={addByCode} />
          </div>
          {(products.data?.items ?? []).length === 0 && <p className="text-sm text-amber-800">{t('offline.noCopy')}</p>}
          <ul className="max-h-40 space-y-1 overflow-y-auto">
            {(products.data?.items ?? []).filter((p) => code.trim() !== '' && `${p.name} ${p.sku}`.toLowerCase().includes(code.trim().toLowerCase())).slice(0, 6).map((p) => (
              <li key={p.id}><Button variant="secondary" onClick={() => addByCode(p.barcode ?? p.sku)}>{p.name} · {formatMoney(p.selling_price)}</Button></li>
            ))}
          </ul>
          <ul className="space-y-2" aria-label={t('offline.cart')}>
            {cart.map((l) => (
              <li key={l.product.id} className="flex items-end gap-2">
                <span className="flex-1 text-sm">{l.product.name} <span className="text-slate-500">({formatMoney(l.product.selling_price)}; {t('offline.stockWas', { qty: l.product.stock, unit: l.product.unit })})</span></span>
                <TextField label={t('offline.qty')} value={l.quantity} inputMode="decimal" onChange={(e) => setCart(cart.map((x) => (x.product.id === l.product.id ? { ...x, quantity: e.target.value } : x)))} className="w-24" />
                <Button variant="secondary" onClick={() => setCart(cart.filter((x) => x.product.id !== l.product.id))}>{t('offline.remove1')}</Button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {kind !== 'SALE' && <TextField label={t('offline.amount')} value={amount} inputMode="decimal" onChange={(e) => setAmount(e.target.value)} />}
      {total !== null && total > 0n && <p className="text-sm font-medium">{t('offline.total')}: {formatMoney(paiseToText(total))}</p>}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <SelectField label={t('offline.method')} value={method} onChange={(e) => setMethod(e.target.value as typeof method)}>
          {METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
        </SelectField>
        <SelectField label={t('offline.customer')} optional={kind !== 'CUSTOMER_PAYMENT'} value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
          <option value="">—</option>
          {list.map((c) => <option key={c.id} value={c.id}>{c.name}{c.phone ? ` · ${c.phone}` : ''}</option>)}
        </SelectField>
        {kind !== 'CUSTOMER_PAYMENT' && <TextField label={t('offline.paidNow')} optional hint={t('offline.paidHint')} value={paid} inputMode="decimal" onChange={(e) => setPaid(e.target.value)} />}
      </div>
      {kind === 'SALE' && <Alert tone="info">{t('offline.saleHonest')}</Alert>}
      {error && <Alert tone="error">{error}</Alert>}
      {message && <Alert tone="success">{message}</Alert>}
      <Button onClick={() => void save()}>{t('offline.save')}</Button>
    </section>
  )
}

const today = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function Queue({ scope }: { scope: Scope }) {
  const { t } = useTranslation()
  const { ops, pending, attention } = useQueue(scope)
  const reason = useReason()
  const online = useOnline()
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  async function syncNow() {
    setBusy(true)
    setNote(null)
    const r = await syncQueue(scope)
    setNote(r.stoppedBecause === 'offline' ? t('offline.stillOffline') : r.stoppedBecause === 'signed_out' ? t('offline.signInAgain') : t('offline.syncDone', { synced: r.synced, attention: r.needsAttention }))
    setBusy(false)
  }
  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" aria-labelledby="queue-h">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="queue-h" className="font-semibold text-slate-900">{t('offline.queueTitle')}</h2>
        <Button loading={busy} disabled={!online || pending === 0} onClick={() => void syncNow()}>{t('offline.syncNow', { count: pending })}</Button>
      </div>
      {attention > 0 && <Alert tone="warning">{t('offline.needAttention', { count: attention })}</Alert>}
      {note && <Alert tone="info">{note}</Alert>}
      {ops.length === 0 && <p className="text-sm text-slate-500">{t('offline.queueEmpty')}</p>}
      <ul className="space-y-2">
        {ops.slice().reverse().map((op) => (
          <li key={op.clientOpId} className="space-y-1 rounded-lg border border-slate-200 p-3 text-sm" data-testid={`op-${op.status}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium">{op.label}{op.total ? ` · ${formatMoney(op.total)}` : ''}</span>
              <Badge tone={TONE[op.status] ?? 'slate'}>{t(`offline.status.${op.status}`)}</Badge>
            </div>
            <p className="text-xs text-slate-500">{formatDateTime(op.createdAt)}</p>
            {(op.status === 'CONFLICT' || op.status === 'FAILED') && <p className="text-red-800">{reason(op)}</p>}
            {op.status === 'CONFLICT' && op.result && 'server_total' in op.result && <p className="text-xs">{t('offline.totalChanged', { expected: formatMoney(String(op.result.expected_total)), server: formatMoney(String(op.result.server_total)) })}</p>}
            <div className="flex flex-wrap gap-2">
              {op.status === 'CONFLICT' && <Button variant="secondary" disabled={!online} onClick={() => void retryConflict(scope, op).catch(() => setNote(t('offline.retryFailed')))}>{t('offline.retry')}</Button>}
              {(op.status === 'CONFLICT' || op.status === 'FAILED' || op.status === 'PENDING') && (
                <Button variant="secondary" onClick={() => { if (window.confirm(t('offline.discardConfirm'))) void discardOp(scope, op).catch(() => setNote(t('offline.retryFailed'))) }}>{t('offline.discard')}</Button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}

export function OfflinePage() {
  const { t } = useTranslation()
  const scope = useScope()
  const online = useOnline()
  if (!scope) return <Alert tone="warning">{t('offline.noShop')}</Alert>
  return (
    <div className="space-y-6">
      <PageHeader title={t('offline.pageTitle')} subtitle={t('offline.pageSubtitle')} actions={<InstallButton />} />
      <Alert tone={online ? 'success' : 'warning'}>{online ? t('offline.youAreOnline') : t('offline.youAreOffline')}</Alert>
      <Alert tone="info">{t('offline.storageWarning')}</Alert>
      <Till scope={scope} />
      <Queue scope={scope} />
      <Snapshots scope={scope} />
    </div>
  )
}
