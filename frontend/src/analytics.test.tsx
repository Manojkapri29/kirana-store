import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { BuilderPage } from '@/features/analytics/BuilderPage'
import { DrillPage } from '@/features/analytics/DrillPage'
import { ExecutivePage } from '@/features/analytics/ExecutivePage'
import { ObservationsPage } from '@/features/analytics/InsightsPage'
import { ReportsPage } from '@/features/analytics/ReportsPage'
import { SavedReportsPage } from '@/features/analytics/SavedReportsPage'
import { SchedulesPage } from '@/features/analytics/SchedulesPage'
import { en } from '@/i18n/locales/en'
import { hi } from '@/i18n/locales/hi'

function json(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}
type Handler = (init?: RequestInit, url?: URL) => Promise<Response>
function routes(map: Record<string, Handler>) {
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost')
    const handler = map[`${init?.method ?? 'GET'} ${url.pathname}`]
    return handler ? handler(init, url) : json(404, { message: 'not found' })
  })
  vi.stubGlobal('fetch', mock)
  return mock
}
function page(element: React.ReactNode, path = '/a') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><Routes><Route path="/a" element={element} /></Routes></MemoryRouter>
    </QueryClientProvider>,
  )
}
afterEach(() => vi.unstubAllGlobals())

const period = { start: '2026-09-01', end: '2026-09-24', label: 'This month' }
const prevPeriod = { start: '2026-08-08', end: '2026-08-31', label: 'Previous period' }
const def = (key: string, name: string, unit = 'money') => ({ key, name, group: 'sales', description: `${name} description`, formula: `${name} formula`, source: `${name} source`, unit, limitations: `${name} limits`, permission: 'X', as_of_only: false })
const kpi = (key: string, name: string, amount: string | null, over: Record<string, unknown> = {}) => ({
  definition: def(key, name, key === 'gross_margin' ? 'percent' : 'money'), period, comparison_period: prevPeriod,
  current: { amount, availability: amount === null ? 'NOT_AVAILABLE' : 'AVAILABLE', reason: amount === null ? 'Some sales have no cost.' : null }, previous: null, change: null, ...over,
})
const executive = () => ({
  period, comparison: prevPeriod, notes: ['KPIs are shop-wide for the period.'],
  sections: [
    { key: 'overview', title: 'Overview', hidden_kpis: [], trend: null, trend_label: null, kpis: [
      kpi('revenue', 'Revenue', '1500.00', { change: { absolute: '750.00', percent: '100.0', note: null } }),
      kpi('sales_growth', 'Sales growth', null, { change: null, current: { amount: null, availability: 'INSUFFICIENT_DATA', reason: 'Insufficient comparison data' } }),
    ] },
    { key: 'profitability', title: 'Profitability', hidden_kpis: ['net_profit'], trend: null, trend_label: null, kpis: [kpi('gross_profit', 'Gross profit', null)] },
  ],
})
const table = (over: Record<string, unknown> = {}) => ({
  columns: [['period', 'Period', 'text'], ['combined_net', 'Revenue', 'money'], ['gross_profit', 'Gross profit', 'money'], ['day', 'Day', 'date']],
  rows: [{ period: '2026-09', combined_net: '1500.00', gross_profit: null, day: '2026-09-24' }], total: 1, limit: 50, offset: 0, title: 'Sales by month',
  period, comparison: null, notes: ['Quick Sales have no product detail.'], filters_applied: [], filters_ignored: ['product_id'], source: 'Posted sales', ...over,
})

