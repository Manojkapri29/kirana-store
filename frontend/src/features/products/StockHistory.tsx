import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { getProductHistory } from '@/api/inventory'
import type { InventoryTransaction, Product } from '@/api/types'
import { ExportButtons } from '@/components/ExportButtons'
import { Pagination } from '@/components/Pagination'
import { EmptyState, QueryError, Spinner } from '@/components/ui'
import { formatDate, formatDateTime, formatMoney, formatQuantity } from '@/lib/format'

const PAGE_SIZE = 20

/** Every stock movement of one product: what came in, what went out, and what remained. */
export function StockHistory({ product }: { product: Product }) {
  const { t } = useTranslation()
  const [offset, setOffset] = useState(0)
  const history = useQuery({
    queryKey: ['history', product.id, offset],
    queryFn: () => getProductHistory(product.id, { limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
  })

  return (
    <section id="history" className="space-y-4">
      <h2 className="text-lg font-semibold text-slate-900">{t('history.heading')}</h2>

      {history.isError && <QueryError error={history.error} onRetry={() => void history.refetch()} />}
      {history.isPending && <Spinner />}
      {history.data?.total === 0 && <EmptyState title={t('history.empty')} />}

      {history.data && history.data.total > 0 && (
        <>
          <HistoryTable rows={history.data.items} unitCode={product.unit_code} />
          <Pagination
            total={history.data.total}
            limit={history.data.limit}
            offset={history.data.offset}
            onChange={setOffset}
          />
          <div className="border-t border-slate-200 pt-4">
            <p className="mb-2 text-sm font-medium text-slate-700">{t('history.exportHistory')}</p>
            <ExportButtons kind="inventory-history" filters={{ product_id: product.id }} />
          </div>
        </>
      )}
    </section>
  )
}

function HistoryTable({ rows, unitCode }: { rows: InventoryTransaction[]; unitCode: string }) {
  const { t } = useTranslation()
  const heading = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'

  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
      <table className="min-w-full divide-y divide-slate-200">
        <thead className="bg-slate-50">
          <tr>
            <th className={heading}>{t('history.columns.date')}</th>
            <th className={heading}>{t('history.columns.type')}</th>
            <th className={`${heading} text-right`}>{t('history.columns.change')}</th>
            <th className={`${heading} text-right`}>{t('history.columns.balance')}</th>
            <th className={`${heading} text-right`}>{t('history.columns.unitCost')}</th>
            <th className={heading}>{t('history.columns.details')}</th>
            <th className={heading}>{t('history.columns.recordedBy')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row) => {
            const incoming = !row.qty_delta.startsWith('-')
            return (
              <tr key={row.id}>
                <td className="whitespace-nowrap px-4 py-3 text-sm">{formatDate(row.txn_date)}</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm font-medium">{t(`history.types.${row.txn_type}`)}</td>
                <td
                  className={`whitespace-nowrap px-4 py-3 text-right font-semibold ${incoming ? 'text-emerald-700' : 'text-red-700'}`}
                >
                  {incoming ? '+' : ''}
                  {formatQuantity(row.qty_delta)} {unitCode}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-right">
                  {formatQuantity(row.balance_after)} {unitCode}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-right text-sm">{formatMoney(row.unit_cost)}</td>
                <td className="px-4 py-3 text-sm">
                  {row.purchase_id && (
                    <Link to={`/purchases/${row.purchase_id}`} className="font-medium text-emerald-800 hover:underline">
                      {row.purchase_no}
                    </Link>
                  )}
                  {row.sale_id && (
                    <Link to={`/sales/${row.sale_id}`} className="font-medium text-emerald-800 hover:underline">
                      {row.sale_no}
                    </Link>
                  )}
                  {row.reason_code && <div className="font-medium">{t(`history.reasons.${row.reason_code}`)}</div>}
                  {row.note && <div className="text-slate-600">{row.note}</div>}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm">
                  {row.created_by_name}
                  <div className="text-xs text-slate-500">{formatDateTime(row.created_at)}</div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
