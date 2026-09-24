import { API_V1_PREFIX, apiFetch, apiSend, type Query } from './client'
import type { Page } from './types'

const CRM = `${API_V1_PREFIX}/crm`
const LOYALTY = `${API_V1_PREFIX}/loyalty`
const CAMPAIGNS = `${API_V1_PREFIX}/campaigns`
const RULES = `${API_V1_PREFIX}/automation-rules`
const REFERRALS = `${API_V1_PREFIX}/referrals`

export type Channel = 'IN_APP' | 'EMAIL' | 'SMS' | 'WHATSAPP' | 'PUSH'
export const CHANNELS: readonly Channel[] = ['IN_APP', 'EMAIL', 'SMS', 'WHATSAPP', 'PUSH']
export type CampaignStatus = 'DRAFT' | 'SCHEDULED' | 'RUNNING' | 'PAUSED' | 'COMPLETED' | 'CANCELLED'
export type SendStatus = 'SENT' | 'NOT_CONFIGURED' | 'SKIPPED_NO_CONSENT' | 'SKIPPED_OPTED_OUT' | 'FAILED'

// --- Dashboard / retention -----------------------------------------------------------------------
export interface CrmDashboard {
  period_days: number
  customers: { total_customers: number; new_customers: number; active_customers: number; returning_customers: number; inactive_customers: number }
  revenue: { customer_revenue: string; average_transaction_value: string | null; repeat_customer_revenue: string }
  retention: {
    customers_with_purchases: number
    repeat_customers: number
    repeat_purchase_rate: string | null
    inactive_customer_count: number
    reactivated_count: number
    average_purchase_interval_days: string | null
    cohort_retention_rate: string
  }
  loyalty: { points_issued: number; points_redeemed: number; points_outstanding: number; program_active: boolean }
  campaigns: { total_campaigns: number; running: number; completed: number; draft: number }
  referrals: { total_referrals: number; successful_referrals: number; pending_referrals: number }
}
export const getCrmDashboard = (periodDays: number) => apiFetch<CrmDashboard>(`${CRM}/dashboard`, { query: { period_days: periodDays } })

export interface ReactivationRow {
  customer_id: number
  name: string
  days_since_last_purchase: number | null
  verdict: 'ELIGIBLE' | 'NO_CONSENT' | 'IN_COOLDOWN'
  reason: string
}
export interface ReactivationPreview {
  inactive_days: number
  cooldown_days: number
  channel: Channel
  eligible_count: number
  excluded_count: number
  rows: ReactivationRow[]
}
export interface ReactivationParams { inactive_days: number; cooldown_days: number; channel: Channel }
export const previewReactivation = (p: ReactivationParams) =>
  apiFetch<ReactivationPreview>(`${CRM}/reactivation/preview`, { query: p as unknown as Query })
export const createReactivationDraft = (p: ReactivationParams & { name: string; message_template: string }) =>
  apiSend<Campaign>('POST', `${CRM}/reactivation/draft`, p)

// --- Customer profile ----------------------------------------------------------------------------
export interface CrmProfile {
  customer_id: number
  name: string
  phone: string | null
  email: string | null
  customer_type: 'RETAIL' | 'WHOLESALE' | 'OTHER' | null
  source: string | null
  tags: string[]
  is_active: boolean
  preferred_contact_channel: Channel | null
  marketing_opt_in_email: boolean
  marketing_opt_in_sms: boolean
  marketing_opt_in_whatsapp: boolean
  marketing_opt_in_push: boolean
  analytics: { total_purchases: string; outstanding: string; days_since_last_purchase: number | null; segments: string[] }
  loyalty_balance: number
  loyalty_program_active: boolean
  referrals_made: number
  referral_code: string | null
}
export type ClassificationChanges = Partial<
  Pick<CrmProfile, 'customer_type' | 'source' | 'tags' | 'preferred_contact_channel' | 'marketing_opt_in_email' | 'marketing_opt_in_sms' | 'marketing_opt_in_whatsapp' | 'marketing_opt_in_push'>
>
export const getCrmProfile = (id: number) => apiFetch<CrmProfile>(`${CRM}/customers/${id}/profile`)
export const updateClassification = (id: number, changes: ClassificationChanges) =>
  apiSend<CrmProfile>('PATCH', `${CRM}/customers/${id}/classification`, changes)

export interface TimelineEvent {
  kind: string
  occurred_at: string
  title: string
  detail: string
  amount: string | null
  reference_type: string | null
  reference_id: number | null
}
export const getTimeline = (id: number) => apiFetch<TimelineEvent[]>(`${CRM}/customers/${id}/timeline`)

export interface Note { id: number; customer_id: number; user_id: number; body: string; created_at: string }
export const listNotes = (id: number) => apiFetch<Note[]>(`${CRM}/customers/${id}/notes`)
export const addNote = (id: number, body: string) => apiSend<Note>('POST', `${CRM}/customers/${id}/notes`, { body })

// --- Groups --------------------------------------------------------------------------------------
export interface CustomerGroup { id: number; name: string; kind: 'MANUAL' | 'RULE_BASED'; member_count: number | null }
export const listGroups = () => apiFetch<{ items: CustomerGroup[] }>(`${CRM}/groups`)

