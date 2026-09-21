import { API_V1_PREFIX, apiFetch, apiSend } from './client'

export interface RoleRef {
  id: number
  code: string
  name: string
  is_system: boolean
}

export interface StaffMember {
  id: number
  name: string
  email: string
  role: RoleRef | null
  status: 'INVITED' | 'ACTIVE' | 'SUSPENDED' | 'REMOVED'
  joined_at: string | null
  last_active_at: string | null
  permission_count: number
  is_you: boolean
  permissions: string[] | null
}

export interface Invitation {
  id: number
  email: string
  role: string
  status: 'PENDING' | 'ACCEPTED' | 'REVOKED' | 'EXPIRED'
  expires_at: string
  created_at: string
}

export interface RoleView {
  id: number
  code: string
  name: string
  description: string | null
  is_system: boolean
  is_active: boolean
  permissions: string[]
  members: number
}

export interface PermissionInfo {
  code: string
  group: string
  description: string
  owner_only: boolean
}

const STAFF = `${API_V1_PREFIX}/staff`
const ROLES = `${API_V1_PREFIX}/roles`

export const listStaff = (includeRemoved = false) =>
  apiFetch<{ items: StaffMember[]; total: number }>(STAFF, { query: { include_removed: includeRemoved } })
export const getStaffMember = (id: number) => apiFetch<StaffMember>(`${STAFF}/${id}`)
export const listInvitations = () => apiFetch<{ items: Invitation[] }>(`${STAFF}/invitations`)
export const invite = (email: string, role_id: number) =>
  apiSend<{ invitation: Invitation; link: string; delivery: string }>('POST', `${STAFF}/invitations`, { email, role_id })
export const revokeInvitation = (id: number) => apiSend<Invitation>('POST', `${STAFF}/invitations/${id}/revoke`)
export const changeRole = (id: number, role_id: number) => apiSend<StaffMember>('PATCH', `${STAFF}/${id}`, { role_id })
export const staffAction = (id: number, action: 'suspend' | 'reactivate' | 'remove') => apiSend<StaffMember>('POST', `${STAFF}/${id}/${action}`)

export const listRoles = () => apiFetch<{ items: RoleView[] }>(ROLES)
export const listPermissions = () => apiFetch<PermissionInfo[]>(`${ROLES}/permissions`)
export const createRole = (body: { name: string; description?: string; permissions: string[] }) => apiSend<RoleView>('POST', ROLES, body)
export const updateRole = (id: number, body: { name?: string; description?: string; permissions?: string[] }) =>
  apiSend<RoleView>('PATCH', `${ROLES}/${id}`, body)
export const setRoleActive = (id: number, active: boolean) => apiSend<RoleView>('POST', `${ROLES}/${id}/${active ? 'reactivate' : 'deactivate'}`)
