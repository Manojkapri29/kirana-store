import { API_V1_PREFIX, apiFetch, apiSend, type Query } from './client'
import type {
  Page,
  PaymentType,
  Sale,
  SaleHeaderPayload,
  SaleItemPayload,
  SalePaymentPayload,
  SalePreview,
  SaleStatus,
  SaleSummary,
} from './types'

const BASE = `${API_V1_PREFIX}/sales`

export interface SaleListParams {
  q?: string
  customer_id?: number | null
  /** One status, or empty for all. */
  status?: SaleStatus | ''
  payment_type?: PaymentType | ''
  date_from?: string
  date_to?: string
  limit?: number
  offset?: number
}

export const listSales = (params: SaleListParams) => apiFetch<Page<SaleSummary>>(BASE, { query: params as Query })

export const getSale = (id: number) => apiFetch<Sale>(`${BASE}/${id}`)

/** Price a cart without saving anything. The billing screen shows exactly what this returns. */
export const calculateSale = (payload: {
  items: SaleItemPayload[]
  discount: string | null
  amount_paid?: string | null
  customer_id?: number | null
  coupon_code?: string | null
}) => apiSend<SalePreview>('POST', `${BASE}/calculate`, payload)

/** Create a draft (a cart). Nothing is posted and no stock moves. */
export const createSale = (payload: SaleHeaderPayload & { items: SaleItemPayload[] }) =>
  apiSend<Sale>('POST', BASE, payload)

export const updateSale = (id: number, changes: Partial<SaleHeaderPayload>) =>
  apiSend<Sale>('PATCH', `${BASE}/${id}`, changes)

/** Replace every line of a draft (this is also how lines are changed or removed). */
export const replaceSaleItems = (id: number, items: SaleItemPayload[]) =>
  apiSend<Sale>('PUT', `${BASE}/${id}/items`, { items })

/** Post a draft: settle the payment, number it, take the stock out, record the cost, charge any credit. */
export const postSale = (id: number, payment: SalePaymentPayload) => apiSend<Sale>('POST', `${BASE}/${id}/post`, payment)

/** Void a posted sale (stock and khata are reversed) or discard a draft. A reason is required. */
export const voidSale = (id: number, reason: string) => apiSend<Sale>('POST', `${BASE}/${id}/void`, { reason })

/** Copy a voided sale into a new draft to correct and post. */
export const correctSale = (id: number) => apiSend<Sale>('POST', `${BASE}/${id}/correct`)
