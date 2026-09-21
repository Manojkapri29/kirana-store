import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { LookupProduct, LookupResult } from '@/api/types'
import { Badge } from '@/components/ui'
import { formatMoney, formatQuantity, NOT_SET } from '@/lib/format'

/**
 * What was just scanned: name, SKU, barcode, brand, unit, the three prices, the average cost, the stock, the reorder
 * level and the supplier. The offer price (if any) is shown beside the MRP and selling price and never replaces them.
 */
export function ScannedProductCard({
  product,
  match,
  onClose,
}: {
  product: LookupProduct
  match: LookupResult['match_type']
  onClose: () => void
}) {
  const { t } = useTranslation()
  const rows: [string, string][] = [
    [t('scanner.card.sku'), product.sku],
    [t('scanner.card.barcode'), product.barcode ?? NOT_SET],
    [t('scanner.card.brand'), product.brand ?? NOT_SET],
    [t('scanner.card.unit'), product.unit_code],
    [t('scanner.card.mrp'), product.mrp ? formatMoney(product.mrp) : t('scanner.card.notSet')],
    [t('scanner.card.sellingPrice'), formatMoney(product.selling_price)],
    [t('scanner.card.purchasePrice'), product.purchase_price ? formatMoney(product.purchase_price) : t('scanner.card.notSet')],
    [t('scanner.card.avgCost'), product.avg_cost ? formatMoney(product.avg_cost) : t('scanner.card.notSet')],
    [t('scanner.card.stock'), `${formatQuantity(product.current_stock)} ${product.unit_code}`],
    [t('scanner.card.reorder'), `${formatQuantity(product.reorder_level)} ${product.unit_code}`],
    [t('scanner.card.supplier'), product.default_supplier_name ?? NOT_SET],
  ]
  return (
    <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-4" aria-live="polite">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-lg font-semibold text-slate-900">{product.name}</p>
          <Badge tone={match === 'SEARCH' ? 'amber' : 'green'}>{t(`scanner.match.${match}`)}</Badge>
        </div>
        <button type="button" onClick={onClose} aria-label={t('common.clear')} className="flex size-10 shrink-0 items-center justify-center rounded-lg text-slate-600 hover:bg-emerald-100">
          <X aria-hidden="true" className="size-5" />
        </button>
      </div>
      {product.offer && (
        <p className="mt-2 font-semibold text-emerald-900">
          {product.offer.name}: {t('scanner.card.offerPrice')} {formatMoney(product.offer.offer_price)}
        </p>
      )}
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-3">
            <dt className="text-slate-600">{label}</dt>
            <dd className="text-right font-medium text-slate-900">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
