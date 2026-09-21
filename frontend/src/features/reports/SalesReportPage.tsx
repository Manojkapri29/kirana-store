import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import { getDiscountReport, getSalesSummary } from '@/api/reports'
import type { ReportTotals } from '@/api/types'
import { ExportButtons } from '@/components/ExportButtons'
import { Alert, PageHeader, QueryError, Spinner } from '@/components/ui'
import { formatDate, formatMoney } from '@/lib/format'

const DATE_INPUT = 'block min-h-12 rounded-lg border border-slate-300 bg-white px-3 text-base focus-visible:outline-2 focus-visible:outline-emerald-600'

function TotalsCard({ title, totals, note }: { title: string; totals: ReportTotals; note?: string }) {
  const { t } = useTranslation()
  const line = (label: string, value: string, sign = '') => (
    <div className="flex justify-between py-1.5">
      <dt className="text-slate-600">{label}</dt>
      <dd className="font-medium text-slate-900">{sign}{formatMoney(value)}</dd>
    </div>
  )
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
      <p className="text-sm text-slate-500">{t('reportsPage.salesCount')}: {totals.sales_count}</p>
      <dl className="mt-2 divide-y divide-slate-100">
        {line(t('reportsPage.gross'), totals.gross_sales)}
        {line(t('reportsPage.lineDiscount'), totals.line_discount, '− ')}
        {line(t('reportsPage.billDiscount'), totals.bill_discount, '− ')}
        {line(t('reportsPage.promotionDiscount'), totals.promotion_discount, '− ')}
        <div className="flex justify-between py-1.5 font-semibold">
          <dt>{t('reportsPage.discount')}</dt>
          <dd>{formatMoney(totals.discount)}</dd>
        </div>
        <div className="flex items-baseline justify-between pt-2">
          <dt className="text-lg font-semibold text-slate-900">{t('reportsPage.net')}</dt>
          <dd className="text-2xl font-bold text-slate-900">{formatMoney(totals.net_sales)}</dd>
        </div>
      </dl>
      {note && <p className="mt-2 text-sm text-slate-500">{note}</p>}
    </section>
  )
}

