import { API_V1_PREFIX, apiFetch, apiSend, type Query } from './client'
import type { Page } from './types'

const F = `${API_V1_PREFIX}/finance`

export type Money = string
export type PaymentMethod = 'CASH' | 'UPI' | 'CARD' | 'BANK_TRANSFER' | 'OTHER'
export const PAYMENT_METHODS: readonly PaymentMethod[] = ['CASH', 'UPI', 'CARD', 'BANK_TRANSFER', 'OTHER']
export type ExpenseStatus = 'DRAFT' | 'SUBMITTED' | 'APPROVED' | 'POSTED' | 'REJECTED' | 'VOIDED'
export type PeriodStatus = 'OPEN' | 'LOCKED' | 'CLOSED'
export type ReconStatus = 'MATCHED' | 'UNMATCHED' | 'PARTIAL' | 'REVIEW_REQUIRED'
export type EventType =
  | 'SALE' | 'PURCHASE' | 'SALE_RETURN' | 'PURCHASE_RETURN' | 'CUSTOMER_PAYMENT' | 'SUPPLIER_PAYMENT'
  | 'EXPENSE' | 'OWNER_CAPITAL' | 'OWNER_WITHDRAWAL' | 'ADJUSTMENT' | 'OTHER_INCOME'
export const MANUAL_EVENTS: readonly EventType[] = ['SUPPLIER_PAYMENT', 'OWNER_CAPITAL', 'OWNER_WITHDRAWAL', 'OTHER_INCOME']

export interface Change { current: Money | null; previous: Money | null; change_pct: string | null }
export interface Flow { inflow: Money; outflow: Money }
export interface Aging { [bucket: string]: Money }

export interface ProfitAndLoss {
  date_from: string
  date_to: string
  detailed_sales: Money
  quick_sales: Money
  online_sales: Money
  sales_returns: Money
  revenue: Money
  cogs: Money | null
  gross_profit: Money | null
  operating_expenses: Money
  net_profit: Money | null
  gross_margin_pct: string | null
  status: 'ACTUAL' | 'NOT_AVAILABLE'
  status_note: string
  costed_sales: { status: string; revenue: Money | null; cogs: Money | null; gross_profit: Money | null; coverage_pct: string | null }
  other_income: Money
  detailed_sales_without_cost: number
  returns_without_cost: number
  quick_sales_have_no_cost: boolean
  notes: string[]
}
export interface TrendPoint { period_start: string; period_end: string; revenue: Money; cogs: Money | null; gross_profit: Money | null; operating_expenses: Money; net_profit: Money | null }
export interface FlowPoint { period_start: string; period_end: string; inflow: Money; outflow: Money; net: Money }
export interface CashFlowReport {
  date_from: string
  date_to: string
  inflow: Money
  outflow: Money
  net: Money
  by_class: Record<string, Flow>
  by_event: Record<string, Flow>
  by_method: Record<string, Flow>
  unclassified_amount: Money
  basis: string
  notes: string[]
}
export interface FinanceAlert { code: string; severity: 'INFO' | 'REVIEW'; title: string; message: string; figures: Record<string, string> }

export interface Dashboard {
  date_from: string
  date_to: string
  comparison_from: string | null
  comparison_to: string | null
  pnl: ProfitAndLoss
  cash_flow: CashFlowReport
  receivables_total: Money
  payables_total: Money
  customer_outstanding: Money
  supplier_outstanding: Money
  receivables_aging: Aging
  payables_aging: Aging
  tax_status: string
  tax_collected: Money | null
  tax_paid: Money | null
  net_tax: Money | null
  revenue_change: Change | null
  gross_profit_change: Change | null
  net_profit_change: Change | null
  expenses_change: Change | null
  net_cash_flow_change: Change | null
  revenue_trend: TrendPoint[]
  cash_flow_trend: FlowPoint[]
  expense_categories: { category_id: number; name: string; amount: Money }[]
  payment_method_mix: { payment_method: string; inflow: Money; outflow: Money }[]
  alert_count: number
  granularity: string
  notes: string[]
}
export interface Range { date_from?: string; date_to?: string }
export const getDashboard = (r: Range & { compare?: boolean }) => apiFetch<Dashboard>(`${F}/dashboard`, { query: r as Query })
export const getAlerts = () => apiFetch<FinanceAlert[]>(`${F}/alerts`)
export const notifyAlerts = () => apiSend<{ alerts: number }>('POST', `${F}/alerts/notify`)
export const getPnl = (r: Range) => apiFetch<ProfitAndLoss>(`${F}/pnl`, { query: r as Query })
export const getCashFlow = (r: Range) => apiFetch<CashFlowReport>(`${F}/cash-flow`, { query: r as Query })

