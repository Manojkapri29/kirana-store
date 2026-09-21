import { API_V1_PREFIX, apiFetch, apiSend, type Query, type SendOptions } from './client'
import type { Page, Promotion, PromotionPayload, PromotionStatus, PromotionType, PromotionUsage } from './types'

const BASE = `${API_V1_PREFIX}/promotions`

export interface PromotionListParams {
  q?: string
  status?: PromotionStatus | ''
  promo_type?: PromotionType | ''
  coupon_only?: boolean | null
  limit?: number
  offset?: number
}

export const listPromotions = (params: PromotionListParams) => apiFetch<Page<Promotion>>(BASE, { query: params as Query })
export const getPromotion = (id: number) => apiFetch<Promotion>(`${BASE}/${id}`)
export const createPromotion = (payload: PromotionPayload, options?: SendOptions) =>
  apiSend<Promotion>('POST', BASE, payload, options)
export const updatePromotion = (id: number, changes: PromotionPayload) => apiSend<Promotion>('PATCH', `${BASE}/${id}`, changes)
export const setPromotionState = (id: number, action: 'activate' | 'pause' | 'expire') =>
  apiSend<Promotion>('POST', `${BASE}/${id}/${action}`)
export const listPromotionUsage = (params: { promotion_id?: number; coupon_only?: boolean }) =>
  apiFetch<{ items: PromotionUsage[] }>(`${BASE}/usage`, { query: params as Query })
