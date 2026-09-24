import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { cancelPublicOrder, trackPublicOrder, type OrderStatus } from '@/api/onlineStore'
import { LanguageSwitcher } from '@/components/LanguageSwitcher'
import { buttonClasses } from '@/components/buttonStyles'
import { Alert, Badge, type BadgeTone, Spinner } from '@/components/ui'
import { formatDateTime, formatMoney, formatQuantity } from '@/lib/format'

const TONE: Record<OrderStatus, BadgeTone> = { PLACED: 'amber', ACCEPTED: 'green', PREPARING: 'green', READY: 'green', OUT_FOR_DELIVERY: 'green', DELIVERED: 'slate', REJECTED: 'red', CANCELLED: 'red' }

export function PublicOrderPage() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const { slug = '', reference = '' } = useParams()
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const order = useQuery({
    queryKey: ['public-order', slug, reference, token], queryFn: () => trackPublicOrder(slug, reference, token), retry: false,
    refetchInterval: (query) => (query.state.data && ['DELIVERED', 'REJECTED', 'CANCELLED'].includes(query.state.data.status) ? false : 20_000),
  })
  const cancel = useMutation({ mutationFn: () => cancelPublicOrder(slug, reference, token), onSuccess: (fresh) => client.setQueryData(['public-order', slug, reference, token], fresh) })
  const o = order.data
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-2xl items-center justify-between gap-3 px-4 py-3">
          <h1 className="text-lg font-semibold">{o?.store_name ?? t('onlineStore.public.trackTitle')}</h1>
          <LanguageSwitcher />
        </div>
      </header>
      <main className="mx-auto max-w-2xl space-y-4 px-4 py-6">
        {order.isPending && <Spinner />}
        {order.isError && <Alert tone="error">{t('onlineStore.orders.empty')}</Alert>}
        {o && (
          <>
            {o.status === 'PLACED' && <Alert tone="info">{t('onlineStore.public.placedHint')}</Alert>}
            <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-4" data-testid="public-order">
              <div className="flex items-center justify-between gap-3">
                <div><p className="text-sm text-slate-600">{t('onlineStore.public.trackTitle')}</p><p className="text-lg font-semibold">{o.order_no}</p></div>
                <Badge tone={TONE[o.status]}>{t(`onlineStore.orders.status.${o.status}`)}</Badge>
              </div>
              <ul className="space-y-1 text-sm">
                {o.items.map((i) => (<li key={i.name} className="flex justify-between gap-3"><span>{i.name} × {formatQuantity(i.quantity)} {i.unit}</span><span>{formatMoney(i.line_total)}</span></li>))}
              </ul>
              <p className="flex justify-between border-t border-slate-100 pt-3 font-semibold"><span>{t('onlineStore.public.total')}</span><span>{formatMoney(o.total_amount)}</span></p>
              <p className="text-sm text-slate-600">{t(`onlineStore.orders.how.${o.fulfilment}`)} · {t(`onlineStore.orders.pay.${o.payment}`)}. {t('onlineStore.public.payAtDoor')}</p>
              <ol className="space-y-1 text-sm text-slate-700">
                {o.timeline.map((e, i) => (<li key={i}>{formatDateTime(e.at)} · {t(`onlineStore.orders.status.${e.status as OrderStatus}`)}</li>))}
              </ol>
              {cancel.isError && <Alert tone="error">{cancel.error instanceof ApiError ? cancel.error.message : t('common.genericError')}</Alert>}
              {o.can_cancel && <button type="button" className={buttonClasses('danger')} disabled={cancel.isPending} onClick={() => cancel.mutate()}>{t('onlineStore.public.cancelOrder')}</button>}
              {o.status === 'CANCELLED' && <Alert tone="info">{t('onlineStore.public.cancelled')}</Alert>}
              {o.store_phone && <a className="block text-sm underline" href={`tel:${o.store_phone}`}>{t('onlineStore.public.callShop')}: {o.store_phone}</a>}
            </div>
            <Link className="text-sm underline" to={`/store/${encodeURIComponent(slug)}`}>{t('onlineStore.public.backToStore')}</Link>
          </>
        )}
      </main>
    </div>
  )
}
