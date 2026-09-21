import { API_V1_PREFIX, apiFetch, apiSend } from './client'

export interface Membership {
  user_id: number
  shop_id: number
  shop_name: string
  role_code: string
  role_name: string
  status: string
}

/** Who is signed in, and what they may do. `permissions` is for showing or hiding buttons ONLY: the server checks every call. */
export interface SessionInfo {
  account: { id: number; email: string; full_name: string }
  memberships: Membership[]
  active_user_id: number | null
  shop_id: number | null
  shop_name: string | null
  role_code: string | null
  role_name: string | null
  permissions: string[]
  expires_at: string
  idle_minutes: number
  csrf_token?: string | null
}

export interface InvitationPreview {
  shop_name: string
  email: string
  role_name: string
  has_account: boolean
  expires_at: string
}

const BASE = `${API_V1_PREFIX}/auth`

export const getSession = () => apiFetch<SessionInfo>(`${BASE}/me`)
export const login = (email: string, password: string) => apiSend<SessionInfo>('POST', `${BASE}/login`, { email, password })
export const logout = () => apiSend<void>('POST', `${BASE}/logout`)
export const selectShop = (userId: number) => apiSend<SessionInfo>('POST', `${BASE}/select-shop`, { user_id: userId })
export const changePassword = (current_password: string, new_password: string) =>
  apiSend<{ other_sessions_ended: number }>('POST', `${BASE}/change-password`, { current_password, new_password })
export const previewInvitation = (token: string) => apiSend<InvitationPreview>('POST', `${BASE}/invitations/preview`, { token })
export const acceptInvitation = (token: string, password: string, full_name?: string) =>
  apiSend<SessionInfo>('POST', `${BASE}/invitations/accept`, { token, password, full_name })
