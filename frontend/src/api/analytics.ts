import { API_V1_PREFIX, apiDownload, apiFetch, apiSend, type Query } from './client'

const A = `${API_V1_PREFIX}/analytics`

export const PRESETS = [
  'today', 'yesterday', 'this_week', 'last_week', 'this_month', 'last_month', 'this_quarter', 'last_quarter', 'this_year', 'last_year', 'custom',
] as const
export type Preset = (typeof PRESETS)[number]
export type CompareMode = 'previous_period' | 'previous_year' | 'none'
export type Availability = 'AVAILABLE' | 'NOT_AVAILABLE' | 'INSUFFICIENT_DATA'
export type ColumnKind = 'text' | 'integer' | 'money' | 'quantity' | 'percent' | 'date' | 'decimal'

/** What every analytics screen sends. The server resolves presets to dates and comparison periods; nothing here is SQL. */
export interface ReportQuery {
  preset: Preset
  date_from?: string
  date_to?: string
  compare: CompareMode
  limit?: number
  offset?: number
  category_id?: number
  product_id?: number
  supplier_id?: number
  customer_id?: number
  brand?: string
  payment_method?: string
  channel?: 'ALL' | 'DETAILED' | 'QUICK' | 'ONLINE'
}
export const DEFAULT_QUERY: ReportQuery = { preset: 'this_month', compare: 'previous_period' }

const q = (r: ReportQuery, extra: Query = {}): Query => ({ ...(r as unknown as Query), ...extra })

export interface Period { start: string; end: string; label: string }
export interface Value { amount: string | null; availability: Availability; reason: string | null }
export interface KpiDefinition {
  key: string; name: string; group: string; description: string; formula: string; source: string; unit: 'money' | 'count' | 'percent'
  limitations: string; permission: string; as_of_only: boolean
}
export interface KpiResult {
  definition: KpiDefinition
  period: Period
  comparison_period: Period | null
  current: Value
  previous: Value | null
  change: { absolute: string | null; percent: string | null; note: string | null } | null
}
export interface KpiReport { period: Period; comparison: Period | null; kpis: KpiResult[]; hidden: string[]; notes: string[] }
export interface ExecutiveDashboard {
  period: Period
  comparison: Period | null
  sections: { key: string; title: string; kpis: KpiResult[]; hidden_kpis: string[]; trend: { label: string; value: string }[] | null; trend_label: string | null }[]
  notes: string[]
}
export interface ReportTable {
  columns: [string, string, ColumnKind][]
  rows: Record<string, unknown>[]
  total: number
  limit: number
  offset: number
  title: string
  period: Period | null
  comparison: Period | null
  notes: string[]
  filters_applied: string[]
  filters_ignored: string[]
  source: string
}
export interface Insight {
  key: string; title: string; availability: Availability; statement: string; evidence: Record<string, unknown>[]; sources: string[]; limitations: string[]
}
export interface Insights { period: Period; comparison: Period | null; insights: Insight[]; hidden: string[]; caution: string }

export const getKpis = (r: ReportQuery) => apiFetch<KpiReport>(`${A}/kpis`, { query: q(r) })
export const getExecutive = (r: ReportQuery) => apiFetch<ExecutiveDashboard>(`${A}/executive`, { query: q(r) })
export const getInsights = (r: ReportQuery) => apiFetch<Insights>(`${A}/insights`, { query: q(r) })

