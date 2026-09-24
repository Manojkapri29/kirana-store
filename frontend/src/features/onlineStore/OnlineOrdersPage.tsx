import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { acceptOrder, advanceOrder, cancelOrder, getOrder, getOrders, getOrdersSummary, rejectOrder, type OrderDetail, type OrderStatus } from '@/api/onlineStore'
import { TextField } from '@/components/fields'
import { Pagination } from '@/components/Pagination'
import { Alert, Badge, type BadgeTone, Button, EmptyState, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useErrorText } from '@/features/finance/financeUtils'
import { formatDateTime, formatMoney, formatQuantity } from '@/lib/format'

const TONE: Record<OrderStatus, BadgeTone> = { PLACED: 'amber', ACCEPTED: 'green', PREPARING: 'green', READY: 'green', OUT_FOR_DELIVERY: 'green', DELIVERED: 'slate', REJECTED: 'red', CANCELLED: 'red' }
const FILTERS: (OrderStatus | '')[] = ['', 'PLACED', 'ACCEPTED', 'PREPARING', 'READY', 'OUT_FOR_DELIVERY', 'DELIVERED', 'REJECTED', 'CANCELLED']
const FORWARD = ['PREPARING', 'READY', 'OUT_FOR_DELIVERY', 'DELIVERED'] as const

function Detail({ id, onChanged }: { id: number; onChanged: () => void }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const client = useQueryClient()
  const order = useQuery({ queryKey: ['online-order', id], queryFn: () => getOrder(id) })
  const [ask, setAsk] = useState<'reject' | 'cancel' | null>(null)
  const [reason, setReason] = useState('')
  const done = (fresh: OrderDetail) => { client.setQueryData(['online-order', id], fresh); setAsk(null); setReason(''); onChanged() }
  const accept = useMutation({ mutationFn: () => acceptOrder(id), onSuccess: done })
  const move = useMutation({ mutationFn: (s: OrderStatus) => advanceOrder(id, s), onSuccess: done })
  const negative = useMutation({ mutationFn: () => (ask === 'reject' ? rejectOrder(id, reason) : cancelOrder(id, reason)), onSuccess: done })
  if (order.isPending) return <Spinner />
  if (order.isError) return <QueryError error={order.error} onRetry={() => void order.refetch()} />
  const o = order.data
  const next = o.allowed_next
  const error = accept.error ?? move.error ?? negative.error
  return (
    <div className="space-y-4 border-t border-slate-100 p-4" data-testid={`order-detail-${o.id}`}>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        <div><dt className="text-slate-500">{t('onlineStore.orders.customer')}</dt><dd className="font-medium">{o.customer_name}</dd></div>
        <div><dt className="text-slate-500">{t('onlineStore.orders.phone')}</dt><dd><a className="text-emerald-700 underline" href={`tel:${o.customer_phone}`}>{o.customer_phone}</a></dd></div>
        <div><dt className="text-slate-500">{t('onlineStore.orders.fulfilment')}</dt><dd>{t(`onlineStore.orders.how.${o.fulfilment}`)} · {t(`onlineStore.orders.pay.${o.payment}`)}</dd></div>
        {o.delivery_address && <div><dt className="text-slate-500">{t('onlineStore.orders.address')}</dt><dd>{o.delivery_address}</dd></div>}
        {o.notes && <div className="sm:col-span-2"><dt className="text-slate-500">{t('onlineStore.orders.notes')}</dt><dd>{o.notes}</dd></div>}
      </dl>
      <table className="w-full text-sm">
        <thead><tr className="text-left text-slate-500"><th className="py-1 font-medium">{t('onlineStore.orders.items')}</th><th className="py-1 text-right font-medium">×</th><th className="py-1 text-right font-medium">{t('onlineStore.orders.total')}</th></tr></thead>
        <tbody>
          {o.items.map((i) => (<tr key={i.product_id} className="border-t border-slate-100"><td className="py-1">{i.product_name}</td><td className="py-1 text-right">{formatQuantity(i.quantity)} {i.unit}</td><td className="py-1 text-right">{formatMoney(i.line_total)}</td></tr>))}
          <tr className="border-t border-slate-200 font-semibold"><td className="py-1" colSpan={2}>{t('onlineStore.orders.total')}</td><td className="py-1 text-right">{formatMoney(o.total_amount)}</td></tr>
        </tbody>
      </table>
      {o.sale_id && (
        <p className="text-sm">{t('onlineStore.orders.sale')}: <Link className="font-medium text-emerald-700 underline" to={`/sales/${o.sale_id}`}>{o.invoice_no ?? t('onlineStore.orders.viewSale')}</Link> · {t('onlineStore.orders.saleTotal')} {formatMoney(o.sale_total)}</p>
      )}
      {o.warnings.map((w) => <Alert key={w} tone="warning">{w}</Alert>)}
      {error && <Alert tone="error">{errorText(error)}</Alert>}
      {ask ? (
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); negative.mutate() }}>
          <TextField label={t('onlineStore.orders.reasonLabel')} value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} autoFocus />
          <div className="flex gap-3">
            <Button type="submit" variant="danger" loading={negative.isPending} disabled={reason.trim().length < 3}>{t('onlineStore.orders.confirm')}</Button>
            <Button variant="secondary" onClick={() => setAsk(null)}>{t('onlineStore.orders.back')}</Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-3">
          {next.includes('ACCEPTED') && <Button requires="ONLINE_ORDER_ACCEPT" loading={accept.isPending} onClick={() => accept.mutate()}>{t('onlineStore.orders.accept')}</Button>}
          {next.includes('REJECTED') && <Button variant="danger" requires="ONLINE_ORDER_REJECT" onClick={() => setAsk('reject')}>{t('onlineStore.orders.reject')}</Button>}
          {FORWARD.filter((s) => next.includes(s)).map((s) => (
            <Button key={s} requires="ONLINE_ORDER_STATUS_UPDATE" loading={move.isPending && move.variables === s} onClick={() => move.mutate(s)}>{t(`onlineStore.orders.next.${s}`)}</Button>
          ))}
          {next.includes('CANCELLED') && <Button variant="secondary" requires="ONLINE_ORDER_STATUS_UPDATE" onClick={() => setAsk('cancel')}>{t('onlineStore.orders.cancel')}</Button>}
        </div>
      )}
      {next.includes('DELIVERED') && <p className="text-xs text-slate-500">{t('onlineStore.orders.deliveredHint')}</p>}
      <details>
        <summary className="cursor-pointer text-sm font-medium">{t('onlineStore.orders.history')}</summary>
        <ul className="mt-2 space-y-1 text-sm text-slate-700">
          {o.events.map((e, i) => (
            <li key={i}>{formatDateTime(e.at)} · {t(`onlineStore.orders.status.${e.to_status as OrderStatus}`)} · {t(`onlineStore.orders.actor.${e.actor as 'CUSTOMER' | 'STAFF'}`)}{e.note ? ` · ${e.note}` : ''}</li>
          ))}
        </ul>
      </details>
    </div>
  )
}

