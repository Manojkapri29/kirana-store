import { API_V1_PREFIX, apiFetch, apiSend, type Query } from './client'
import type {
  Page,
  Purchase,
  PurchaseHeaderPayload,
  PurchaseItemPayload,
  PurchaseStatus,
  PurchaseSummary,
  SupplierPurchaseTotals,
} from './types'

const BASE = `${API_V1_PREFIX}/purchases`

export interface PurchaseListParams {
  q?: string
  supplier_id?: number | null
  /** One status, or empty for all. */
  status?: PurchaseStatus | ''
  date_from?: string
  date_to?: string
  limit?: number
  offset?: number
}

export const listPurchases = (params: PurchaseListParams) =>
  apiFetch<Page<PurchaseSummary>>(BASE, { query: params as Query })

export const getPurchase = (id: number) => apiFetch<Purchase>(`${BASE}/${id}`)

export const getSupplierPurchaseTotals = (supplierId: number) =>
  apiFetch<SupplierPurchaseTotals>(`${BASE}/supplier-totals/${supplierId}`)

/** Create a draft with its lines. Nothing is posted and no stock moves. */
export const createPurchase = (payload: PurchaseHeaderPayload & { items: PurchaseItemPayload[] }) =>
  apiSend<Purchase>('POST', BASE, payload)

export const updatePurchaseHeader = (id: number, changes: Partial<PurchaseHeaderPayload>) =>
  apiSend<Purchase>('PATCH', `${BASE}/${id}`, changes)

/** Replace every line of a draft (this is also how lines are removed). */
export const replacePurchaseItems = (id: number, items: PurchaseItemPayload[]) =>
  apiSend<Purchase>('PUT', `${BASE}/${id}/items`, { items })

/** Post a draft: number it, add the stock and update the average costs, all at once. */
export const postPurchase = (id: number) => apiSend<Purchase>('POST', `${BASE}/${id}/post`)

/** Void a posted purchase (its stock is reversed) or discard a draft. A reason is required. */
export const voidPurchase = (id: number, reason: string) =>
  apiSend<Purchase>('POST', `${BASE}/${id}/void`, { reason })

/** Copy a voided purchase into a new draft to correct and post. */
export const correctPurchase = (id: number) => apiSend<Purchase>('POST', `${BASE}/${id}/correct`)
