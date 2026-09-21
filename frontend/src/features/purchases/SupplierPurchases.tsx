import { useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { getSupplierPurchaseTotals, listPurchases } from '@/api/purchases'
import { Badge, LinkButton, QueryError, Spinner } from '@/components/ui'
import { formatDate, formatMoney, NOT_SET } from '@/lib/format'

import { PurchaseStatusBadge } from './PurchaseStatusBadge'

const SHOWN = 10

/** The purchases from one supplier, newest first, with the posted total. (Payments come in a later phase.) */
export function SupplierPurchases({ supplierId, canBuy }: { supplierId: number; canBuy: boolean }) {
  const { t } = useTranslation()
  const purchases = useQuery({
    queryKey: ['purchases', { supplier_id: supplierId, limit: SHOWN }],
    queryFn: () => listPurchases({ supplier_id: supplierId, limit: SHOWN }),
  })
  const totals = useQuery({
    queryKey: ['supplierPurchaseTotals', supplierId],
    queryFn: () => getSupplierPurchaseTotals(supplierId),
  })

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">{t('suppliers.detail.purchaseHistory')}</h2>
          <p className="text-sm text-slate-500">{t('suppliers.detail.purchaseHistoryHint')}</p>
        </div>
        {canBuy && (
          <LinkButton to={`/purchases/new?supplier=${supplierId}`} variant="secondary">
            <Plus aria-hidden="true" className="size-5" />
            {t('purchases.add')}
          </LinkButton>
        )}
      </div>

      {totals.data && totals.data.posted_count > 0 && (
        <p className="text-slate-700">
          <Badge tone="green">{t('suppliers.detail.postedPurchases', { count: totals.data.posted_count })}</Badge>{' '}
          {t('suppliers.detail.postedTotal')}: <span className="font-semibold">{formatMoney(totals.data.posted_total)}</span>
        </p>
      )}

      {purchases.isError && <QueryError error={purchases.error} onRetry={() => void purchases.refetch()} />}
      {purchases.isPending && <Spinner />}
      {purchases.data?.total === 0 && (
        <p className="rounded-xl border border-dashed border-slate-300 bg-white px-4 py-6 text-center text-slate-600">
          {t('suppliers.detail.noPurchases')}
        </p>
      )}
      {purchases.data && purchases.data.items.length > 0 && (
        <>
          <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white shadow-sm">
            {purchases.data.items.map((purchase) => (
              <li key={purchase.id}>
                <Link to={`/purchases/${purchase.id}`} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 hover:bg-slate-50">
                  <span className="min-w-0">
                    <span className="block font-medium text-emerald-800">
                      {purchase.purchase_no ?? t('purchases.draftLabel', { id: purchase.id })}
                    </span>
                    <span className="text-sm text-slate-500">
                      {formatDate(purchase.purchase_date)} · {purchase.supplier_invoice_no ?? NOT_SET}
                    </span>
                  </span>
                  <span className="flex items-center gap-4 text-sm">
                    <span className="font-semibold">{formatMoney(purchase.total_amount)}</span>
                    <PurchaseStatusBadge status={purchase.status} />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          {purchases.data.total > SHOWN && (
            <p className="text-sm">
              <Link to={`/purchases?supplier=${supplierId}`} className="text-emerald-800 hover:underline">
                {t('suppliers.detail.allPurchases', { count: purchases.data.total })}
              </Link>
            </p>
          )}
        </>
      )}
    </section>
  )
}
