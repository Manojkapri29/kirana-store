import { API_V1_PREFIX, apiFetch, apiSend, type Query, type SendOptions } from './client'
import type {
  LookupResult,
  Page,
  Product,
  ProductCreatePayload,
  ProductSaved,
  ProductPayload,
  StatusFilter,
} from './types'

const BASE = `${API_V1_PREFIX}/products`

export interface ProductListParams {
  q?: string
  category_id?: number | null
  supplier_id?: number | null
  status?: StatusFilter
  limit?: number
  offset?: number
}

export const listProducts = (params: ProductListParams) =>
  apiFetch<Page<Product>>(BASE, { query: params as Query })

export const getProduct = (id: number) => apiFetch<Product>(`${BASE}/${id}`)

export const createProduct = (payload: ProductCreatePayload, options?: SendOptions) =>
  apiSend<ProductSaved>('POST', BASE, payload, options)

export const updateProduct = (id: number, changes: Partial<ProductPayload>) =>
  apiSend<ProductSaved>('PATCH', `${BASE}/${id}`, changes)

export const setProductActive = (id: number, active: boolean) =>
  apiSend<Product>('POST', `${BASE}/${id}/${active ? 'activate' : 'deactivate'}`)

/**
 * Find a product from a scan or typed text: exact barcode, exact SKU, exact name, then search. Never creates a
 * product: an unknown code answers "Barcode not found". Needs the plan's barcode feature (403 otherwise).
 */
export const lookupProduct = (code: string) =>
  apiFetch<LookupResult>(`${BASE}/lookup`, { query: { code } })
