import { API_V1_PREFIX, apiFetch, apiSend, type Query } from './client'
import type {
  InventoryItem,
  InventoryTransaction,
  OpeningStockPayload,
  OpeningStockResult,
  Page,
  StatusFilter,
  StockStatus,
} from './types'

const BASE = `${API_V1_PREFIX}/inventory`

export interface InventoryListParams {
  q?: string
  category_id?: number | null
  status?: StatusFilter
  stock_status?: StockStatus | null
  limit?: number
  offset?: number
}

export const listInventory = (params: InventoryListParams) =>
  apiFetch<Page<InventoryItem>>(BASE, { query: params as Query })

export const getProductHistory = (productId: number, params: { limit?: number; offset?: number }) =>
  apiFetch<Page<InventoryTransaction>>(`${BASE}/products/${productId}/transactions`, {
    query: params as Query,
  })

export const createOpeningStock = (payload: OpeningStockPayload) =>
  apiSend<OpeningStockResult>('POST', `${BASE}/opening-stock`, payload)
