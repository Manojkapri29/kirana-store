import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CashPage } from '@/features/finance/CashPage'
import { ExpensesPage } from '@/features/finance/ExpensesPage'
import { FinanceDashboardPage } from '@/features/finance/FinanceDashboardPage'
import { PeriodsPage } from '@/features/finance/PeriodsPage'
import { ReconciliationPage } from '@/features/finance/ReconciliationPage'
import { en } from '@/i18n/locales/en'
import { hi } from '@/i18n/locales/hi'

function json(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}
type Handler = (init?: RequestInit) => Promise<Response>
function routes(map: Record<string, Handler>) {
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).split('?')[0]
    const handler = map[`${init?.method ?? 'GET'} ${path}`]
    return handler ? handler(init) : json(404, { message: 'not found' })
  })
  vi.stubGlobal('fetch', mock)
  return mock
}
function page(element: React.ReactNode, path = '/f') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><Routes><Route path={path} element={element} /></Routes></MemoryRouter>
    </QueryClientProvider>,
  )
}
afterEach(() => vi.unstubAllGlobals())

const pnl = (over: Record<string, unknown> = {}) => ({
  date_from: '2026-09-01', date_to: '2026-09-24', detailed_sales: '0.00', quick_sales: '500.00', online_sales: '0.00', sales_returns: '0.00', revenue: '500.00', cogs: null, gross_profit: null,
  operating_expenses: '80.00', net_profit: null, gross_margin_pct: null, status: 'NOT_AVAILABLE', status_note: 'Insufficient Cost Data',
  costed_sales: { status: 'NOT_AVAILABLE', revenue: null, cogs: null, gross_profit: null, coverage_pct: null }, other_income: '0.00', detailed_sales_without_cost: 0, returns_without_cost: 0, quick_sales_have_no_cost: true, notes: [], ...over,
})
const dashboard = () => ({
  date_from: '2026-09-01', date_to: '2026-09-24', comparison_from: '2026-08-08', comparison_to: '2026-08-31', pnl: pnl(),
  cash_flow: { date_from: '', date_to: '', inflow: '500.00', outflow: '80.00', net: '420.00', by_class: {}, by_event: {}, by_method: {}, unclassified_amount: '0.00', basis: '', notes: [] },
  receivables_total: '250.00', payables_total: '0.00', customer_outstanding: '250.00', supplier_outstanding: '0.00', receivables_aging: { '0-30': '250.00', '31-60': '0.00', '61-90': '0.00', '90+': '0.00' },
  payables_aging: { '0-30': '0.00', '31-60': '0.00', '61-90': '0.00', '90+': '0.00' }, tax_status: 'NOT_CONFIGURED', tax_collected: null, tax_paid: null, net_tax: null,
  revenue_change: { current: '500.00', previous: '250.00', change_pct: '100.0' }, gross_profit_change: { current: null, previous: null, change_pct: null }, net_profit_change: null, expenses_change: null, net_cash_flow_change: null,
  revenue_trend: [{ period_start: '2026-09-24', period_end: '2026-09-24', revenue: '500.00', cogs: null, gross_profit: null, operating_expenses: '80.00', net_profit: null }], cash_flow_trend: [], expense_categories: [], payment_method_mix: [], alert_count: 1, granularity: 'day', notes: [],
})

describe('finance dashboard', () => {
  it('shows Not Available for profit the backend cannot know, and warns instead of showing zero', async () => {
    routes({
      'GET /api/v1/finance/dashboard': () => json(200, dashboard()),
      'GET /api/v1/finance/alerts': () => json(200, [{ code: 'MISSING_COST', severity: 'INFO', title: 'Missing cost data', message: '1 sale has an unknown cost.', figures: {} }]),
    })
    page(<FinanceDashboardPage />)

    expect(await screen.findByText(/Profit Not Available/)).toBeInTheDocument()
    expect(screen.getAllByText('Not Available').length).toBeGreaterThanOrEqual(3) // COGS, gross profit, net profit
    expect(screen.getByText('Missing cost data')).toBeInTheDocument()
    expect(screen.getByText(/100.0% vs previous period/)).toBeInTheDocument()
  })

  it('shows the tax card as not configured rather than a zero', async () => {
    routes({ 'GET /api/v1/finance/dashboard': () => json(200, dashboard()), 'GET /api/v1/finance/alerts': () => json(200, []) })
    page(<FinanceDashboardPage />)
    expect(await screen.findByText('Not configured')).toBeInTheDocument()
  })
})

