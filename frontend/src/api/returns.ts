import { API_V1_PREFIX, apiFetch, apiSend, type Query, type SendOptions } from './client'
import type { Page } from './types'

export type ReturnStatus = 'POSTED' | 'VOID'
export type RefundMode = 'CASH' | 'UPI' | 'KHATA'
export type SupplierCreditMode = 'CASH' | 'UPI' | 'SUPPLIER_CREDIT'

interface FieldProblem {
  field: string
  message: string
}

// --- Sales returns ------------------------------------------------------------------------------------

export interface SalesReturnPreviewLine {
  sale_item_id: number | null
  product_name: string | null
  sku: string | null
  unit_code: string | null
  sold: string | null
  already_returned: string | null
  returnable: string | null
  quantity: string | null
  refund: string | null
  errors: FieldProblem[]
}

export interface SalesReturnPreview {
  sale_id: number
  lines: SalesReturnPreviewLine[]
  total_refund: string
  /** The most that can be given back as cash or UPI for this sale. */
  cash_refundable: string
  khata_allowed: boolean
  errors: FieldProblem[]
}

export interface ReturnLinePayload {
  quantity: string
}
export interface SalesReturnItemPayload extends ReturnLinePayload {
  sale_item_id: number
}
export interface PurchaseReturnItemPayload extends ReturnLinePayload {
  purchase_item_id: number
}

export interface SalesReturnPayload {
  sale_id: number
  refund_mode: RefundMode
  return_date: string | null
  reason: string | null
  items: SalesReturnItemPayload[]
}

export interface SalesReturnItem {
  id: number
  sale_item_id: number
  product_id: number
  sku: string
  product_name: string
  unit_code: string
  sold_quantity: string
  quantity: string
  refund_amount: string
  unit_cost: string | null
  cogs_amount: string | null
}

export interface SalesReturn {
  id: number
  return_no: string
  status: ReturnStatus
  sale_id: number
  invoice_no: string
  customer_id: number | null
  customer_name: string | null
  return_date: string
  refund_mode: RefundMode
  total_refund: string
  reason: string | null
  cogs_total: string | null
  created_by_name: string
  created_at: string
  void_reason: string | null
  voided_at: string | null
  items: SalesReturnItem[]
}

export interface SalesReturnRow {
  id: number
  return_no: string
  status: ReturnStatus
  sale_id: number
  invoice_no: string
  customer_name: string | null
  return_date: string
  refund_mode: RefundMode
  total_refund: string
  item_count: number
}

// --- Purchase returns ---------------------------------------------------------------------------------

export interface PurchaseReturnPreviewLine {
  purchase_item_id: number | null
  product_name: string | null
  sku: string | null
  unit_code: string | null
  bought: string | null
  already_returned: string | null
  returnable: string | null
  in_stock: string | null
  quantity: string | null
  credit: string | null
  errors: FieldProblem[]
}

export interface PurchaseReturnPreview {
  purchase_id: number
  lines: PurchaseReturnPreviewLine[]
  total_credit: string
}

export interface PurchaseReturnPayload {
  purchase_id: number
  credit_mode: SupplierCreditMode
  return_date: string | null
  reason: string | null
  items: PurchaseReturnItemPayload[]
}

export interface PurchaseReturnItem {
  id: number
  purchase_item_id: number
  product_id: number
  sku: string
  product_name: string
  unit_code: string
  bought_quantity: string
  quantity: string
  unit_cost: string
  line_total: string
}

export interface PurchaseReturn {
  id: number
  return_no: string
  status: ReturnStatus
  purchase_id: number
  purchase_no: string
  supplier_id: number
  supplier_name: string
  return_date: string
  credit_mode: SupplierCreditMode
  total_amount: string
  reason: string | null
  created_by_name: string
  created_at: string
  void_reason: string | null
  voided_at: string | null
  items: PurchaseReturnItem[]
}

export interface PurchaseReturnRow {
  id: number
  return_no: string
  status: ReturnStatus
  purchase_id: number
  purchase_no: string
  supplier_name: string
  return_date: string
  credit_mode: SupplierCreditMode
  total_amount: string
  item_count: number
}

export interface ReturnListParams {
  q?: string
  sale_id?: number
  purchase_id?: number
  limit?: number
  offset?: number
}

const SALES = `${API_V1_PREFIX}/sales-returns`
const PURCHASES = `${API_V1_PREFIX}/purchase-returns`

export const listSalesReturns = (params: ReturnListParams) => apiFetch<Page<SalesReturnRow>>(SALES, { query: params as Query })
export const getSalesReturn = (id: number) => apiFetch<SalesReturn>(`${SALES}/${id}`)
export const calculateSalesReturn = (payload: { sale_id: number; refund_mode: RefundMode | null; items: SalesReturnItemPayload[] }) =>
  apiSend<SalesReturnPreview>('POST', `${SALES}/calculate`, payload)
/** Posts a return: the goods go back on the shelf and the refund is worked out by the server. */
export const createSalesReturn = (payload: SalesReturnPayload, options?: SendOptions) =>
  apiSend<SalesReturn>('POST', SALES, payload, options)
export const voidSalesReturn = (id: number, reason: string) => apiSend<SalesReturn>('POST', `${SALES}/${id}/void`, { reason })

export const listPurchaseReturns = (params: ReturnListParams) => apiFetch<Page<PurchaseReturnRow>>(PURCHASES, { query: params as Query })
export const getPurchaseReturn = (id: number) => apiFetch<PurchaseReturn>(`${PURCHASES}/${id}`)
export const calculatePurchaseReturn = (payload: { purchase_id: number; items: PurchaseReturnItemPayload[] }) =>
  apiSend<PurchaseReturnPreview>('POST', `${PURCHASES}/calculate`, payload)
export const createPurchaseReturn = (payload: PurchaseReturnPayload, options?: SendOptions) =>
  apiSend<PurchaseReturn>('POST', PURCHASES, payload, options)
export const voidPurchaseReturn = (id: number, reason: string) =>
  apiSend<PurchaseReturn>('POST', `${PURCHASES}/${id}/void`, { reason })
