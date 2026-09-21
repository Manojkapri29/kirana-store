import { API_V1_PREFIX, apiFetch, apiSend } from './client'

const BASE = `${API_V1_PREFIX}/ai`

export type AnswerStatus = 'ANSWERED' | 'NO_DATA' | 'NOT_AVAILABLE' | 'NOT_UNDERSTOOD' | 'NOT_CONFIGURED' | 'REFUSED'

export interface Figure {
  label: string
  value: string
  note: string | null
}

export interface AnswerTable {
  columns: string[]
  rows: string[][]
}

export type ActionKind = 'PURCHASE_DRAFT' | 'STOCK_ADJUSTMENT' | 'PROMOTION_DRAFT'
export type ActionStatus = 'PROPOSED' | 'EXECUTED' | 'CANCELLED' | 'FAILED'

/** An action the assistant can prepare. Showing it changes nothing; the person chooses to create the draft. */
export interface Proposal {
  kind: ActionKind
  feature: string
  label: string
  payload: Record<string, unknown>
}

export interface Answer {
  status: AnswerStatus
  tool: string | null
  title: string
  message: string
  figures: Figure[]
  table: AnswerTable | null
  sources: string[]
  period: { from: string; to: string; label: string } | null
  notes: string[]
  badges: string[]
  proposals: Proposal[]
  /** For the existing export buttons: {kind, filters}. */
  export: { kind: string; filters: Record<string, string> } | null
  follow_ups: string[]
  provider_used: boolean
}

export interface AiStatus {
  configured: boolean
  provider_label: string | null
  documents: boolean
  features: Record<string, boolean | null>
  usage: {
    period: string
    requests: number
    limit: number | null
    failed: number
    input_tokens: number
    output_tokens: number
    by_feature: Record<string, number>
  } | null
  suggestions: { label: string; question: string }[]
}

export interface ActionPreview {
  shop: string
  title: string
  summary?: string
  supplier?: string
  lines?: { columns: string[]; rows: string[][] }
  totals: { label: string; value: string }[]
  impact: string[]
  problems: string[]
  warnings: string[]
  can_confirm: boolean
}

export interface AiAction {
  id: number
  kind: ActionKind
  status: ActionStatus
  feature: string
  created_at: string
  decided_at: string | null
  attempts: number
  current: Record<string, unknown>
  preview: ActionPreview
  result_type: string | null
  result_ids: number[] | null
  failure_message: string | null
  reference_id: string | null
}

export type DocumentKind = 'invoice' | 'stock_list'

export interface DocumentRow {
  name: string | null
  brand: string | null
  barcode: string | null
  sku: string | null
  quantity: string | null
  unit: string | null
  unit_price: string | null
  discount: string | null
  line_total: string | null
  product_id?: number | null
}

export interface Extraction {
  status: 'OK' | 'NOT_CONFIGURED' | 'FAILED'
  message: string | null
  kind: DocumentKind
  header: Record<string, string | null>
  rows: DocumentRow[]
  warnings: string[]
  provider_label: string | null
  stored: boolean
}

export interface Candidate {
  product_id: number
  name: string
  sku: string
  brand: string | null
  unit_code: string
  strength: string
  reasons: string[]
}

export type MatchStatus = 'MATCHED' | 'POSSIBLE_MATCH' | 'NEW_PRODUCT_CANDIDATE'

export interface MatchedRow extends DocumentRow {
  index: number
  status: MatchStatus
  product_id: number | null
  suggested_product_id: number | null
  unit_code: string | null
  candidates: Candidate[]
  problems: string[]
  warnings: string[]
  system_quantity: string | null
  difference: string | null
}

export interface MatchResult {
  kind: DocumentKind
  header: Record<string, unknown>
  rows: MatchedRow[]
  counts: Record<string, number>
  can_propose: boolean
  problems: string[]
  changes_data: boolean
}

export const getAiStatus = (language: string) => apiFetch<AiStatus>(`${BASE}/status`, { query: { language } })

export const askAssistant = (question: string, language: string) =>
  apiSend<Answer>('POST', `${BASE}/ask`, { question, language }, { timeoutMs: 60_000 })

export const runAiTool = (name: string, language: string, args: Record<string, unknown> = {}) =>
  apiSend<Answer>('POST', `${BASE}/tools/${name}`, { args, language }, { timeoutMs: 60_000 })

export const extractDocument = (kind: DocumentKind, image_base64: string, content_type: string | null) =>
  apiSend<Extraction>('POST', `${BASE}/documents/extract`, { kind, image_base64, content_type }, { timeoutMs: 90_000 })

export const matchDocument = (kind: DocumentKind, header: Record<string, unknown>, rows: DocumentRow[]) =>
  apiSend<MatchResult>('POST', `${BASE}/documents/match`, { kind, header, rows })

export const proposeAction = (kind: ActionKind, feature: string, payload: Record<string, unknown>) =>
  apiSend<AiAction>('POST', `${BASE}/actions`, { kind, feature, payload })

export const getAction = (id: number) => apiFetch<AiAction>(`${BASE}/actions/${id}`)
export const editAction = (id: number, payload: Record<string, unknown>) =>
  apiSend<AiAction>('PATCH', `${BASE}/actions/${id}`, { payload })
export const refreshActionStock = (id: number) => apiSend<AiAction>('POST', `${BASE}/actions/${id}/refresh-stock`)
export const confirmAction = (id: number) => apiSend<AiAction>('POST', `${BASE}/actions/${id}/confirm`)
export const cancelAction = (id: number) => apiSend<AiAction>('POST', `${BASE}/actions/${id}/cancel`)