describe('expenses', () => {
  const base = { expense_no: 'EXP/2026-27/0001', expense_date: '2026-09-24', category_id: 1, amount: '500.00', payment_method: 'CASH', payee: null, description: null, requires_approval: false, rejection_reason: null, void_reason: null }
  const handlers = (status: string, extra: Record<string, Handler> = {}) => ({
    'GET /api/v1/finance/expense-categories': () => json(200, [{ id: 1, name: 'Rent', is_active: true, cash_flow_class: null }]),
    'GET /api/v1/finance/expenses': () => json(200, { items: [{ ...base, id: 9, status }], total: 1, limit: 100, offset: 0 }),
    ...extra,
  })

  it('offers only the next valid step for each status', async () => {
    routes(handlers('DRAFT'))
    const { unmount } = page(<ExpensesPage />)
    expect(await screen.findByRole('button', { name: 'Submit' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Post to books' })).not.toBeInTheDocument()
    unmount()

    routes(handlers('APPROVED'))
    const second = page(<ExpensesPage />)
    expect(await screen.findByRole('button', { name: 'Post to books' })).toBeInTheDocument()
    second.unmount()

    routes(handlers('POSTED'))
    page(<ExpensesPage />)
    expect(await screen.findByRole('button', { name: 'Void' })).toBeInTheDocument()
  })

  it('explains a submitted expense is waiting for a second person', async () => {
    routes(handlers('SUBMITTED'))
    page(<ExpensesPage />)
    expect(await screen.findByText(/second person must approve/i)).toBeInTheDocument()
  })

  it('voiding asks for a reason and sends it', async () => {
    const mock = routes(handlers('POSTED', { 'POST /api/v1/finance/expenses/9/void': () => json(200, { ...base, id: 9, status: 'VOIDED' }) }))
    page(<ExpensesPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Void' }))
    const reason = screen.getByLabelText('Reason')
    fireEvent.change(reason, { target: { value: 'Entered twice' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Void' }).at(-1)!)
    await waitFor(() => {
      const call = mock.mock.calls.find(([u]) => String(u).endsWith('/void'))
      expect(call).toBeDefined()
      expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ reason: 'Entered twice' })
    })
  })
})

describe('cash', () => {
  const summary = { day: '2026-09-24', opening_cash: null, lines: Object.fromEntries(['cash_sales', 'customer_payments', 'other_cash_income', 'owner_capital', 'supplier_refunds', 'cash_purchases', 'expenses', 'supplier_payments', 'refunds', 'owner_withdrawals', 'adjustments'].map((k) => [k, '0.00'])), net_movement: '0.00', expected_closing: null, opening_basis: 'No cash count exists before this day, so the opening cash cannot be known.', unclassified_receipts: '0.00', latest_count: null, notes: [] }

  it('says the opening cash is unknown instead of showing zero', async () => {
    routes({ 'GET /api/v1/finance/cash/summary': () => json(200, summary), 'GET /api/v1/finance/cash/counts': () => json(200, []) })
    page(<CashPage />)
    expect(await screen.findByText(/cannot be known/)).toBeInTheDocument()
    expect(screen.getAllByText('Not Available').length).toBeGreaterThanOrEqual(2)
  })

  it('tells the person a large adjustment was not applied', async () => {
    routes({
      'GET /api/v1/finance/cash/summary': () => json(200, summary), 'GET /api/v1/finance/cash/counts': () => json(200, []),
      'POST /api/v1/finance/adjustments': () => json(202, { status: 'APPROVAL_REQUIRED', approval_request_id: 12 }),
    })
    page(<CashPage />)
    await screen.findByText(/cannot be known/)
    const amounts = screen.getAllByLabelText('Amount')
    fireEvent.change(amounts[amounts.length - 1], { target: { value: '5000' } })
    const reasons = screen.getAllByLabelText('Reason')
    fireEvent.change(reasons[reasons.length - 1], { target: { value: 'Bank deposit' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record adjustment' }))
    expect(await screen.findByText(/NOT applied/)).toBeInTheDocument()
    expect(screen.getByText(/request #12/)).toBeInTheDocument()
  })
})

describe('periods', () => {
  const period = { id: 3, period_start: '2026-08-01', period_end: '2026-08-31', status: 'OPEN', reopen_approval_pending: false }
  it('asks for confirmation before closing and does not close on the first click', async () => {
    const mock = routes({ 'GET /api/v1/finance/periods': () => json(200, [period]), 'POST /api/v1/finance/periods/3/close': () => json(200, { ...period, status: 'CLOSED' }) })
    page(<PeriodsPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Close' }))
    expect(await screen.findByText(/Nothing dated inside it can then be changed directly/)).toBeInTheDocument()
    expect(mock.mock.calls.some(([u]) => String(u).endsWith('/close'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, close it' }))
    await waitFor(() => expect(mock.mock.calls.some(([u, i]) => String(u).endsWith('/close') && (i as RequestInit).method === 'POST')).toBe(true))
  })

  it('shows a pending reopen request on a closed period', async () => {
    routes({ 'GET /api/v1/finance/periods': () => json(200, [{ ...period, status: 'CLOSED', reopen_approval_pending: true }]) })
    page(<PeriodsPage />)
    expect(await screen.findByText(/waiting for a second person/)).toBeInTheDocument()
  })
})

describe('reconciliation', () => {
  it('states that no bank integration exists and invents no bank rows', async () => {
    routes({ 'GET /api/v1/finance/reconciliation': () => json(200, { counts: { MATCHED: 0, UNMATCHED: 0, PARTIAL: 0, REVIEW_REQUIRED: 0 }, amounts: { MATCHED: '0.00', UNMATCHED: '0.00', PARTIAL: '0.00', REVIEW_REQUIRED: '0.00' }, total_items: 0, bank_integration: 'Bank Integration Not Configured', items: [], notes: [] }) })
    page(<ReconciliationPage />)
    expect(await screen.findByText(/Bank Integration Not Configured/)).toBeInTheDocument()
    expect(await screen.findByText('No electronic payments in this period')).toBeInTheDocument()
  })
})

describe('translations', () => {
  const keys = (o: object, prefix = ''): string[] =>
    Object.entries(o).flatMap(([k, v]) => (typeof v === 'object' && v !== null ? keys(v, `${prefix}${k}.`) : [`${prefix}${k}`]))
  it('has the same finance keys in Hindi as in English', () => {
    expect(keys(hi.finance).sort()).toEqual(keys(en.finance).sort())
  })
})
