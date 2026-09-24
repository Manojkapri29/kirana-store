import { API_V1_PREFIX, apiFetch, apiSend } from './client'

const STORE = `${API_V1_PREFIX}/store`
const ORDERS = `${API_V1_PREFIX}/online-orders`
const PUBLIC = `${API_V1_PREFIX}/public/stores`

export type OrderStatus = 'PLACED' | 'ACCEPTED' | 'PREPARING' | 'READY' | 'OUT_FOR_DELIVERY' | 'DELIVERED' | 'REJECTED' | 'CANCELLED'
export type Fulfilment = 'DELIVERY' | 'PICKUP'
export type PayHow = 'COD' | 'UPI'

// ---- shop side -------------------------------------------------------------------------------------------------
export interface StoreSettings {
  slug: string; display_name: string; is_open: boolean; accepts_cod: boolean; accepts_upi: boolean
  delivery_enabled: boolean; pickup_enabled: boolean; min_order_amount: string
  contact_phone: string | null; announcement: string | null; public_path: string
}
export type StoreSettingsInput = Partial<Omit<StoreSettings, 'public_path'>>
export interface Listing { product_id: number; sku: string; name: string; category: string; unit: string; price: string; is_active: boolean; is_visible: boolean }
export interface OrderRow {
  id: number; order_no: string; status: OrderStatus; fulfilment: Fulfilment; payment: PayHow; customer_name: string
  customer_phone: string; total_amount: string; item_count: number; placed_at: string; sale_id: number | null
}
export interface OrderDetail extends Omit<OrderRow, 'item_count'> {
  delivery_address: string | null; notes: string | null; customer_id: number | null; invoice_no: string | null; sale_total: string | null
  decision_reason: string | null; warnings: string[]; allowed_next: OrderStatus[]
  items: { product_id: number; product_name: string; unit: string; quantity: string; unit_price: string; line_total: string }[]
  events: { from_status: string | null; to_status: string; actor: string; note: string | null; at: string }[]
}

export const getStoreSettings = () => apiFetch<{ store: StoreSettings | null }>(`${STORE}/settings`)
export const saveStoreSettings = (body: StoreSettingsInput) => apiSend<StoreSettings>('PUT', `${STORE}/settings`, body)
export const getListings = (q: string, offset: number) => apiFetch<{ items: Listing[]; total: number; limit: number; offset: number }>(`${STORE}/listings`, { query: { q: q || undefined, limit: 20, offset } })
export const setListing = (productId: number, visible: boolean) => apiSend<Listing>('PUT', `${STORE}/listings/${productId}`, { visible })
export const getOrders = (status: OrderStatus | '', offset: number) => apiFetch<{ items: OrderRow[]; total: number; limit: number; offset: number }>(ORDERS, { query: { status: status || undefined, limit: 20, offset } })
export const getOrdersSummary = () => apiFetch<{ by_status: Record<string, number>; open: number; delivered_value: string }>(`${ORDERS}/summary`)
export const getOrder = (id: number) => apiFetch<OrderDetail>(`${ORDERS}/${id}`)
export const acceptOrder = (id: number) => apiSend<OrderDetail>('POST', `${ORDERS}/${id}/accept`, {})
export const rejectOrder = (id: number, reason: string) => apiSend<OrderDetail>('POST', `${ORDERS}/${id}/reject`, { reason })
export const cancelOrder = (id: number, reason: string) => apiSend<OrderDetail>('POST', `${ORDERS}/${id}/cancel`, { reason })
export const advanceOrder = (id: number, status: OrderStatus) => apiSend<OrderDetail>('POST', `${ORDERS}/${id}/advance`, { status })

// ---- the public storefront (no sign-in) -----------------------------------------------------------------------
export interface PublicStore {
  slug: string; name: string; is_open: boolean; accepts_cod: boolean; accepts_upi: boolean; delivery_enabled: boolean
  pickup_enabled: boolean; min_order_amount: string; contact_phone: string | null; announcement: string | null
}
export interface PublicProduct { id: number; name: string; category: string; unit: string; allows_decimal: boolean; price: string; in_stock: boolean }
export interface PublicOrder {
  order_no: string; reference: string; status: OrderStatus; fulfilment: Fulfilment; payment: PayHow; total_amount: string; placed_at: string
  items: { name: string; unit: string; quantity: string; unit_price: string; line_total: string }[]
  timeline: { status: string; at: string }[]; store_name: string; store_phone: string | null; can_cancel: boolean; tracking_token: string | null
}
export interface PublicOrderInput {
  customer_name: string; customer_phone: string; fulfilment: Fulfilment; payment: PayHow; delivery_address?: string; notes?: string
  items: { product_id: number; quantity: string }[]
}
export const getPublicStore = (slug: string) => apiFetch<PublicStore>(`${PUBLIC}/${encodeURIComponent(slug)}`)
export const getPublicProducts = (slug: string, q: string, offset: number) =>
  apiFetch<{ items: PublicProduct[]; total: number; limit: number; offset: number }>(`${PUBLIC}/${encodeURIComponent(slug)}/products`, { query: { q: q || undefined, limit: 24, offset } })
export const placePublicOrder = (slug: string, body: PublicOrderInput, key: string) =>
  apiSend<PublicOrder>('POST', `${PUBLIC}/${encodeURIComponent(slug)}/orders`, body, { idempotencyKey: key })
export const trackPublicOrder = (slug: string, reference: string, token: string) =>
  apiFetch<PublicOrder>(`${PUBLIC}/${encodeURIComponent(slug)}/orders/${encodeURIComponent(reference)}`, { query: { token } })
export const cancelPublicOrder = (slug: string, reference: string, token: string) =>
  apiSend<PublicOrder>('POST', `${PUBLIC}/${encodeURIComponent(slug)}/orders/${encodeURIComponent(reference)}/cancel?token=${encodeURIComponent(token)}`, {})