/** The standard tables, by the name of their endpoint under /analytics. */
export const REPORTS = [
  { group: 'sales', key: 'sales/trend', permission: 'REPORT_VIEW' },
  { group: 'sales', key: 'sales/products', permission: 'REPORT_VIEW' },
  { group: 'sales', key: 'sales/categories', permission: 'REPORT_VIEW' },
  { group: 'sales', key: 'sales/channels', permission: 'REPORT_VIEW' },
  { group: 'sales', key: 'sales/payment-methods', permission: 'REPORT_VIEW' },
  { group: 'inventory', key: 'inventory/stock', permission: 'INVENTORY_VIEW' },
  { group: 'inventory', key: 'inventory/turnover', permission: 'INVENTORY_VIEW' },
  { group: 'inventory', key: 'inventory/reorder', permission: 'INVENTORY_VIEW' },
  { group: 'customers', key: 'customers/list', permission: 'CRM_ANALYTICS_VIEW' },
  { group: 'customers', key: 'customers/segments', permission: 'CRM_ANALYTICS_VIEW' },
  { group: 'customers', key: 'cohorts', permission: 'CRM_ANALYTICS_VIEW' },
  { group: 'suppliers', key: 'suppliers/spend', permission: 'SUPPLIER_VIEW' },
  { group: 'suppliers', key: 'suppliers/products', permission: 'SUPPLIER_VIEW' },
  { group: 'finance', key: 'finance/trend', permission: 'FINANCE_VIEW' },
  { group: 'finance', key: 'finance/expenses', permission: 'FINANCE_VIEW' },
] as const
export type ReportPath = (typeof REPORTS)[number]['key']

/** The export key of each report above (see `analytics_export_service`): the same function as the screen. */
export const EXPORT_KEYS: Record<ReportPath, string> = {
  'sales/trend': 'sales-trend-month', 'sales/products': 'sales-products-top', 'sales/categories': 'sales-categories', 'sales/channels': 'sales-channels',
  'sales/payment-methods': 'sales-payment-methods', 'inventory/stock': 'inventory-stock', 'inventory/turnover': 'inventory-turnover',
  'inventory/reorder': 'inventory-reorder', 'customers/list': 'customers-list', 'customers/segments': 'customers-segments', cohorts: 'cohorts',
  'suppliers/spend': 'suppliers-spend', 'suppliers/products': 'suppliers-products', 'finance/trend': 'finance-trend-month', 'finance/expenses': 'finance-expenses',
}
export const getReport = (path: ReportPath, r: ReportQuery) =>
  apiFetch<ReportTable>(`${A}/${path}`, { query: q(r, path === 'sales/trend' || path === 'finance/trend' ? { bucket: 'month' } : {}) })

