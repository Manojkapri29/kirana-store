import { API_V1_PREFIX, apiBlob, apiFetch, apiSend, type SendOptions } from './client'
import type { Product, ProductSaved } from './types'

const BASE = `${API_V1_PREFIX}/image-intelligence`

export interface ImageStatus {
  allowed_by_plan: boolean | null
  /** Is an image analysis service set up on the server? (Never says which key or setting.) */
  configured: boolean
  provider_label: string | null
  max_bytes: number
  max_side: number
  formats: string[]
}

export interface Suggestion {
  field: string
  value: string
  /** "Detected" or "Suggested": never "confirmed". */
  label: 'Detected' | 'Suggested'
  source: string
  category_id: number | null
  unit_id: number | null
}

export interface DuplicateMatch {
  product_id: number
  name: string
  sku: string
  barcode: string | null
  brand: string | null
  strength: 'EXACT' | 'LIKELY' | 'POSSIBLE'
  reasons: string[]
}

export type AnalysisStatus = 'LOCAL_ONLY' | 'PROVIDER_USED' | 'NOT_CONFIGURED' | 'PROVIDER_FAILED'

export interface Analysis {
  status: AnalysisStatus
  message: string | null
  provider_label: string | null
  sent_to_provider: boolean
  image: { content_type: string; width: number; height: number; size_bytes: number }
  barcode: string | null
  barcode_note: string | null
  suggestions: Suggestion[]
  existing_products: Product[]
  possible_duplicates: DuplicateMatch[]
  visible_text: string[]
  notes: string[]
  stored: boolean
  changes_data: boolean
}

export interface AnalyzePayload {
  image_base64: string
  content_type: string | null
  barcode_hint: string | null
  use_provider: boolean
  enrich: boolean
}

export interface ReviewedProduct {
  sku: string
  name: string
  brand: string | null
  category_id: number
  unit_id: number
  default_supplier_id: number | null
  reorder_level: string
  mrp: string | null
  selling_price: string
  purchase_price: string | null
  barcode: string | null
}

export interface ConfirmPayload {
  product: ReviewedProduct
  acknowledge_duplicates: boolean
  keep_image: boolean
  image_base64: string | null
  content_type: string | null
}

export interface Confirmed extends ProductSaved {
  image_kept: boolean
}

export interface KeptImage {
  product_id: number
  content_type: string
  width: number
  height: number
  size_bytes: number
  created_at: string
}

export const getImageStatus = () => apiFetch<ImageStatus>(`${BASE}/status`)

/** Read a photo. Nothing is saved or changed by this call. */
export const analyzePhoto = (payload: AnalyzePayload) =>
  apiSend<Analysis>('POST', `${BASE}/analyze`, payload, { timeoutMs: 60_000 })

/** The one call that creates a product from a photo, and only after the person reviewed and confirmed it. */
export const confirmProductFromPhoto = (payload: ConfirmPayload, options?: SendOptions) =>
  apiSend<Confirmed>('POST', `${BASE}/confirm-product`, payload, { timeoutMs: 60_000, ...options })

export const keepProductImage = (productId: number, payload: { image_base64: string; content_type: string | null }) =>
  apiSend<KeptImage>('PUT', `${BASE}/products/${productId}/image`, payload, { timeoutMs: 60_000 })

export const removeProductImage = (productId: number) => apiSend<void>('POST', `${BASE}/products/${productId}/image/remove`)

/** The kept photo, fetched through the API (it is private, so it has no public address). */
export const getProductImage = (productId: number) => apiBlob(`${BASE}/products/${productId}/image`)
