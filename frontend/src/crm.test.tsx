import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CampaignDetailPage } from '@/features/crm/CampaignDetailPage'
import { CrmDashboardPage } from '@/features/crm/CrmDashboardPage'
import { CustomerCrmPage } from '@/features/crm/CustomerCrmPage'
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
function page(path: string, pattern: string, element: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path={pattern} element={element} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}
afterEach(() => vi.unstubAllGlobals())

const campaign = {
  id: 7, name: 'Win-back', description: null, status: 'COMPLETED', channel: 'SMS', target_group_id: null, target_segment: 'INACTIVE',
  promotion_id: null, message_template: 'We miss you', launched_at: '2026-01-05T10:00:00Z', cancel_reason: null, requires_approval: false, created_at: '2026-01-05T09:00:00Z',
}

describe('campaign detail', () => {
  it('shows the honest outcome per customer and says nothing was delivered', async () => {
    routes({
      'GET /api/v1/campaigns/7': () => json(200, campaign),
      'GET /api/v1/campaigns/7/audience': () => json(200, { customer_ids: [1, 2], audience_size: 2 }),
      'GET /api/v1/campaigns/7/sends': () =>
        json(200, { items: [
          { id: 1, customer_id: 1, channel: 'SMS', status: 'NOT_CONFIGURED', detail: 'No SMS provider is configured' },
          { id: 2, customer_id: 2, channel: 'SMS', status: 'SKIPPED_NO_CONSENT', detail: null },
        ] }),
    })
    page('/crm/campaigns/7', '/crm/campaigns/:id', <CampaignDetailPage />)

    expect(await screen.findByText('Not configured')).toBeInTheDocument()
    expect(screen.getByText('No consent')).toBeInTheDocument()
    expect(screen.queryByText('Sent')).not.toBeInTheDocument()
    expect(screen.getByText(/nothing is actually delivered/i)).toBeInTheDocument()
  })

  it('explains a launch that is waiting for a second person', async () => {
    routes({
      'GET /api/v1/campaigns/7': () => json(200, { ...campaign, status: 'DRAFT', requires_approval: true, launched_at: null }),
      'GET /api/v1/campaigns/7/audience': () => json(200, { customer_ids: [], audience_size: 300 }),
      'GET /api/v1/campaigns/7/sends': () => json(200, { items: [] }),
    })
    page('/crm/campaigns/7', '/crm/campaigns/:id', <CampaignDetailPage />)

    expect(await screen.findByText(/second person must approve/i)).toBeInTheDocument()
  })

  it('asks for confirmation, with the audience size, before launching', async () => {
    const mock = routes({
      'GET /api/v1/campaigns/7': () => json(200, { ...campaign, status: 'DRAFT', launched_at: null }),
      'GET /api/v1/campaigns/7/audience': () => json(200, { customer_ids: [1, 2, 3], audience_size: 3 }),
      'GET /api/v1/campaigns/7/sends': () => json(200, { items: [] }),
      'POST /api/v1/campaigns/7/launch': () => json(200, { ...campaign }),
    })
    page('/crm/campaigns/7', '/crm/campaigns/:id', <CampaignDetailPage />)

    fireEvent.click(await screen.findByRole('button', { name: 'Launch' }))
    expect(await screen.findByText(/Launch to 3 customer/)).toBeInTheDocument()
    expect(mock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'POST')).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: 'Yes, launch' }))
    await waitFor(() => expect(mock.mock.calls.some(([u, init]) => String(u).endsWith('/launch') && (init as RequestInit).method === 'POST')).toBe(true))
  })
})

describe('customer growth profile', () => {
  const profile = {
    customer_id: 4, name: 'Ravi', phone: null, email: null, customer_type: null, source: null, tags: [], is_active: true,
    preferred_contact_channel: null, marketing_opt_in_email: false, marketing_opt_in_sms: false, marketing_opt_in_whatsapp: false, marketing_opt_in_push: false,
    analytics: { total_purchases: '500.00', outstanding: '0.00', days_since_last_purchase: 12, segments: ['ACTIVE'] },
    loyalty_balance: 40, loyalty_program_active: true, referrals_made: 0, referral_code: 'ABC123',
  }
  const base = {
    'GET /api/v1/crm/customers/4/profile': () => json(200, profile),
    'GET /api/v1/crm/customers/4/timeline': () => json(200, []),
    'GET /api/v1/crm/customers/4/notes': () => json(200, []),
    'GET /api/v1/loyalty/customers/4/ledger': () => json(200, { items: [], total: 0, balance: 40 }),
  }

  it('starts with every marketing channel unticked and sends only the one that was changed', async () => {
    const mock = routes({ ...base, 'PATCH /api/v1/crm/customers/4/classification': () => json(200, { ...profile, marketing_opt_in_sms: true }) })
    page('/customers/4/crm', '/customers/:id/crm', <CustomerCrmPage />)

    const sms = await screen.findByRole('checkbox', { name: 'SMS' })
    expect(sms).not.toBeChecked()
    fireEvent.click(sms)

    await waitFor(() => {
      const call = mock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')
      expect(call).toBeDefined()
      expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ marketing_opt_in_sms: true })
    })
  })

  it('tells the person a large adjustment was not applied and needs approval', async () => {
    routes({ ...base, 'POST /api/v1/loyalty/customers/4/adjust': () => json(202, { status: 'APPROVAL_REQUIRED', approval_request_id: 9 }) })
    page('/customers/4/crm', '/customers/:id/crm', <CustomerCrmPage />)

    fireEvent.change(await screen.findByLabelText(/Points \(\+ add/), { target: { value: '5000' } })
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Goodwill' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record adjustment' }))

    expect(await screen.findByText(/has not been applied/i)).toBeInTheDocument()
    expect(screen.getByText(/request #9/)).toBeInTheDocument()
  })
})

describe('dashboard', () => {
  it('says "Not available" instead of inventing a rate when there is no history', async () => {
    routes({
      'GET /api/v1/crm/dashboard': () =>
        json(200, {
          period_days: 90,
          customers: { total_customers: 0, new_customers: 0, active_customers: 0, returning_customers: 0, inactive_customers: 0 },
          revenue: { customer_revenue: '0.00', average_transaction_value: null, repeat_customer_revenue: '0.00' },
          retention: { customers_with_purchases: 0, repeat_customers: 0, repeat_purchase_rate: null, inactive_customer_count: 0, reactivated_count: 0, average_purchase_interval_days: null, cohort_retention_rate: 'NOT_ENOUGH_DATA' },
          loyalty: { points_issued: 0, points_redeemed: 0, points_outstanding: 0, program_active: false },
          campaigns: { total_campaigns: 0, running: 0, completed: 0, draft: 0 },
          referrals: { total_referrals: 0, successful_referrals: 0, pending_referrals: 0 },
        }),
    })
    page('/crm', '/crm', <CrmDashboardPage />)

    expect(await screen.findByText('Not enough data yet')).toBeInTheDocument()
    expect(screen.getAllByText('Not available').length).toBeGreaterThanOrEqual(2)
  })
})

describe('translations', () => {
  const keys = (o: object, prefix = ''): string[] =>
    Object.entries(o).flatMap(([k, v]) => (typeof v === 'object' && v !== null ? keys(v, `${prefix}${k}.`) : [`${prefix}${k}`]))
  it('has the same growth keys in Hindi as in English', () => {
    expect(keys(hi.crm).sort()).toEqual(keys(en.crm).sort())
  })
})