export type ExportFormat = 'csv' | 'xlsx' | 'pdf'
export async function downloadAnalyticsExport(key: string, format: ExportFormat, r: ReportQuery, extra: Query = {}): Promise<void> {
  const { blob, filename } = await apiDownload(`${A}/export/${key}`, { ...q({ ...r, limit: undefined, offset: undefined }, extra), format })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

// --- Drill-down -----------------------------------------------------------------------------------------
export type DrillPath = 'revenue' | 'inventory' | 'customers' | 'expenses' | 'suppliers' | 'finance'
export const DRILL_START: Record<DrillPath, string> = {
  revenue: 'months', inventory: 'categories', customers: 'segments', expenses: 'categories', suppliers: 'suppliers', finance: 'kpis',
}
export const DRILL_PERMISSION: Record<DrillPath, string> = {
  revenue: 'REPORT_VIEW', inventory: 'INVENTORY_VIEW', customers: 'CRM_ANALYTICS_VIEW', expenses: 'FINANCE_EXPENSE_VIEW', suppliers: 'SUPPLIER_VIEW', finance: 'FINANCE_VIEW',
}
export type Drill = { path: DrillPath; level: string } & Record<string, string | number>
export const getDrill = (d: Drill, r: ReportQuery) => {
  const { path, level, ...params } = d
  return apiFetch<ReportTable>(`${A}/drill/${path}/${level}`, { query: q(r, params as Query) })
}

// --- Custom report builder --------------------------------------------------------------------------------
export interface BuilderField { key: string; label: string; kind: ColumnKind; operators: string[]; aggregations: string[] }
export interface BuilderDataset { key: string; label: string; available: boolean; reason: string | null; source: string; notes: string[]; period_applies: boolean; fields: BuilderField[] }
export interface Definition {
  columns: string[]
  group_by: string[]
  aggregations: { field: string; op: string; label?: string }[]
  filters: { field: string; op: string; value: string | string[] }[]
  sort: { field: string; direction: 'asc' | 'desc' }[]
}
export const EMPTY_DEFINITION: Definition = { columns: [], group_by: [], aggregations: [], filters: [], sort: [] }
export interface SavedReport {
  id: number; name: string; description: string | null; dataset: string; definition: Definition; is_archived: boolean
  created_by: number; updated_by: number | null; created_at: string; updated_at: string
}
export interface SavedReportPayload { name: string; description?: string | null; dataset: string; definition: Definition }
export const getBuilderDatasets = () => apiFetch<{ datasets: BuilderDataset[]; max_rows: number }>(`${A}/builder/datasets`)
export const previewReport = (dataset: string, definition: Definition, r: ReportQuery) =>
  apiFetch<ReportTable>(`${A}/builder/preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dataset, definition }), query: q(r),
  })
export const listSavedReports = (includeArchived = false) => apiFetch<{ items: SavedReport[] }>(`${A}/reports`, { query: { include_archived: includeArchived } })
export const createSavedReport = (p: SavedReportPayload) => apiSend<SavedReport>('POST', `${A}/reports`, p)
export const updateSavedReport = (id: number, p: SavedReportPayload) => apiSend<SavedReport>('PUT', `${A}/reports/${id}`, p)
export const archiveSavedReport = (id: number, archived: boolean) => apiSend<SavedReport>('POST', `${A}/reports/${id}/${archived ? 'archive' : 'restore'}`)
export const runSavedReport = (id: number, r: ReportQuery) => apiFetch<ReportTable>(`${A}/reports/${id}/run`, { query: q(r) })

// --- Scheduled advanced reports -----------------------------------------------------------------------------
const S = `${API_V1_PREFIX}/scheduled-reports`
export const ADVANCED_KINDS = ['adv_kpis', 'adv_executive', 'adv_sales', 'adv_inventory', 'adv_customers', 'adv_cohorts', 'adv_suppliers', 'adv_finance', 'saved_report'] as const
export type AdvancedKind = (typeof ADVANCED_KINDS)[number]
export type Schedule = 'DAILY' | 'WEEKLY' | 'MONTHLY'
export interface ScheduledReport {
  id: number; report_type: string; schedule: Schedule; is_active: boolean; next_run_at: string; last_run_at: string | null; last_status: string | null
  last_error: string | null; params: Record<string, unknown> | null; export_format: string | null; delivery_channel: string | null; recipients: string[] | null
}
export interface ReportRun {
  id: number; run_key: string; period_start: string; period_end: string; status: 'OK' | 'FAILED'
  delivery_status: 'STORED_IN_APP' | 'NOT_CONFIGURED' | 'NOT_DELIVERED'; error: string | null; summary: Record<string, unknown> | null; generated_at: string
}
export interface AdvancedSchedulePayload {
  kind: AdvancedKind; schedule: Schedule; saved_report_id?: number | null; export_format?: string | null; delivery_channel?: string | null; recipients?: string[]
}
export const listSchedules = () => apiFetch<{ items: ScheduledReport[] }>(S)
export const createAdvancedSchedule = (p: AdvancedSchedulePayload) => apiSend<ScheduledReport>('POST', `${S}/advanced`, p)
export const runScheduleNow = (id: number) => apiSend<ScheduledReport>('POST', `${S}/${id}/run-now`)
export const setScheduleActive = (id: number, active: boolean) => apiSend<ScheduledReport>('POST', `${S}/${id}/${active ? 'reactivate' : 'deactivate'}`)
export const listRuns = (id: number) => apiFetch<ReportRun[]>(`${S}/${id}/runs`)