describe('executive dashboard', () => {
  it('shows values, honest gaps, changes, hidden figures and how each figure is calculated', async () => {
    routes({ 'GET /api/v1/analytics/executive': () => json(200, executive()) })
    page(<ExecutivePage />)

    expect(await screen.findByText('Overview')).toBeInTheDocument()
    const revenue = screen.getByTestId('kpi-revenue')
    expect(within(revenue).getByText('₹1,500.00')).toBeInTheDocument()
    expect(within(revenue).getByText('+100.0% vs comparison')).toBeInTheDocument()
    expect(within(screen.getByTestId('kpi-gross_profit')).getByText('Not Available')).toBeInTheDocument() // never a zero
    expect(within(screen.getByTestId('kpi-sales_growth')).getByText('Insufficient Data')).toBeInTheDocument()
    expect(screen.getByText(/1 figure\(s\) are hidden/)).toBeInTheDocument()
    expect(within(revenue).getByText('Revenue formula')).toBeInTheDocument()
    expect(screen.getByText(/Compared with Previous period/)).toBeInTheDocument()
  })

  it('sends the chosen preset and comparison to the server and never computes a period itself', async () => {
    const mock = routes({ 'GET /api/v1/analytics/executive': () => json(200, executive()) })
    page(<ExecutivePage />)
    await screen.findByText('Overview')
    fireEvent.change(screen.getByLabelText('Period'), { target: { value: 'last_quarter' } })
    fireEvent.change(screen.getByLabelText('Compare with'), { target: { value: 'previous_year' } })
    await waitFor(() => {
      const last = new URL(String(mock.mock.calls.at(-1)![0]), 'http://localhost')
      expect(last.searchParams.get('preset')).toBe('last_quarter')
      expect(last.searchParams.get('compare')).toBe('previous_year')
    })
  })

  it('waits for both dates before asking for a custom period', async () => {
    const mock = routes({ 'GET /api/v1/analytics/executive': () => json(200, executive()) })
    page(<ExecutivePage />)
    await screen.findByText('Overview')
    const before = mock.mock.calls.length
    fireEvent.change(screen.getByLabelText('Period'), { target: { value: 'custom' } })
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-09-01' } })
    expect(mock.mock.calls.length).toBe(before)
  })

  it('shows an error with a retry, not a blank page', async () => {
    routes({ 'GET /api/v1/analytics/executive': () => json(500, { message: 'boom' }) })
    page(<ExecutivePage />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})

describe('reports', () => {
  it('shows blanks as Not Available, lists filters that did not apply, and offers CSV, Excel and PDF', async () => {
    routes({ 'GET /api/v1/analytics/sales/trend': () => json(200, table()) })
    page(<ReportsPage />)
    expect(await screen.findByText('₹1,500.00')).toBeInTheDocument()
    expect(screen.getAllByText('Not Available').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/do not apply to this report and were not used: product_id/)).toBeInTheDocument()
    expect(screen.getByText('Quick Sales have no product detail.')).toBeInTheDocument()
    for (const label of ['CSV', 'Excel', 'PDF']) expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
  })

  it('downloads the export of the report on screen with the same period', async () => {
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:x', revokeObjectURL: () => undefined }))
    const mock = routes({
      'GET /api/v1/analytics/sales/trend': () => json(200, table()),
      'GET /api/v1/analytics/export/sales-trend-month': () => Promise.resolve(new Response('x', { headers: { 'Content-Disposition': 'attachment; filename="sales.pdf"' } })),
    })
    page(<ReportsPage />)
    await screen.findByText('₹1,500.00')
    fireEvent.click(screen.getByRole('button', { name: 'PDF' }))
    await waitFor(() => expect(mock.mock.calls.some(([u]) => String(u).includes('/export/sales-trend-month') && String(u).includes('format=pdf') && String(u).includes('preset=this_month'))).toBe(true))
  })
})

describe('drill-down', () => {
  it('follows the drill link of a row to the next level and lets you step back', async () => {
    const mock = routes({
      'GET /api/v1/analytics/drill/revenue/months': () => json(200, table({ title: 'Sales by month', rows: [{ period: '2026-09', combined_net: '1500.00', gross_profit: null, day: null, drill: { path: 'revenue', level: 'days', month: '2026-09' } }] })),
      'GET /api/v1/analytics/drill/revenue/days': () => json(200, table({ title: 'Sales by day', rows: [{ period: '2026-09-24', combined_net: '1500.00', gross_profit: null, day: null, drill: null }] })),
    })
    page(<DrillPage />)
    expect(await screen.findByText('Sales by month')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    expect(await screen.findByText('Sales by day')).toBeInTheDocument()
    const days = mock.mock.calls.map(([u]) => new URL(String(u), 'http://localhost')).find((u) => u.pathname.endsWith('/days'))!
    expect(days.searchParams.get('month')).toBe('2026-09')
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Where you are' })).getByRole('button', { name: 'Months' }))
    expect(await screen.findByText('Sales by month')).toBeInTheDocument()
  })
})

describe('report builder', () => {
  const datasets = {
    max_rows: 20000,
    datasets: [
      { key: 'sales', label: 'Sales', available: true, reason: null, source: 'Posted sales', notes: ['Quick Sales are money-only.'], period_applies: true, fields: [
        { key: 'kind', label: 'Kind', kind: 'text', operators: ['eq', 'ne', 'contains', 'in'], aggregations: ['COUNT'] },
        { key: 'total_amount', label: 'Total', kind: 'money', operators: ['gt', 'lt'], aggregations: ['COUNT', 'SUM', 'AVG', 'MIN', 'MAX'] },
      ] },
      { key: 'finance', label: 'Finance', available: false, reason: 'Your role does not include the permission to read this data.', source: '', notes: [], period_applies: true, fields: [] },
      { key: 'online_orders', label: 'Online Orders', available: false, reason: 'Not Available: online orders are not connected.', source: '', notes: [], period_applies: true, fields: [] },
    ],
  }

  it('offers only allowed datasets and explains the ones it cannot', async () => {
    routes({ 'GET /api/v1/analytics/builder/datasets': () => json(200, datasets) })
    page(<BuilderPage />)
    expect(await screen.findByLabelText('Data')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Sales' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Finance' })).toBeNull()
    expect(screen.getByText(/Online Orders: Not Available: online orders are not connected/)).toBeInTheDocument()
    expect(screen.getByText(/Nothing you type is ever run as a database command/)).toBeInTheDocument()
  })

  it('only offers the aggregations a field allows, and sends a structured definition (never SQL)', async () => {
    let sent: { dataset: string; definition: Record<string, unknown> } | null = null
    routes({
      'GET /api/v1/analytics/builder/datasets': () => json(200, datasets),
      'POST /api/v1/analytics/builder/preview': (init) => { sent = JSON.parse(String(init?.body)); return json(200, table({ title: 'Custom report: Sales', rows: [{ period: 'Quick', combined_net: '80.00', gross_profit: null, day: null }] })) },
    })
    page(<BuilderPage />)
    await screen.findByLabelText('Data')
    fireEvent.change(screen.getByLabelText('Report type'), { target: { value: 'summary' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Kind' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add a total' }))
    const op = screen.getByLabelText('Operation') as HTMLSelectElement
    expect([...op.options].map((o) => o.value)).toEqual(['COUNT']) // the first field is text: only COUNT is offered
    fireEvent.change(screen.getByLabelText('Field'), { target: { value: 'total_amount' } })
    expect([...(screen.getByLabelText('Operation') as HTMLSelectElement).options].map((o) => o.value)).toEqual(['COUNT', 'SUM', 'AVG', 'MIN', 'MAX'])
    fireEvent.change(screen.getByLabelText('Operation'), { target: { value: 'SUM' } })
    fireEvent.click(screen.getByRole('button', { name: 'Run report' }))
    await waitFor(() => expect(sent).not.toBeNull())
    expect(sent!.dataset).toBe('sales')
    expect(sent!.definition).toMatchObject({ columns: [], group_by: ['kind'], aggregations: [{ field: 'total_amount', op: 'SUM' }] })
    expect(JSON.stringify(sent)).not.toMatch(/select|drop/i)
  })

  it('saves a named report', async () => {
    let saved: Record<string, unknown> | null = null
    routes({
      'GET /api/v1/analytics/builder/datasets': () => json(200, datasets),
      'POST /api/v1/analytics/reports': (init) => { saved = JSON.parse(String(init?.body)); return json(201, { id: 1, ...saved }) },
    })
    page(<BuilderPage />)
    await screen.findByLabelText('Data')
    expect(screen.getByRole('button', { name: 'Save report' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Report name'), { target: { value: 'Cash sales' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save report' }))
    expect(await screen.findByText('Report saved.')).toBeInTheDocument()
    expect(saved).toMatchObject({ name: 'Cash sales', dataset: 'sales' })
  })
})

describe('saved reports', () => {
  const report = { id: 4, name: 'Cash sales', description: null, dataset: 'sales', definition: { columns: [], group_by: [], aggregations: [], filters: [], sort: [] }, is_archived: false, created_by: 1, updated_by: null, created_at: '', updated_at: '' }
  it('archives instead of deleting, and there is no delete control', async () => {
    let archived = false
    routes({
      'GET /api/v1/analytics/reports': () => json(200, { items: archived ? [] : [report] }),
      'POST /api/v1/analytics/reports/4/archive': () => { archived = true; return json(200, { ...report, is_archived: true }) },
    })
    page(<SavedReportsPage />)
    expect(await screen.findByText('Cash sales')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /delete/i })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Archive' }))
    expect(await screen.findByText('No saved reports yet.')).toBeInTheDocument()
  })
})

