import { API_V1_PREFIX, apiFetch, apiSend, type Query, type SendOptions } from './client'
import type {
  BalanceFilter,
  BalanceSummary,
  Customer,
  CustomerPayload,
  CustomerSaved,
  KhataEntryResult,
  LedgerEntry,
  LedgerEntryType,
  Page,
  PaymentMethod,
  StatusFilter,
} from './types'

const BASE = `${API_V1_PREFIX}/customers`

export interface CustomerListParams {
  q?: string
  status?: StatusFilter
  balance?: BalanceFilter
  /** "balance" puts the largest amounts owed first. */
  sort?: 'name' | 'balance'
  limit?: number
  offset?: number
}

export const listCustomers = (params: CustomerListParams) => apiFetch<Page<Customer>>(BASE, { query: params as Query })

export const getCustomer = (id: number) => apiFetch<Customer>(`${BASE}/${id}`)

export const getBalance = (id: number) => apiFetch<BalanceSummary>(`${BASE}/${id}/balance`)

/** Create a customer, optionally with what they already owe (an opening balance). */
export const createCustomer = (
  payload: CustomerPayload & { opening_balance?: string | null },
  options?: SendOptions,
) => apiSend<CustomerSaved>('POST', BASE, payload, options)

export const updateCustomer = (id: number, changes: Partial<CustomerPayload>) =>
  apiSend<CustomerSaved>('PATCH', `${BASE}/${id}`, changes)

export const setCustomerActive = (id: number, active: boolean) =>
  apiSend<Customer>('POST', `${BASE}/${id}/${active ? 'activate' : 'deactivate'}`)

export interface LedgerParams {
  entry_type?: LedgerEntryType | ''
  date_from?: string
  date_to?: string
  limit?: number
  offset?: number
}

export const getLedger = (id: number, params: LedgerParams) =>
  apiFetch<Page<LedgerEntry>>(`${BASE}/${id}/ledger`, { query: params as Query })

export interface EntryFields {
  amount: string
  entry_date?: string | null
  note?: string | null
}

export const addOpeningBalance = (id: number, payload: EntryFields) =>
  apiSend<KhataEntryResult>('POST', `${BASE}/${id}/opening-balance`, payload)

export const addPayment = (
  id: number,
  payload: EntryFields & { payment_method?: PaymentMethod | null; payment_reference?: string | null },
) => apiSend<KhataEntryResult>('POST', `${BASE}/${id}/payments`, payload)

export const addAdjustment = (
  id: number,
  payload: { amount: string; direction: 'INCREASE' | 'DECREASE'; reason: string; entry_date?: string | null },
) => apiSend<KhataEntryResult>('POST', `${BASE}/${id}/adjustments`, payload)

export const reverseEntry = (customerId: number, entryId: number, reason: string) =>
  apiSend<KhataEntryResult>('POST', `${BASE}/${customerId}/ledger/${entryId}/reverse`, { reason })
