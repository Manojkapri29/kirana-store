import { API_V1_PREFIX, apiFetch, apiSend } from './client'
import type { PriceResult } from './types'

const BASE = `${API_V1_PREFIX}/price-intelligence`

export interface PriceCheckPayload {
  product_id?: number | null
  barcode?: string | null
  city?: string | null
  state?: string | null
  market?: string | null
}

/** Ask outside sources for prices. Information only: nothing here changes a price or a cost. */
export const checkPrices = (payload: PriceCheckPayload) => apiSend<PriceResult>('POST', `${BASE}/check`, payload)

export interface ProviderStatus {
  name: string
  label: string
  gives_prices: boolean
  /** true or false only: the key itself is never shown. */
  configured: boolean
  enabled: boolean
}

export const listProviders = () => apiFetch<{ items: ProviderStatus[] }>(`${BASE}/providers`)
