import { API_V1_PREFIX, apiFetch } from './client'

export type AccountStatus = 'ACTIVE' | 'TRIAL' | 'SUSPENDED' | 'DEACTIVATED'

export interface Account {
  shop_name: string
  account_status: AccountStatus
  /** A safe sentence for the owner, present only when the account is restricted. */
  message: string | null
  plan_code: string
  plan_name: string
  subscription_status: string | null
  capabilities: Record<string, boolean>
}

export interface UsageItem {
  used: number
  limit: number | null
  remaining: number | null
  unlimited: boolean
  percent_used: number | null
}

export interface Usage {
  period: string
  plan_code: string
  limits: Record<string, UsageItem>
}

export interface BackupStatus {
  state: 'recent' | 'stale' | 'none' | 'not_configured'
  last_backup_at: string | null
}

export interface HealthAlert {
  kind: string
  severity: string
  title: string
  message: string
}

export interface BusinessHealth {
  alerts: HealthAlert[]
  backup: BackupStatus
}

/** A section that cannot be known says so instead of showing a made-up number. */
export interface Unavailable {
  available: false
  reason?: string
  message?: string
}

export interface Overview {
  period: { from: string; to: string }
  sales: {
    gross: string
    discounts: string
    net: string
    transactions: number
    average_bill: string | null
    detailed: { count: number; net: string }
    quick: { count: number; net: string }
    online: Unavailable
    returns: { count: number; refunded: string }
    net_after_returns: string
    series: { period: string; net: string; gross: string; transactions: number }[]
    bucket: string
  }
  inventory: {
    products: number
    in_stock: number
    low_stock: number
    out_of_stock: number
    stock_value: string | null
    stock_value_note: string | null
    products_without_cost: number
    slow_moving: { name: string; stock: string; sold: string; days: number }[]
  }
  customers: {
    outstanding_total: string
    customers_owing: number
    collections: string
    credit_sales: { bills: number; unpaid: string }
    customers_who_bought: number
    returning_customers: number
  }
  purchases: {
    count: number
    total: string
    net: string
    by_supplier: { supplier: string; purchases: number; total: string }[]
  }
  profit:
    | Unavailable
    | { available: true; gross_profit: string; margin_percent: string | null; cogs: string; sales_without_cost: number; note: string }
  promotions:
    | Unavailable
    | { available: true; discount_total: string; offer_applications: number; coupon_uses: number }
  online_store:
    | Unavailable
    | { available: true; placed: number; delivered: number; rejected_or_cancelled: number; delivered_value: string | null; note: string }
  source: string
}

export const getAccount = () => apiFetch<Account>(`${API_V1_PREFIX}/account`)
export const getUsage = () => apiFetch<Usage>(`${API_V1_PREFIX}/account/usage`)
export const getBackupStatus = () => apiFetch<BackupStatus>(`${API_V1_PREFIX}/account/backup-status`)
export const getBusinessHealth = () => apiFetch<BusinessHealth>(`${API_V1_PREFIX}/account/health`)
export const getOverview = (query: { date_from?: string; date_to?: string; bucket?: 'day' | 'week' | 'month' } = {}) =>
  apiFetch<Overview>(`${API_V1_PREFIX}/analytics/overview`, { query })
