import { API_V1_PREFIX, apiFetch, apiSend, type Query } from './client'
import type {
  Page,
  StatusFilter,
  Supplier,
  SupplierOption,
  SupplierPayload,
  SupplierSaved,
} from './types'

const BASE = `${API_V1_PREFIX}/suppliers`

export interface SupplierListParams {
  q?: string
  status?: StatusFilter
  limit?: number
  offset?: number
}

export const listSuppliers = (params: SupplierListParams) =>
  apiFetch<Page<Supplier>>(BASE, { query: params as Query })

/** Every active supplier (id and name), for pickers such as a product's default supplier. */
export const listSupplierOptions = () => apiFetch<SupplierOption[]>(`${BASE}/options`)

export const getSupplier = (id: number) => apiFetch<Supplier>(`${BASE}/${id}`)

export const createSupplier = (payload: SupplierPayload) => apiSend<SupplierSaved>('POST', BASE, payload)

export const updateSupplier = (id: number, changes: Partial<SupplierPayload>) =>
  apiSend<SupplierSaved>('PATCH', `${BASE}/${id}`, changes)

export const setSupplierActive = (id: number, active: boolean) =>
  apiSend<Supplier>('POST', `${BASE}/${id}/${active ? 'activate' : 'deactivate'}`)
