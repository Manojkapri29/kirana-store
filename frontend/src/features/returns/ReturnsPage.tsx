import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { listPurchaseReturns, listSalesReturns, type ReturnStatus } from '@/api/returns'
import { ExportButtons } from '@/components/ExportButtons'
import { Pagination } from '@/components/Pagination'
import { SearchInput } from '@/components/SearchInput'
import { Badge, EmptyState, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useDebounced } from '@/hooks/useDebounced'
import { formatDate, formatMoney } from '@/lib/format'

const PAGE_SIZE = 25

export function ReturnStatusBadge({ status }: { status: ReturnStatus }) {
  const { t } = useTranslation()
  return <Badge tone={status === 'VOID' ? 'slate' : 'green'}>{t(`returns.status.${status}`)}</Badge>
}

/** Every return, sales and purchase, in one place. A return never edits or deletes the sale or purchase it came from. */
export function ReturnsPage() {
  const { t } = useTranslation()
  const [tab, setTab] = useState<'sales' | 'purchases'>('sales')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const q = useDebounced(search.trim())

  const sales = useQuery({
    queryKey: ['salesReturns', q, offset],
    queryFn: () => listSalesReturns({ q, limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
    enabled: tab === 'sales',
  })
  const purchases = useQuery({
    queryKey: ['purchaseReturns', q, offset],
    queryFn: () => listPurchaseReturns({ q, limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
    enabled: tab === 'purchases',
  })
  const active = tab === 'sales' ? sales : purchases
  const total = active.data?.total ?? 0
  const heading = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'

  function pick(next: 'sales' | 'purchases') {
    setTab(next)
    setOffset(0)
  }

  const tabClass = (name: 'sales' | 'purchases') =>
    `min-h-12 rounded-lg px-5 font-medium ${tab === name ? 'bg-emerald-600 text-white' : 'border border-slate-300 bg-white text-slate-800 hover:bg-slate-50'}`

  return (
    <div className="space-y-6">
      <PageHeader title={t('returns.title')} subtitle={t('returns.subtitle')} />
      <div className="flex flex-wrap gap-3" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'sales'} className={tabClass('sales')} onClick={() => pick('sales')}>
          {t('returns.salesTab')}
        </button>
        <button type="button" role="tab" aria-selected={tab === 'purchases'} className={tabClass('purchases')} onClick={() => pick('purchases')}>
          {t('returns.purchaseTab')}
        </button>
      </div>
      <SearchInput
        value={search}
        onChange={(value) => {
          setSearch(value)
          setOffset(0)
        }}
        placeholder={t('returns.searchPlaceholder')}
      />

      {active.isError && <QueryError error={active.error} onRetry={() => void active.refetch()} />}
      {active.isPending && <Spinner />}
      {active.data && total === 0 && <EmptyState title={q === '' ? t('returns.empty') : t('returns.noMatch')} />}

      {tab === 'sales' && sales.data && sales.data.items.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200">
            <thead className="bg-slate-50">
              <tr>
                <th className={heading}>{t('returns.columns.number')}</th>
                <th className={heading}>{t('returns.columns.date')}</th>
                <th className={heading}>{t('returns.columns.document')}</th>
                <th className={heading}>{t('returns.columns.party')}</th>
                <th className={heading}>{t('returns.columns.mode')}</th>
                <th className={`${heading} text-right`}>{t('returns.columns.amount')}</th>
                <th className={heading}>{t('returns.columns.status')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {sales.data.items.map((row) => (
                <tr key={row.id} className={row.status === 'VOID' ? 'text-slate-500' : ''}>
                  <td className="whitespace-nowrap px-4 py-3">
                    <Link to={`/returns/sales/${row.id}`} className="font-medium text-emerald-800 hover:underline">
                      {row.return_no}
                    </Link>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">{formatDate(row.return_date)}</td>
                  <td className="px-4 py-3">
                    <Link to={`/sales/${row.sale_id}`} className="hover:underline">
                      {row.invoice_no}
                    </Link>
                  </td>
                  <td className="px-4 py-3">{row.customer_name ?? '—'}</td>
                  <td className="px-4 py-3 text-sm">{t(`returns.refundModes.${row.refund_mode}`)}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-right font-semibold">{formatMoney(row.total_refund)}</td>
                  <td className="px-4 py-3">
                    <ReturnStatusBadge status={row.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'purchases' && purchases.data && purchases.data.items.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200">
            <thead className="bg-slate-50">
              <tr>
                <th className={heading}>{t('returns.columns.number')}</th>
                <th className={heading}>{t('returns.columns.date')}</th>
                <th className={heading}>{t('returns.columns.document')}</th>
                <th className={heading}>{t('returns.columns.party')}</th>
                <th className={heading}>{t('returns.columns.mode')}</th>
                <th className={`${heading} text-right`}>{t('returns.columns.amount')}</th>
                <th className={heading}>{t('returns.columns.status')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {purchases.data.items.map((row) => (
                <tr key={row.id} className={row.status === 'VOID' ? 'text-slate-500' : ''}>
                  <td className="whitespace-nowrap px-4 py-3">
                    <Link to={`/returns/purchases/${row.id}`} className="font-medium text-emerald-800 hover:underline">
                      {row.return_no}
                    </Link>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">{formatDate(row.return_date)}</td>
                  <td className="px-4 py-3">
                    <Link to={`/purchases/${row.purchase_id}`} className="hover:underline">
                      {row.purchase_no}
                    </Link>
                  </td>
                  <td className="px-4 py-3">{row.supplier_name}</td>
                  <td className="px-4 py-3 text-sm">{t(`returns.creditModes.${row.credit_mode}`)}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-right font-semibold">{formatMoney(row.total_amount)}</td>
                  <td className="px-4 py-3">
                    <ReturnStatusBadge status={row.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {active.data && total > 0 && (
        <>
          <Pagination total={active.data.total} limit={active.data.limit} offset={active.data.offset} onChange={setOffset} />
          <div className="border-t border-slate-200 pt-4">
            <p className="mb-2 text-sm font-medium text-slate-700">{tab === 'sales' ? t('returns.exportSales') : t('returns.exportPurchases')}</p>
            <ExportButtons kind={tab === 'sales' ? 'sales-returns' : 'purchase-returns'} filters={{ q }} />
          </div>
        </>
      )}
    </div>
  )
}
