import { API_V1_PREFIX, apiFetch, apiSend } from './client'

export interface AppNotification {
  id: number
  event_type: string
  category: string
  title: string
  message: string
  created_at: string
  read: boolean
  entity_type: string | null
  entity_id: number | null
}

export interface NotificationList {
  items: AppNotification[]
  total: number
  limit: number
  offset: number
  page: number
  page_size: number
  total_pages: number
  unread: number
}

export interface Preferences {
  /** category -> channel (lower case: in_app, email, sms, whatsapp, push) -> wanted */
  preferences: Record<string, Record<string, boolean>>
  /** channel (upper case) -> can it really deliver? Only in-app until a provider is configured. */
  channels: Record<string, boolean>
}

const BASE = `${API_V1_PREFIX}/notifications`

export const listNotifications = (query: { unread_only?: boolean; limit?: number; offset?: number } = {}) =>
  apiFetch<NotificationList>(BASE, { query })
export const getUnreadCount = () => apiFetch<{ unread: number }>(`${BASE}/unread-count`)
export const markRead = (id: number) => apiSend<{ unread: number }>('POST', `${BASE}/${id}/read`)
export const markAllRead = () => apiSend<{ unread: number }>('POST', `${BASE}/read-all`)
export const refreshAlerts = () => apiSend<{ unread: number }>('POST', `${BASE}/refresh-alerts`)
export const getPreferences = () => apiFetch<Preferences>(`${BASE}/preferences`)
export const setPreference = (category: string, channels: Record<string, boolean>) =>
  apiSend<Preferences>('PUT', `${BASE}/preferences/${category}`, { channels })