// --- Expenses ---------------------------------------------------------------------------------------
export interface ExpenseCategory { id: number; name: string; is_active: boolean; cash_flow_class: string | null }
export interface Expense {
  id: number
  expense_no: string
  expense_date: string
  category_id: number
  amount: Money
  payment_method: PaymentMethod
  payee: string | null
  description: string | null
  status: ExpenseStatus
  requires_approval: boolean
  rejection_reason: string | null
  void_reason: string | null
}
export interface ExpensePayload { expense_date: string; category_id: number; amount: Money; payment_method: PaymentMethod; payee?: string | null; description?: string | null }
export const listCategories = () => apiFetch<ExpenseCategory[]>(`${F}/expense-categories`)
export const createCategory = (name: string) => apiSend<ExpenseCategory>('POST', `${F}/expense-categories`, { name })
export const setCategoryActive = (id: number, is_active: boolean) => apiSend<ExpenseCategory>('PATCH', `${F}/expense-categories/${id}`, { is_active })
export const listExpenses = (p: { status?: ExpenseStatus | ''; limit?: number; offset?: number }) => apiFetch<Page<Expense>>(`${F}/expenses`, { query: p as Query })
export const createExpense = (p: ExpensePayload) => apiSend<Expense>('POST', `${F}/expenses`, p)
export const expenseAction = (id: number, action: 'submit' | 'approve' | 'post') => apiSend<Expense>('POST', `${F}/expenses/${id}/${action}`)
export const rejectExpense = (id: number, reason: string) => apiSend<Expense>('POST', `${F}/expenses/${id}/reject`, { reason })
export const voidExpense = (id: number, reason: string) => apiSend<Expense>('POST', `${F}/expenses/${id}/void`, { reason })

// --- Cash -------------------------------------------------------------------------------------------
export interface CashCount { id: number; count_date: string; expected_cash: Money | null; actual_cash: Money; difference: Money | null; reason: string | null }
export interface CashSummary {
  day: string
  opening_cash: Money | null
  lines: Record<string, Money>
  net_movement: Money
  expected_closing: Money | null
  opening_basis: string
  unclassified_receipts: Money
  latest_count: CashCount | null
  notes: string[]
}
export const getCashSummary = (day: string) => apiFetch<CashSummary>(`${F}/cash/summary`, { query: { day } })
export const listCashCounts = () => apiFetch<CashCount[]>(`${F}/cash/counts`)
export const recordCashCount = (p: { count_date: string; actual_cash: Money; reason?: string | null }) => apiSend<CashCount>('POST', `${F}/cash/counts`, p)

// --- Ledger and adjustments ---------------------------------------------------------------------------
export interface LedgerRow {
  key: string
  entry_date: string
  event_type: EventType
  source_module: string
  source_type: string
  source_id: number
  reference: string | null
  amount: Money
  settled_amount: Money
  direction: 'IN' | 'OUT'
  payment_method: string
  status: string
  note: string | null
}
export interface LedgerPage extends Page<LedgerRow> { total_in: Money; total_out: Money }
export const getLedger = (p: Range & { event_type?: EventType | ''; payment_method?: string; limit?: number; offset?: number }) =>
  apiFetch<LedgerPage>(`${F}/ledger`, { query: p as Query })
export const recordEntry = (p: { event_type: EventType; amount: Money; payment_method: PaymentMethod; entry_date: string; supplier_id?: number | null; note?: string | null }, key: string) =>
  apiSend<{ id: number }>('POST', `${F}/entries`, p, { idempotencyKey: key })
export type AdjustResult = { id: number } | { status: 'APPROVAL_REQUIRED'; approval_request_id: number }
export const recordAdjustment = (p: { direction: 'IN' | 'OUT'; amount: Money; payment_method: PaymentMethod; entry_date: string; note: string; approval_request_id?: number | null }) =>
  apiSend<AdjustResult>('POST', `${F}/adjustments`, p)