// --- Loyalty -------------------------------------------------------------------------------------
export interface LoyaltyProgram {
  id: number
  is_active: boolean
  points_per_amount: string
  min_transaction_amount: string
  redemption_value: string
  min_redemption_points: number | null
  max_redeem_points_per_txn: number | null
  points_expiry_days: number | null
}
export type LoyaltyProgramPayload = Omit<LoyaltyProgram, 'id'>
export interface LoyaltyLedgerEntry {
  id: number
  entry_type: 'EARN' | 'REDEEM' | 'ADJUST' | 'EXPIRE' | 'REVERSAL'
  points_delta: number
  note: string | null
  entry_date: string
}
export const getLoyaltyProgram = () => apiFetch<LoyaltyProgram | null>(`${LOYALTY}/program`)
export const saveLoyaltyProgram = (p: LoyaltyProgramPayload) => apiSend<LoyaltyProgram>('PUT', `${LOYALTY}/program`, p)
export const getLoyaltySummary = () => apiFetch<{ points_issued: number; points_redeemed: number; points_outstanding: number }>(`${LOYALTY}/summary`)
export const getLoyaltyLedger = (id: number) => apiFetch<{ items: LoyaltyLedgerEntry[]; total: number; balance: number }>(`${LOYALTY}/customers/${id}/ledger`)
export const expireLoyaltyPoints = () => apiSend<{ customers_expired: number; points_expired: number }>('POST', `${LOYALTY}/expire`)
export type AdjustResult = LoyaltyLedgerEntry | { status: 'APPROVAL_REQUIRED'; approval_request_id: number }
export const adjustLoyalty = (id: number, body: { points_delta: number; note: string; approval_request_id?: number | null }) =>
  apiSend<AdjustResult>('POST', `${LOYALTY}/customers/${id}/adjust`, body)

// --- Campaigns -----------------------------------------------------------------------------------
export interface Campaign {
  id: number
  name: string
  description: string | null
  status: CampaignStatus
  channel: Channel
  target_group_id: number | null
  target_segment: string | null
  promotion_id: number | null
  message_template: string
  launched_at: string | null
  cancel_reason: string | null
  requires_approval: boolean
  created_at: string
}
export interface CampaignPayload {
  name: string
  description?: string | null
  channel: Channel
  target_group_id?: number | null
  target_segment?: string | null
  message_template: string
}
export interface SendOutcome { id: number; customer_id: number; channel: Channel; status: SendStatus; detail: string | null }
export const listCampaigns = (params: { status?: CampaignStatus | ''; limit?: number; offset?: number }) =>
  apiFetch<Page<Campaign>>(CAMPAIGNS, { query: params as Query })
export const getCampaign = (id: number) => apiFetch<Campaign>(`${CAMPAIGNS}/${id}`)
export const createCampaign = (p: CampaignPayload) => apiSend<Campaign>('POST', CAMPAIGNS, p)
export const getCampaignAudience = (id: number) => apiFetch<{ customer_ids: number[]; audience_size: number }>(`${CAMPAIGNS}/${id}/audience`)
export const getCampaignSends = (id: number) => apiFetch<{ items: SendOutcome[] }>(`${CAMPAIGNS}/${id}/sends`)
export const campaignAction = (id: number, action: 'launch' | 'pause' | 'resume') => apiSend<Campaign>('POST', `${CAMPAIGNS}/${id}/${action}`)
export const cancelCampaign = (id: number, reason: string) => apiSend<Campaign>('POST', `${CAMPAIGNS}/${id}/cancel`, { reason })

// --- Automation ----------------------------------------------------------------------------------
export type Trigger = 'NEW_CUSTOMER' | 'INACTIVITY' | 'LOYALTY_MILESTONE' | 'PURCHASE_MILESTONE'
export type RuleAction = 'CREATE_CAMPAIGN_DRAFT' | 'CREATE_TASK' | 'NOTIFY'
export interface AutomationRule {
  id: number
  name: string
  trigger_type: Trigger
  conditions: Record<string, unknown>
  action_type: RuleAction
  action_config: Record<string, unknown>
  cooldown_days: number
  is_active: boolean
}
export interface RuleRunResult { run_status: string; customers_matched: number; customers_actioned: number; detail: string }
export const listRules = () => apiFetch<{ items: AutomationRule[] }>(RULES)
export const createRule = (p: Omit<AutomationRule, 'id' | 'is_active'>) => apiSend<AutomationRule>('POST', RULES, p)
export const setRuleActive = (id: number, active: boolean) => apiSend<AutomationRule>('POST', `${RULES}/${id}/${active ? 'activate' : 'deactivate'}`)
export const runRule = (id: number) => apiSend<RuleRunResult>('POST', `${RULES}/${id}/run`)

// --- Referrals and approval settings ---------------------------------------------------------------
export interface ReferralProgram {
  id: number
  is_active: boolean
  referrer_reward_points: number | null
  referred_reward_points: number | null
  min_purchase_amount: string | null
  max_referrals_per_customer: number | null
  expiry_days: number | null
}
export type ReferralProgramPayload = Omit<ReferralProgram, 'id'>
export const getReferralProgram = () => apiFetch<ReferralProgram | null>(`${REFERRALS}/program`)
export const saveReferralProgram = (p: ReferralProgramPayload) => apiSend<ReferralProgram>('PUT', `${REFERRALS}/program`, p)

export interface ApprovalSettings { campaign_audience_threshold: number | null; loyalty_adjustment_threshold: number | null }
export const getApprovalSettings = () => apiFetch<ApprovalSettings>(`${CRM}/approval-settings`)
export const saveApprovalSettings = (s: ApprovalSettings) => apiSend<ApprovalSettings>('PUT', `${CRM}/approval-settings`, s)
