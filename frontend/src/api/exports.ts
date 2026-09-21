import { API_V1_PREFIX, apiDownload, type Query } from './client'
import type { ExportFormat } from './types'

export type ExportKind =
  | 'products'
  | 'inventory'
  | 'inventory-history'
  | 'purchases'
  | 'purchase-items'
  | `purchases/${number}` // one purchase with its lines
  | 'customers'
  | 'sales'
  | 'sale-items'
  | 'quick-sales'
  | 'promotions'
  | 'promotion-usage'
  | 'price-history'
  | 'sales-summary'
  | 'discount-report'
  | `sales/${number}` // one sale with its lines
  | `customers/${number}/ledger` // one customer's khata

/** Fetch an export and hand it to the browser as a file download. */
export async function downloadExport(
  kind: ExportKind,
  format: ExportFormat,
  filters: Query = {},
): Promise<void> {
  const { blob, filename } = await apiDownload(`${API_V1_PREFIX}/exports/${kind}`, { ...filters, format })
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(objectUrl)
}
