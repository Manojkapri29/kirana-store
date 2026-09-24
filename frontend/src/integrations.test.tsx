import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { IntegrationsPage } from '@/features/integrations/IntegrationsPage'
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
function page() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/i']}><Routes><Route path="/i" element={<IntegrationsPage />} /></Routes></MemoryRouter></QueryClientProvider>)
}
afterEach(() => vi.unstubAllGlobals())

const provider = { provider: 'smtp', label: 'SMTP mail server', external: true, webhook: false, needs_credentials: false, settings: ['host', 'port', 'security', 'sender', 'username'], required: ['host'], note: '' }
const item = (over: Record<string, unknown> = {}) => ({
  integration_type: 'EMAIL', provider: null, provider_label: null, available_providers: [provider], is_enabled: false, status: 'NOT_CONFIGURED', message: 'Provider Not Configured',
  config: {}, credential_ref: null, credentials_present: false, webhook_path: null, webhook_secret_present: false, webhook_credential_ref: null, last_success_at: null,
  last_failure_at: null, failure_count: 0, last_error_code: null, rotated_at: null, external: true, ...over,
})
const dashboard = (items: unknown[]) => ({
  integrations: items, platform: [{ integration_type: 'MAPS', provider: null, status: 'NOT_CONFIGURED', message: 'Provider Not Configured', external: true, note: 'Distance works without a provider.' }],
  summary: { configured: 0, errors: 1, not_configured: 1, disabled: 0 },
})
const base = { 'GET /api/v1/integrations/logs': () => json(200, { items: [] }), 'GET /api/v1/payments': () => json(200, { items: [], total: 0 }) }

describe('integrations page', () => {
  it('says Provider Not Configured instead of pretending, and never shows a secret value', async () => {
    routes({ ...base, 'GET /api/v1/integrations/dashboard': () => json(200, dashboard([item(), item({ integration_type: 'SMS', provider: 'http_json', provider_label: 'HTTPS gateway', is_enabled: true, status: 'ERROR', message: 'Configured', credential_ref: 'KIRANA_INTEGRATION_SMS_TOKEN', credentials_present: true, failure_count: 3, last_error_code: 'http_500' })])) })
    page()
    const email = await screen.findByTestId('integration-EMAIL')
    expect(within(email).getAllByText('Provider Not Configured').length).toBeGreaterThan(0)
    const sms = screen.getByTestId('integration-SMS')
    expect(within(sms).getByText('Errors')).toBeInTheDocument()
    expect(within(sms).getByText('http_500')).toBeInTheDocument()
    expect(within(sms).getByText('KIRANA_INTEGRATION_SMS_TOKEN')).toBeInTheDocument() // the NAME of the variable, present or not
    expect(screen.getByText(/Nothing is shown as sent or paid unless a provider confirmed it/)).toBeInTheDocument()
    expect(screen.getByText('Distance works without a provider.')).toBeInTheDocument()
  })

  it('saves a provider with the variable NAME and only the settings that provider allows', async () => {
    let sent: Record<string, unknown> | null = null
    routes({ ...base, 'GET /api/v1/integrations/dashboard': () => json(200, dashboard([item()])), 'PUT /api/v1/integrations/EMAIL': (init) => { sent = JSON.parse(String(init?.body)); return json(200, item({ provider: 'smtp' })) } })
    page()
    const card = await screen.findByTestId('integration-EMAIL')
    fireEvent.click(within(card).getByRole('button', { name: 'Configure' }))
    fireEvent.change(within(card).getByLabelText('Mail server'), { target: { value: 'mail.example.test' } })
    fireEvent.change(within(card).getByLabelText('Port'), { target: { value: '587' } })
    fireEvent.change(within(card).getByLabelText('Credential variable name'), { target: { value: 'KIRANA_INTEGRATION_SMTP_PASSWORD' } })
    fireEvent.click(within(card).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(sent).not.toBeNull())
    expect(sent).toMatchObject({ provider: 'smtp', config: { host: 'mail.example.test', port: 587 }, credential_ref: 'KIRANA_INTEGRATION_SMTP_PASSWORD' })
  })

  it('shows the honest test result', async () => {
    routes({
      ...base,
      'GET /api/v1/integrations/dashboard': () => json(200, dashboard([item({ provider: 'smtp', provider_label: 'SMTP mail server', is_enabled: true, status: 'CONFIGURED', message: 'Configured' })])),
      'POST /api/v1/integrations/EMAIL/test': () => json(200, { ok: false, code: 'invalid_credentials', message: 'The provider did not accept the connection.' }),
    })
    page()
    fireEvent.click(await screen.findByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText(/did not accept the connection/)).toBeInTheDocument()
    expect(screen.getByText(/invalid_credentials/)).toBeInTheDocument()
  })

  it('lists payments that need review', async () => {
    routes({ ...base, 'GET /api/v1/integrations/dashboard': () => json(200, dashboard([item()])), 'GET /api/v1/payments': () => json(200, { total: 1, items: [{ id: 7, provider: 'x', method: 'UPI', purpose: 'KHATA_PAYMENT', status: 'PENDING', amount: '250.00', refunded_amount: '0.00', needs_review: true, review_reason: "The provider's amount (249.00) does not match", failure_code: null, created_at: '' }] }) })
    page()
    expect(await screen.findByText(/does not match/)).toBeInTheDocument()
  })

  it('has Hindi for every integrations string with the same placeholders', () => {
    const walk = (a: unknown, b: unknown, path: string) => {
      if (typeof a === 'string') {
        expect(typeof b, path).toBe('string')
        expect((a.match(/{{\w+}}/g) ?? []).sort(), path).toEqual(((b as string).match(/{{\w+}}/g) ?? []).sort())
        return
      }
      for (const key of Object.keys(a as object)) walk((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key], `${path}.${key}`)
    }
    walk(en.integrations, hi.integrations, 'integrations')
  })
})
