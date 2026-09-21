import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { listPurchaseReturns, listSalesReturns } from '@/api/returns'
import { formatDate, formatMoney } from '@/lib/format'

import { ReturnStatusBadge } from './ReturnsPage'

/** The returns already made against one sale or purchase, shown on that document. */
export function ReturnsOfDocument({ kind, id }: { kind: 'sale' | 'purchase'; id: number }) {
  const { t } = useTranslation()
  const returns = useQuery({
    queryKey: [kind === 'sale' ? 'salesReturns' : 'purchaseReturns', 'of', id],
    queryFn: async () =>
      kind === 'sale'
        ? (await listSalesReturns({ sale_id: id, limit: 50 })).items.map((r) => ({ ...r, amount: r.total_refund, to: `/returns/sales/${r.id}` }))
        : (await listPurchaseReturns({ purchase_id: id, limit: 50 })).items.map((r) => ({ ...r, amount: r.total_amount, to: `/returns/purchases/${r.id}` })),
  })
  if (returns.isError) return null // the document itself is still fully usable without this list
  if (!returns.data) return null
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t('returns.returnsOfSale')}</h2>
      {returns.data.length === 0 ? (
        <p className="mt-2 text-slate-600">{t('returns.noneOfSale')}</p>
      ) : (
        <ul className="mt-2 divide-y divide-slate-100">
          {returns.data.map((row) => (
            <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <Link to={row.to} className="font-medium text-emerald-800 hover:underline">
                {row.return_no}
              </Link>
              <span className="text-sm text-slate-600">{formatDate(row.return_date)}</span>
              <span className="font-semibold">{formatMoney(row.amount)}</span>
              <ReturnStatusBadge status={row.status} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
