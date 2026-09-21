import { API_V1_PREFIX, apiFetch, type Query } from './client'
import type { DiscountReport, SalesSummary } from './types'

const BASE = `${API_V1_PREFIX}/reports`

export const getSalesSummary = (params: { date_from?: string; date_to?: string }) =>
  apiFetch<SalesSummary>(`${BASE}/sales-summary`, { query: params as Query })

export const getDiscountReport = (params: { date_from?: string; date_to?: string }) =>
  apiFetch<DiscountReport>(`${BASE}/discounts`, { query: params as Query })
