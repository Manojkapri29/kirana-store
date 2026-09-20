import { API_V1_PREFIX, apiFetch, apiSend, type Query } from './client'
import type {
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
  status?: StatusFilter
  limit?: number
  offset?: number
}

export const listProducts = (params: ProductListParams) =>
  apiFetch<Page<Product>>(BASE, { query: params as Query })

export const getProduct = (id: number) => apiFetch<Product>(`${BASE}/${id}`)

export const createProduct = (payload: ProductCreatePayload) =>
  apiSend<ProductSaved>('POST', BASE, payload)

export const updateProduct = (id: number, changes: Partial<ProductPayload>) =>
  apiSend<ProductSaved>('PATCH', `${BASE}/${id}`, changes)

export const setProductActive = (id: number, active: boolean) =>
  apiSend<Product>('POST', `${BASE}/${id}/${active ? 'activate' : 'deactivate'}`)
