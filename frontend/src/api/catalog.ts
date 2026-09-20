import { API_V1_PREFIX, apiFetch, apiSend } from './client'
import type { BusinessType, Category, Shop, ShopTemplate, Unit } from './types'

export const getShop = () => apiFetch<Shop>(`${API_V1_PREFIX}/shop`)
export const listUnits = () => apiFetch<Unit[]>(`${API_V1_PREFIX}/units`)
export const listCategories = () => apiFetch<Category[]>(`${API_V1_PREFIX}/categories`)
export const createCategory = (name: string) =>
  apiSend<Category>('POST', `${API_V1_PREFIX}/categories`, { name })

export const listBusinessTypes = () => apiFetch<BusinessType[]>(`${API_V1_PREFIX}/business-types`)
export const getShopTemplate = () => apiFetch<ShopTemplate>(`${API_V1_PREFIX}/shop/template`)
export const updateShopBusinessType = (businessType: string) =>
  apiSend<Shop>('PATCH', `${API_V1_PREFIX}/shop`, { business_type: businessType })
