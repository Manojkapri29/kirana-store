import { API_V1_PREFIX, apiFetch, apiSend } from './client'
import type { Category, Shop, Unit } from './types'

export const getShop = () => apiFetch<Shop>(`${API_V1_PREFIX}/shop`)
export const listUnits = () => apiFetch<Unit[]>(`${API_V1_PREFIX}/units`)
export const listCategories = () => apiFetch<Category[]>(`${API_V1_PREFIX}/categories`)
export const createCategory = (name: string) =>
  apiSend<Category>('POST', `${API_V1_PREFIX}/categories`, { name })