export function SalesReportPage() {
  const { t } = useTranslation()
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const rangeInvalid = from !== '' && to !== '' && from > to
  const params = { date_from: from || undefined, date_to: to || undefined }
  const summary = useQuery({ queryKey: ['salesSummary', params], queryFn: () => getSalesSummary(params), enabled: !rangeInvalid })
  const discounts = useQuery({ queryKey: ['discountReport', params], queryFn: () => getDiscountReport(params), enabled: !rangeInvalid, retry: false })
  const advancedRefused = discounts.error instanceof ApiError && discounts.error.status === 403
  const s = summary.data
  const d = discounts.data

  return (
    <div className="space-y-6">
      <PageHeader title={t('reportsPage.title')} subtitle={t('reportsPage.subtitle')} />
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <label className="flex items-center gap-2 text-sm text-slate-700">
          {t('reportsPage.from')}
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={DATE_INPUT} />
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          {t('reportsPage.to')}
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className={DATE_INPUT} />
        </label>
        {rangeInvalid && <p role="alert" className="text-sm font-medium text-red-700">{t('reportsPage.rangeInvalid')}</p>}
      </div>

      {summary.isError && <QueryError error={summary.error} onRetry={() => void summary.refetch()} />}
      {summary.isPending && !rangeInvalid && <Spinner />}

      {s && (
        <>
          <p className="text-sm text-slate-600">{formatDate(s.date_from)} – {formatDate(s.date_to)}</p>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <TotalsCard title={t('reportsPage.detailed')} totals={s.detailed} />
            <TotalsCard title={t('reportsPage.quick')} totals={s.quick} note={t('reportsPage.quickNoProfit')} />
            <TotalsCard title={t('reportsPage.combined')} totals={s.combined} />
          </div>

          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-900">{t('reportsPage.profitHeading')}</h2>
            <dl className="mt-2 divide-y divide-slate-100">
              <div className="flex justify-between py-2">
                <dt className="text-slate-600">{t('reportsPage.detailedProfit')}</dt>
                <dd className="font-medium">{s.detailed_gross_profit === null ? t('reportsPage.notAvailable') : formatMoney(s.detailed_gross_profit)}</dd>
              </div>
              <div className="flex justify-between py-2">
                <dt className="text-slate-600">{t('reportsPage.combinedProfit')}</dt>
                <dd className="font-medium text-slate-600">{t('reportsPage.notAvailable')}</dd>
              </div>
            </dl>
            {s.detailed_sales_without_cost > 0 && <p className="mt-2 text-sm text-slate-600">{t('reportsPage.profitLeftOut', { count: s.detailed_sales_without_cost })}</p>}
          </section>

          <section className="space-y-3">
            <h2 className="text-lg font-semibold text-slate-900">{t('reportsPage.daily')}</h2>
            {s.days.length === 0 ? (
              <p className="text-slate-600">{t('reportsPage.noSales')}</p>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
                <table className="min-w-full divide-y divide-slate-200 text-sm">
                  <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="px-4 py-3">{t('reportsPage.date')}</th>
                      <th className="px-4 py-3 text-right">{t('reportsPage.detailed')}</th>
                      <th className="px-4 py-3 text-right">{t('reportsPage.quick')}</th>
                      <th className="px-4 py-3 text-right">{t('reportsPage.discount')}</th>
                      <th className="px-4 py-3 text-right">{t('reportsPage.net')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {s.days.map((day) => (
                      <tr key={day.day}>
                        <td className="whitespace-nowrap px-4 py-2">{formatDate(day.day)}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right">{formatMoney(day.detailed.net_sales)}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right">{formatMoney(day.quick.net_sales)}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right">{formatMoney(day.combined.discount)}</td>
                        <td className="whitespace-nowrap px-4 py-2 text-right font-semibold">{formatMoney(day.combined.net_sales)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="text-sm font-medium text-slate-700">{t('reportsPage.exportSummary')}</p>
            <ExportButtons kind="sales-summary" filters={params} />
          </section>
        </>
      )}

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900">{t('reportsPage.discountsHeading')}</h2>
        {advancedRefused && <Alert tone="warning">{t('reportsPage.advancedNeeded')}</Alert>}
        {discounts.isError && !advancedRefused && <QueryError error={discounts.error} onRetry={() => void discounts.refetch()} />}
        {d && (
          <>
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {[
                [t('reportsPage.discount'), formatMoney(d.total_discount)],
                [t('reportsPage.promotionsUsed'), String(d.promotions_used)],
                [t('reportsPage.applications'), String(d.promotion_applications)],
                [t('reportsPage.couponUses'), `${d.coupon_uses} · ${formatMoney(d.coupon_discount)}`],
              ].map(([label, value]) => (
                <div key={label} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                  <dt className="text-sm text-slate-600">{label}</dt>
                  <dd className="text-xl font-bold text-slate-900">{value}</dd>
                </div>
              ))}
            </dl>
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <h3 className="mb-2 font-semibold text-slate-800">{t('reportsPage.byPromotion')}</h3>
                <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white shadow-sm">
                  {d.by_promotion.length === 0 && <li className="px-4 py-3 text-slate-500">—</li>}
                  {d.by_promotion.map((row) => (
                    <li key={row.promotion_id} className="flex justify-between gap-3 px-4 py-2">
                      <span>{row.name} <span className="text-sm text-slate-500">× {row.uses}</span></span>
                      <span className="font-medium">{formatMoney(row.discount)}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h3 className="mb-2 font-semibold text-slate-800">{t('reportsPage.byCoupon')}</h3>
                <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white shadow-sm">
                  {d.by_coupon.length === 0 && <li className="px-4 py-3 text-slate-500">—</li>}
                  {d.by_coupon.map((row) => (
                    <li key={row.code} className="flex justify-between gap-3 px-4 py-2">
                      <span className="font-mono">{row.code} <span className="font-sans text-sm text-slate-500">× {row.uses}</span></span>
                      <span className="font-medium">{formatMoney(row.discount)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            <div>
              <h3 className="mb-2 font-semibold text-slate-800">{t('reportsPage.byDate')}</h3>
              <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white shadow-sm">
                {d.by_date.length === 0 && <li className="px-4 py-3 text-slate-500">—</li>}
                {d.by_date.map((row) => (
                  <li key={row.day} className="flex justify-between gap-3 px-4 py-2">
                    <span>{formatDate(row.day)}</span>
                    <span className="font-medium">{formatMoney(row.total)}</span>
                  </li>
                ))}
              </ul>
            </div>
            <p className="text-sm font-medium text-slate-700">{t('reportsPage.exportDiscounts')}</p>
            <ExportButtons kind="discount-report" filters={params} />
          </>
        )}
      </section>
    </div>
  )
}
