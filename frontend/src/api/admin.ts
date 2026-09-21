import { API_V1_PREFIX, apiFetch } from './client'

/**
 * The internal administrator console's calls. An administrator is not a shop user: they send their own token in
 * `X-Admin-Token`, which the browser keeps only for this tab (sessionStorage). The server decides what each role may do.
 */
const TOKEN_KEY = 'kirana.admin.token'

export function getAdminToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setAdminToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // Not remembering the token is acceptable: the administrator signs in again.
  }
}

const headers = () => ({ 'X-Admin-Token': getAdminToken() ?? '' })
const BASE = `${API_V1_PREFIX}/admin`

export interface AdminMe {
  email: string
  display_name: string
  role: string
  permissions: string[]
}

export interface AdminShop {
  id: number
  name: string
  account_status: string
  status_reason: string | null
  plan_code: string
  products: number
  users: number
}

export interface Paged<T> {
  items: T[]
  total: number
  page: number
  total_pages: number
}

export interface AdminEvent {
  id: number
  shop_id: number | null
  category: string
  severity: string
  source: string
  code: string
  message: string
  created_at: string
}

export interface AdminBackup {
  backup_key: string
  kind: string
  status: string
  size_bytes: number | null
  error_code: string | null
  error_message: string | null
  created_at: string
}

export const adminMe = () => apiFetch<AdminMe>(`${BASE}/me`, { headers: headers() })
export const adminHealth = () => apiFetch<Record<string, unknown>>(`${BASE}/system/health`, { headers: headers() })
export const adminOverview = () =>
  apiFetch<{ shops_by_status: Record<string, number>; events_last_24h: Record<string, number> }>(`${BASE}/system/overview`, { headers: headers() })
export const adminShops = () => apiFetch<Paged<AdminShop>>(`${BASE}/shops`, { headers: headers(), query: { limit: 50 } })
export const adminEvents = (category?: string) =>
  apiFetch<Paged<AdminEvent>>(`${BASE}/events`, { headers: headers(), query: { limit: 30, category } })
export const adminBackups = () => apiFetch<AdminBackup[]>(`${BASE}/backups`, { headers: headers() })
export const adminCreateBackup = () => apiFetch<AdminBackup>(`${BASE}/backups`, { method: 'POST', headers: headers() })
