import { ApiError } from '@/api/client'

import { getOne, putOne, type Scope } from './db'
import { CHANGED_EVENT } from './queue'
import { fetchSnapshot } from './sync'

export interface SnapshotProduct {
  id: number; name: string; sku: string; barcode: string | null; brand: string | null; category: string; unit: string
  allows_decimal: boolean; selling_price: string | null; stock: string
}
export interface SnapshotCustomer { id: number; name: string; phone: string | null }
export interface Snapshot<T> { name: string; asOf: string; savedAt: string; truncated: boolean; items: T[] }

/** Older than this and the screen warns loudly: prices and stock may have changed a lot. */
export const STALE_AFTER_HOURS = 24 * 3
export const ageHours = (asOf: string) => (Date.now() - Date.parse(asOf)) / 3_600_000

async function refreshOne<T>(scope: Scope, name: 'products' | 'customers'): Promise<boolean> {
  try {
    const data = await fetchSnapshot<{ as_of: string; items: T[]; truncated: boolean }>(name)
    await putOne<Snapshot<T>>(scope, 'snapshots', { name, asOf: data.as_of, savedAt: new Date().toISOString(), truncated: data.truncated, items: data.items })
    return true
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) return false // this role may not see them: nothing is kept
    throw error
  }
}

/** Fetch fresh copies. Each is stamped with the SERVER's time, which is what "last synced" shows. */
export async function refreshSnapshots(scope: Scope): Promise<{ products: boolean; customers: boolean }> {
  const products = await refreshOne<SnapshotProduct>(scope, 'products')
  const customers = await refreshOne<SnapshotCustomer>(scope, 'customers')
  window.dispatchEvent(new Event(CHANGED_EVENT))
  return { products, customers }
}

export const readSnapshot = <T,>(scope: Scope, name: 'products' | 'customers') => getOne<Snapshot<T>>(scope, 'snapshots', name)

/** Exact barcode or SKU match on the device copy. The server's own lookup (with its EAN/UPC variants) is used whenever there is a connection. */
export function findByCode(items: SnapshotProduct[], code: string): SnapshotProduct | undefined {
  const c = code.trim()
  if (!c) return undefined
  return items.find((p) => p.barcode === c || p.sku.toLowerCase() === c.toLowerCase())
}