export function OnlineOrdersPage() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [status, setStatus] = useState<OrderStatus | ''>('')
  const [offset, setOffset] = useState(0)
  const [openId, setOpenId] = useState<number | null>(null)
  const list = useQuery({ queryKey: ['online-orders', status, offset], queryFn: () => getOrders(status, offset), refetchInterval: 30_000 })
  const summary = useQuery({ queryKey: ['online-orders-summary'], queryFn: getOrdersSummary, refetchInterval: 30_000 })
  const changed = () => { void client.invalidateQueries({ queryKey: ['online-orders'] }); void client.invalidateQueries({ queryKey: ['online-orders-summary'] }) }
  return (
    <div className="space-y-4">
      <PageHeader title={t('onlineStore.orders.title')} subtitle={t('onlineStore.orders.subtitle')} actions={summary.data ? <Badge tone={summary.data.open ? 'amber' : 'slate'}>{t('onlineStore.orders.open')}: {summary.data.open}</Badge> : undefined} />
      <div className="flex flex-wrap gap-2" role="group" aria-label={t('onlineStore.orders.status.PLACED')}>
        {FILTERS.map((f) => (
          <button key={f || 'all'} type="button" aria-pressed={status === f} onClick={() => { setStatus(f); setOffset(0); setOpenId(null) }}
            className={`min-h-11 rounded-full border px-4 text-sm font-medium ${status === f ? 'border-emerald-600 bg-emerald-50 text-emerald-900' : 'border-slate-300 bg-white text-slate-700'}`}>
            {f ? t(`onlineStore.orders.status.${f}`) : t('onlineStore.orders.all')}
          </button>
        ))}
      </div>
      {list.isPending ? <Spinner /> : list.isError ? <QueryError error={list.error} onRetry={() => void list.refetch()} /> : list.data.items.length === 0 ? (
        <EmptyState title={t('onlineStore.orders.empty')} />
      ) : (
        <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
          {list.data.items.map((o) => (
            <li key={o.id} data-testid={`order-${o.id}`}>
              <button type="button" className="flex w-full items-center justify-between gap-3 p-4 text-left" aria-expanded={openId === o.id} onClick={() => setOpenId(openId === o.id ? null : o.id)}>
                <div className="min-w-0">
                  <p className="font-medium">{o.order_no} · {o.customer_name}</p>
                  <p className="text-sm text-slate-600">{formatDateTime(o.placed_at)} · {o.item_count} · {t(`onlineStore.orders.how.${o.fulfilment}`)}</p>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1"><span className="font-semibold">{formatMoney(o.total_amount)}</span><Badge tone={TONE[o.status]}>{t(`onlineStore.orders.status.${o.status}`)}</Badge></div>
              </button>
              {openId === o.id && <Detail id={o.id} onChanged={changed} />}
            </li>
          ))}
        </ul>
      )}
      {list.data && <Pagination total={list.data.total} limit={list.data.limit} offset={list.data.offset} onChange={setOffset} />}
    </div>
  )
}