describe('scheduled reports', () => {
  const schedule = { id: 7, report_type: 'adv_sales', schedule: 'WEEKLY', is_active: true, next_run_at: '2026-09-28T00:00:00Z', last_run_at: null, last_status: null, last_error: null, params: {}, export_format: 'PDF', delivery_channel: 'EMAIL', recipients: ['a@shop.in'] }
  it('never says an email was sent when no channel is configured', async () => {
    routes({
      'GET /api/v1/scheduled-reports': () => json(200, { items: [schedule] }),
      'GET /api/v1/analytics/reports': () => json(200, { items: [] }),
      'GET /api/v1/scheduled-reports/7/runs': () => json(200, [{ id: 1, run_key: 'WEEKLY:2026-09-20', period_start: '2026-09-14', period_end: '2026-09-20', status: 'OK', delivery_status: 'NOT_CONFIGURED', error: null, summary: {}, generated_at: '2026-09-24T00:00:00Z' }]),
    })
    page(<SchedulesPage />)
    expect(await screen.findByText(/No email service is connected/)).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'History' }))
    expect(await screen.findByText('Delivery Channel Not Configured')).toBeInTheDocument()
    expect(screen.queryByText(/sent/i, { selector: 'li *' })).toBeNull()
  })

  it('creates a schedule with a structured request', async () => {
    let body: Record<string, unknown> | null = null
    routes({
      'GET /api/v1/scheduled-reports': () => json(200, { items: [] }),
      'GET /api/v1/analytics/reports': () => json(200, { items: [] }),
      'POST /api/v1/scheduled-reports/advanced': (init) => { body = JSON.parse(String(init?.body)); return json(201, schedule) },
    })
    page(<SchedulesPage />)
    await screen.findByText('No advanced reports are scheduled.')
    fireEvent.change(screen.getByLabelText('Report'), { target: { value: 'adv_finance' } })
    fireEvent.click(screen.getByLabelText('Also email it'))
    fireEvent.change(screen.getByLabelText('Recipients'), { target: { value: 'a@shop.in, b@shop.in' } })
    fireEvent.click(screen.getByRole('button', { name: 'Schedule report' }))
    await waitFor(() => expect(body).not.toBeNull())
    expect(body).toMatchObject({ kind: 'adv_finance', schedule: 'WEEKLY', delivery_channel: 'EMAIL', recipients: ['a@shop.in', 'b@shop.in'] })
  })
})

