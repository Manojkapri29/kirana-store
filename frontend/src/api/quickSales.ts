import { API_V1_PREFIX, apiFetch, apiSend, type Query } from './client'
import type { Page, PaymentType, QuickSale, QuickSalePayload, SalePaymentPayload, SaleStatus } from './types'

const BASE = `${API_V1_PREFIX}/quick-sales`

export interface QuickSaleListParams {
  q?: string
  customer_id?: number | null
  status?: SaleStatus | ''
  payment_type?: PaymentType | ''
  date_from?: string
  date_to?: string
  limit?: number
  offset?: number
}

export const listQuickSales = (params: QuickSaleListParams) => apiFetch<Page<QuickSale>>(BASE, { query: params as Query })
export const getQuickSale = (id: number) => apiFetch<QuickSale>(`${BASE}/${id}`)
export const createQuickSale = (payload: QuickSalePayload) => apiSend<QuickSale>('POST', BASE, payload)
export const updateQuickSale = (id: number, changes: QuickSalePayload) => apiSend<QuickSale>('PATCH', `${BASE}/${id}`, changes)
/** Settle the payment and number it. Any unpaid part goes on the customer's khata. No stock is touched. */
export const postQuickSale = (id: number, payment: SalePaymentPayload) => apiSend<QuickSale>('POST', `${BASE}/${id}/post`, payment)
export const voidQuickSale = (id: number, reason: string) => apiSend<QuickSale>('POST', `${BASE}/${id}/void`, { reason })