// --- Payables / receivables / tax ---------------------------------------------------------------------
export interface Payables { as_of: string; suppliers: { supplier_id: number; name: string; balance: Money; total_purchases: Money; payments_made: Money; aging: Aging }[]; total_payable: Money; total_advances: Money; aging: Aging; methodology: string }
export interface Receivables { as_of: string; customers: { customer_id: number; name: string; balance: Money; oldest_open_days: number | null; aging: Aging }[]; total_receivables: Money; total_advances: Money; aging: Aging; methodology: string; overdue: string }
export const getPayables = () => apiFetch<Payables>(`${F}/payables`)
export const getReceivables = () => apiFetch<Receivables>(`${F}/receivables`)

export interface TaxSection { buckets: { rate_name: string; rate_bp: number | null; taxable_amount: Money; tax_amount: Money }[]; taxable_amount: Money; tax_amount: Money; unclassified_amount: Money }
export interface TaxSummary {
  status: 'CONFIGURED' | 'NOT_CONFIGURED'
  tax_type: string | null
  registration_number: string | null
  prices_include_tax: boolean | null
  sales: TaxSection | null
  sales_returns: TaxSection | null
  purchases: TaxSection | null
  purchase_returns: TaxSection | null
  tax_collected: Money | null
  tax_paid: Money | null
  net_tax: Money | null
  quick_sales_amount: Money
  methodology: string
  disclaimer: string
  notes: string[]
}
export const getTaxSummary = (r: Range) => apiFetch<TaxSummary>(`${F}/tax/summary`, { query: r as Query })
export interface TaxSettings { tax_type: 'GST' | 'VAT' | 'SALES_TAX' | 'OTHER'; registration_number: string | null; location_state: string | null; prices_include_tax: boolean }
export interface TaxRate { id: number; name: string; rate_percent: string; category_id: number | null; is_active: boolean }
export const getTaxSettings = () => apiFetch<TaxSettings | null>(`${F}/tax/settings`)
export const saveTaxSettings = (p: TaxSettings) => apiSend<TaxSettings>('PUT', `${F}/tax/settings`, p)
export const listTaxRates = () => apiFetch<TaxRate[]>(`${F}/tax/rates`)
export const createTaxRate = (p: { name: string; rate_percent: string; category_id?: number | null }) => apiSend<TaxRate>('POST', `${F}/tax/rates`, p)
export const setTaxRateActive = (id: number, is_active: boolean) => apiSend<TaxRate>('PATCH', `${F}/tax/rates/${id}`, { is_active })

// --- Periods, reconciliation, settings -------------------------------------------------------------------
export interface Period { id: number; period_start: string; period_end: string; status: PeriodStatus; reopen_approval_pending: boolean }
export const listPeriods = () => apiFetch<Period[]>(`${F}/periods`)
export const createPeriod = (p: { period_start: string; period_end: string }) => apiSend<Period>('POST', `${F}/periods`, p)
export const periodAction = (id: number, action: 'lock' | 'close' | 'unlock') => apiSend<Period>('POST', `${F}/periods/${id}/${action}`)
export const reopenPeriod = (id: number, reason: string) => apiSend<Period>('POST', `${F}/periods/${id}/reopen`, { reason })

export interface ReconItem { row: LedgerRow; status: ReconStatus; confirmed_amount: Money | null; note: string | null }
export interface ReconSummary { counts: Record<string, number>; amounts: Record<string, Money>; total_items: number; bank_integration: string; items: ReconItem[]; notes: string[] }
export const getReconciliation = (r: Range) => apiFetch<ReconSummary>(`${F}/reconciliation`, { query: r as Query })
export const markReconciliation = (p: { source_type: string; source_id: number; status: ReconStatus; confirmed_amount?: Money | null; note?: string | null }) =>
  apiSend<{ id: number }>('POST', `${F}/reconciliation/marks`, p)

export interface FinanceSettings {
  expense_approval_threshold: Money | null
  adjustment_approval_threshold: Money | null
  cash_adjustment_threshold: Money | null
  period_reopen_requires_approval: boolean
  overdue_after_days: number
  expense_spike_pct: number
  margin_drop_points: number
  cash_variance_alert_amount: Money
}
export const getFinanceSettings = () => apiFetch<FinanceSettings>(`${F}/settings`)
export const saveFinanceSettings = (s: FinanceSettings) => apiSend<FinanceSettings>('PUT', `${F}/settings`, s)
