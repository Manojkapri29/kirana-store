import { useQuery } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { getOverview, type Overview, type Unavailable } from '@/api/account'
import { Alert, PageHeader, QueryError, Spinner } from '@/components/ui'
import { FilterSelect } from '@/components/fields'
import { isoDaysAgo } from '@/lib/dates'
import { formatMoney, NOT_SET } from '@/lib/format'

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
      {children}
    </section>
  )
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 text-sm">
      <span className="text-slate-600">{label}</span>
      <span className="font-medium text-slate-900">{value}</span>
    </div>
  )
}

/** A section the server says it cannot know. The reason is shown; no number is invented. */
function NotAvailable({ what }: { what: Unavailable }) {
  const { t } = useTranslation()
  return <p className="text-sm text-slate-600">{what.message ?? what.reason ?? t('insights.notAvailable')}</p>
}

function money(value: string | null | undefined) {
  return value === null || value === undefined ? NOT_SET : formatMoney(value)
}

function Sections({ o }: { o: Overview }) {
  const { t } = useTranslation()
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Card title={t('insights.sales')}>
        <Row label={t('insights.net')} value={money(o.sales.net)} />
        <Row label={t('insights.transactions')} value={o.sales.transactions} />
        <Row label={t('insights.averageBill')} value={money(o.sales.average_bill)} />
        <Row label={t('insights.discounts')} value={money(o.sales.discounts)} />
        <Row label={t('insights.detailed')} value={`${o.sales.detailed.count} · ${money(o.sales.detailed.net)}`} />
        <Row label={t('insights.quick')} value={`${o.sales.quick.count} · ${money(o.sales.quick.net)}`} />
        <Row label={t('insights.returns')} value={`${o.sales.returns.count} · ${money(o.sales.returns.refunded)}`} />
        <Row label={t('insights.netAfterReturns')} value={money(o.sales.net_after_returns)} />
      </Card>

      <Card title={t('insights.profit')}>
        {o.profit.available ? (
          <>
            <Row label={t('insights.grossProfit')} value={money(o.profit.gross_profit)} />
            <Row label={t('insights.margin')} value={o.profit.margin_percent ? `${o.profit.margin_percent}%` : NOT_SET} />
            <p className="text-xs text-slate-500">{o.profit.note}</p>
          </>
        ) : (
          <NotAvailable what={o.profit} />
        )}
      </Card>

      <Card title={t('insights.stock')}>
        <Row label={t('insights.products')} value={o.inventory.products} />
        <Row label={t('insights.lowStock')} value={o.inventory.low_stock} />
        <Row label={t('insights.outOfStock')} value={o.inventory.out_of_stock} />
        <Row label={t('insights.stockValue')} value={o.inventory.stock_value ? money(o.inventory.stock_value) : t('insights.notAvailable')} />
        {o.inventory.products_without_cost > 0 && (
          <p className="text-xs text-slate-500">{t('insights.withoutCost', { count: o.inventory.products_without_cost })}</p>
        )}
      </Card>

      <Card title={t('insights.customers')}>
        <Row label={t('insights.outstanding')} value={money(o.customers.outstanding_total)} />
        <Row label={t('insights.owing')} value={o.customers.customers_owing} />
        <Row label={t('insights.collections')} value={money(o.customers.collections)} />
        <Row label={t('insights.creditSales')} value={`${o.customers.credit_sales.bills} · ${money(o.customers.credit_sales.unpaid)}`} />
        <Row label={t('insights.returning')} value={`${o.customers.returning_customers} / ${o.customers.customers_who_bought}`} />
      </Card>

      <Card title={t('insights.purchases')}>
        <Row label={t('insights.count')} value={o.purchases.count} />
        <Row label={t('insights.total')} value={money(o.purchases.total)} />
        <Row label={t('insights.netPurchases')} value={money(o.purchases.net)} />
        {o.purchases.by_supplier.map((s) => (
          <Row key={s.supplier} label={s.supplier} value={money(s.total)} />
        ))}
      </Card>

      <Card title={t('insights.offers')}>
        {o.promotions.available ? (
          <>
            <Row label={t('insights.discountGiven')} value={money(o.promotions.discount_total)} />
            <Row label={t('insights.offerUses')} value={o.promotions.offer_applications} />
            <Row label={t('insights.couponUses')} value={o.promotions.coupon_uses} />
          </>
        ) : (
          <NotAvailable what={o.promotions} />
        )}
      </Card>

      <Card title={t('insights.onlineStore')}>
        <NotAvailable what={o.online_store} />
      </Card>
    </div>
  )
}

/** Sales, stock, customers, purchases, profit and offers over a period, all read from the shop's own records. */
export function InsightsPage() {
  const { t } = useTranslation()
  const [days, setDays] = useState(30)
  const query = useQuery({
    queryKey: ['insights', days],
    queryFn: () => getOverview({ date_from: isoDaysAgo(days - 1), date_to: isoDaysAgo(0), bucket: days > 90 ? 'month' : days > 31 ? 'week' : 'day' }),
  })

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('insights.title')}
        subtitle={t('insights.subtitle')}
        actions={
          <FilterSelect aria-label={t('insights.period')} value={days} onChange={(event) => setDays(Number(event.target.value))}>
            {[7, 30, 90, 365].map((d) => (
              <option key={d} value={d}>
                {t('insights.lastDays', { count: d })}
              </option>
            ))}
          </FilterSelect>
        }
      />
      {query.isError ? (
        <QueryError error={query.error} onRetry={() => void query.refetch()} />
      ) : !query.data ? (
        <Spinner />
      ) : (
        <>
          <Sections o={query.data} />
          <Alert tone="info">
            <p className="text-sm">{query.data.source}</p>
          </Alert>
        </>
      )}
    </div>
  )
}