describe('observations', () => {
  it('states that these are things seen together, not causes', async () => {
    routes({ 'GET /api/v1/analytics/insights': () => json(200, {
      period, comparison: null, hidden: ['revenue_and_gross_profit'], caution: 'This shows what appeared together in the period. It does not show that one caused the other.',
      insights: [{ key: 'online_repeat_customers', title: 'Online order repeat customers', availability: 'NOT_AVAILABLE', statement: 'Not Available: online orders are not connected.', evidence: [], sources: ['Online orders'], limitations: ['x'] }],
    }) })
    page(<ObservationsPage />)
    expect(await screen.findByText(/does not show that one caused the other/)).toBeInTheDocument()
    expect(screen.getByText('Not Available: online orders are not connected.')).toBeInTheDocument()
    expect(screen.getByText(/1 observation\(s\) are hidden/)).toBeInTheDocument()
  })
})

describe('translations', () => {
  it('has a Hindi text for every analytics string, with the same placeholders', () => {
    const walk = (a: unknown, b: unknown, path: string) => {
      if (typeof a === 'string') {
        expect(typeof b, path).toBe('string')
        expect((b as string).trim().length, path).toBeGreaterThan(0)
        expect((a.match(/{{\w+}}/g) ?? []).sort(), path).toEqual(((b as string).match(/{{\w+}}/g) ?? []).sort())
        return
      }
      for (const key of Object.keys(a as object)) walk((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key], `${path}.${key}`)
    }
    walk(en.analytics, hi.analytics, 'analytics')
  })
})
