import { API_V1_PREFIX, apiFetch, apiSend } from './client'

const I = `${API_V1_PREFIX}/integrations`

export type IntegrationType = 'PAYMENT' | 'EMAIL' | 'SMS' | 'WHATSAPP' | 'PUSH' | 'ACCOUNTING'
export type IntegrationStatus = 'NOT_CONFIGURED' | 'CONFIGURED' | 'DISABLED' | 'ERROR'

export interface ProviderInfo {
  provider: string
  label: string
  external: boolean
  webhook: boolean
  needs_credentials: boolean
  settings: string[]
  required: string[]
  note: string
}
export interface Integration {
  integration_type: IntegrationType
  provider: string | null
  provider_label: string | null
  available_providers: ProviderInfo[]
  is_enabled: boolean
  status: IntegrationStatus
  message: string
  config: Record<string, string | number>
  /** The NAME of an environment variable. The value is never sent to the browser. */
  credential_ref: string | null
  credentials_present: boolean
  webhook_path: string | null
  webhook_secret_present: boolean
  webhook_credential_ref: string | null
  last_success_at: string | null
  last_failure_at: string | null
  failure_count: number
  last_error_code: string | null
  rotated_at: string | null
  external: boolean
}
export interface PlatformEntry { integration_type: string; provider: string | null; status: string; message: string; external: boolean; note: string }
export interface Dashboard {
  integrations: Integration[]
  platform: PlatformEntry[]
  summary: { configured: number; errors: number; not_configured: number; disabled: number }
}
export interface CallLog { id: number; integration_type: string; provider: string; operation: string; outcome: 'SUCCESS' | 'FAILURE'; error_code: string | null; duration_ms: number; created_at: string }
export interface OnlinePayment {
  id: number; provider: string; method: string; purpose: string; status: string; amount: string; refunded_amount: string
  needs_review: boolean; review_reason: string | null; failure_code: string | null; created_at: string
}
export interface ConfigurePayload { provider: string; config: Record<string, string | number>; credential_ref?: string | null; webhook_credential_ref?: string | null; is_enabled?: boolean }

export const getDashboard = () => apiFetch<Dashboard>(`${I}/dashboard`)
export const getLogs = () => apiFetch<{ items: CallLog[] }>(`${I}/logs`, { query: { limit: 20 } })
export const getReviewPayments = () => apiFetch<{ items: OnlinePayment[]; total: number }>(`${API_V1_PREFIX}/payments`, { query: { needs_review: true, limit: 20 } })
export const configure = (t: IntegrationType, p: ConfigurePayload) => apiSend<Integration>('PUT', `${I}/${t}`, p)
export const setEnabled = (t: IntegrationType, enabled: boolean) => apiSend<Integration>('POST', `${I}/${t}/${enabled ? 'enable' : 'disable'}`)
export const testConnection = (t: IntegrationType) => apiSend<{ ok: boolean; code: string | null; message: string }>('POST', `${I}/${t}/test`)
