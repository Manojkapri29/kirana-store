import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionInfo } from '@/api/auth'
import { ACTIVITY_EVENT, UNAUTHORIZED_EVENT, apiFetch, apiSend } from '@/api/client'
import { ExportButtons } from '@/components/ExportButtons'
import { Button } from '@/components/ui'
import { AcceptInvitationPage } from '@/features/auth/AcceptInvitationPage'
import { AuthGate } from '@/features/auth/AuthGate'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { useCan } from '@/features/auth/authContext'
import { ChangePasswordPage } from '@/features/auth/ChangePasswordPage'
import { SessionBanner } from '@/features/auth/SessionBanner'
import { StaffPage } from '@/features/staff/StaffPage'
import { Sidebar } from '@/layouts/Sidebar'

function json(status: number, body: unknown, headers: Record<string, string> = {}) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } }))
}

type Handler = (init?: RequestInit) => Promise<Response>
function routes(map: Record<string, Handler>) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).split('?')[0]
    const handler = map[`${init?.method ?? 'GET'} ${path}`]
    return handler ? handler(init) : json(404, { message: 'not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const soon = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString()

function session(permissions: string[], over: Partial<SessionInfo> = {}): SessionInfo {
  return {
    account: { id: 1, email: 'asha@shop.test', full_name: 'Asha' },
    memberships: [{ user_id: 5, shop_id: 1, shop_name: 'Asha Store', role_code: 'CASHIER', role_name: 'Cashier', status: 'ACTIVE' }],
    active_user_id: 5, shop_id: 1, shop_name: 'Asha Store', role_code: 'CASHIER', role_name: 'Cashier',
    permissions, expires_at: soon(600), idle_minutes: 60, ...over,
  }
}

function app(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AuthProvider>{ui}</AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  document.cookie = 'kirana_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
})
afterEach(() => vi.unstubAllGlobals())

describe('the API client and the session', () => {
  it('sends the CSRF token on changes, never on reads, and reads it from the cookie the app can see', async () => {
    document.cookie = 'kirana_csrf=abc123; path=/'
    const mock = routes({ 'GET /x': () => json(200, {}), 'POST /x': () => json(200, {}) })
    await apiFetch('/x')
    await apiSend('POST', '/x', {})
    const headers = (i: number) => new Headers((mock.mock.calls[i][1] as RequestInit).headers)
    expect(headers(0).get('X-CSRF-Token')).toBeNull()
    expect(headers(1).get('X-CSRF-Token')).toBe('abc123')
  })

  it('announces a session that has ended, but not for the sign-in calls themselves', async () => {
    const seen = vi.fn()
    window.addEventListener(UNAUTHORIZED_EVENT, seen)
    routes({ 'GET /api/v1/products': () => json(401, { message: 'x', category: 'authentication' }), 'GET /api/v1/auth/me': () => json(401, { message: 'x' }) })
    await apiFetch('/api/v1/products').catch(() => undefined)
    expect(seen).toHaveBeenCalledTimes(1)
    await apiFetch('/api/v1/auth/me').catch(() => undefined)
    expect(seen).toHaveBeenCalledTimes(1)
    window.removeEventListener(UNAUTHORIZED_EVENT, seen)
  })

  it('reports activity so the idle warning knows when the person last did something', async () => {
    const seen = vi.fn()
    window.addEventListener(ACTIVITY_EVENT, seen)
    routes({ 'GET /y': () => json(200, {}) })
    await apiFetch('/y')
    expect(seen).toHaveBeenCalled()
    window.removeEventListener(ACTIVITY_EVENT, seen)
  })
})

describe('signing in', () => {
  it('asks for a sign-in when there is no session, and shows the app after a good one', async () => {
    routes({
      'GET /api/v1/auth/me': () => json(401, { message: 'x', category: 'authentication' }),
      'POST /api/v1/auth/login': () => json(200, session(['PRODUCT_VIEW'])),
    })
    app(<AuthGate>the shop</AuthGate>)
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'asha@shop.test' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'a long passphrase here' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('the shop')).toBeInTheDocument()
  })

  it('says only "not right" for a bad password, and clears the password box', async () => {
    routes({
      'GET /api/v1/auth/me': () => json(401, { message: 'x', category: 'authentication' }),
      'POST /api/v1/auth/login': () => json(401, { message: 'Invalid email or password.', category: 'authentication' }),
    })
    app(<AuthGate>the shop</AuthGate>)
    await screen.findByRole('heading', { name: 'Sign in' })
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'asha@shop.test' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong wrong wrong' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('The email or password is not right.')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toHaveValue('')
    expect(document.body.textContent).not.toContain('wrong wrong wrong')
  })

  it('a paused account is told to wait, not that the password was wrong', async () => {
    routes({
      'GET /api/v1/auth/me': () => json(401, { message: 'x', category: 'authentication' }),
      'POST /api/v1/auth/login': () => json(429, { message: 'Too many', category: 'rate_limited' }, { 'Retry-After': '60' }),
    })
    app(<AuthGate>the shop</AuthGate>)
    await screen.findByRole('heading', { name: 'Sign in' })
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@b.co' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'whatever whatever' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText(/Too many attempts/)).toBeInTheDocument()
  })

  it('a person in two shops chooses one before anything is shown', async () => {
    const two = session([], {
      active_user_id: null, shop_id: null, shop_name: null, role_code: null, role_name: null,
      memberships: [
        { user_id: 5, shop_id: 1, shop_name: 'Asha Store', role_code: 'OWNER', role_name: 'Owner', status: 'ACTIVE' },
        { user_id: 9, shop_id: 2, shop_name: 'Second Shop', role_code: 'MANAGER', role_name: 'Manager', status: 'ACTIVE' },
      ],
    })
    const chosen = vi.fn(() => json(200, session(['PRODUCT_VIEW'], { active_user_id: 9 })))
    routes({ 'GET /api/v1/auth/me': () => json(200, two), 'POST /api/v1/auth/select-shop': chosen })
    app(<AuthGate>the shop</AuthGate>)
    expect(await screen.findByRole('heading', { name: 'Choose a shop' })).toBeInTheDocument()
    expect(screen.queryByText('the shop')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Second Shop/ }))
    expect(await screen.findByText('the shop')).toBeInTheDocument()
    expect(JSON.parse((chosen.mock.calls[0] as unknown as [RequestInit])[0].body as string)).toEqual({ user_id: 9 })
  })

  it('when the session ends mid-work the page stays and a sign-in box appears over it, so nothing typed is lost', async () => {
    routes({ 'GET /api/v1/auth/me': () => json(200, session(['PRODUCT_VIEW'])), 'GET /api/v1/products': () => json(401, { message: 'x', category: 'authentication' }) })
    app(
      <AuthGate>
        <input aria-label="Draft note" defaultValue="" />
      </AuthGate>,
    )
    const note = await screen.findByLabelText('Draft note')
    fireEvent.change(note, { target: { value: 'half a bill' } })
    await apiFetch('/api/v1/products').catch(() => undefined)
    expect(await screen.findByRole('dialog', { name: 'You were signed out' })).toBeInTheDocument()
    expect(screen.getByLabelText('Draft note')).toHaveValue('half a bill')
    expect(within(screen.getByRole('dialog')).getByLabelText('Email')).toBeDisabled() // signing back in as the same person
  })

  it('warns a few minutes before the session ends and lets the person keep it', async () => {
    const mock = routes({ 'GET /api/v1/auth/me': () => json(200, session(['PRODUCT_VIEW'], { expires_at: soon(3) })) })
    app(<SessionBanner />)
    expect(await screen.findByText(/You will be signed out in [34] minutes\./)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Stay signed in' }))
    await waitFor(() => expect(mock.mock.calls.filter(([u]) => String(u).endsWith('/auth/me')).length).toBeGreaterThan(1))
  })
})

describe('what the screen shows follows the permissions (a convenience: the server decides)', () => {
  it('a cashier sees the till but not purchases, staff or reports in the menu', async () => {
    routes({ 'GET /api/v1/auth/me': () => json(200, session(['PRODUCT_VIEW', 'SALE_VIEW', 'CUSTOMER_VIEW'])) })
    app(<Sidebar open onClose={() => undefined} />)
    expect(await screen.findByRole('link', { name: /^Sales$/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^Products$/ })).toBeInTheDocument()
    for (const hidden of [/Purchases/, /^Staff$/, /^Reports$/, /Insights/, /^Plan$/, /Assistant/]) expect(screen.queryByRole('link', { name: hidden })).not.toBeInTheDocument()
  })

  it('an owner sees the staff link', async () => {
    routes({ 'GET /api/v1/auth/me': () => json(200, session(['STAFF_VIEW', 'PRODUCT_VIEW'])) })
    app(<Sidebar open onClose={() => undefined} />)
    expect(await screen.findByRole('link', { name: /^Staff$/ })).toBeInTheDocument()
  })

  it('a button that needs a permission is not drawn without it', async () => {
    routes({ 'GET /api/v1/auth/me': () => json(200, session(['SALE_CREATE'])) })
    app(
      <>
        <Button requires="SALE_CREATE">Bill</Button>
        <Button requires="PURCHASE_POST">Post purchase</Button>
      </>,
    )
    expect(await screen.findByRole('button', { name: 'Bill' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Post purchase' })).not.toBeInTheDocument()
  })

  it('download buttons need the export permission for their kind', async () => {
    routes({ 'GET /api/v1/auth/me': () => json(200, session(['INVENTORY_EXPORT'])) })
    app(
      <>
        <ExportButtons kind="inventory" />
        <ExportButtons kind="sales" />
      </>,
    )
    await waitFor(() => expect(screen.getAllByRole('button', { name: /CSV/ })).toHaveLength(1))
  })

  it('outside a provider nothing is hidden (isolated components), and the check is only ever a convenience', () => {
    function Probe() {
      return <span>{useCan()('ANYTHING') ? 'shown' : 'hidden'}</span>
    }
    render(<Probe />)
    expect(screen.getByText('shown')).toBeInTheDocument()
  })
})

describe('staff', () => {
  const member = (over: object) => ({ id: 7, name: 'Ravi', email: 'ravi@shop.test', role: { id: 3, code: 'CASHIER', name: 'Cashier', is_system: true }, status: 'ACTIVE', joined_at: '2026-09-01T10:00:00Z', last_active_at: null, permission_count: 11, is_you: false, permissions: null, ...over })
  const roles = { items: [{ id: 3, code: 'CASHIER', name: 'Cashier', description: null, is_system: true, is_active: true, permissions: [], members: 1 }, { id: 4, code: 'MANAGER', name: 'Manager', description: null, is_system: true, is_active: true, permissions: [], members: 0 }] }

  it('lists people with role and status, and marks the signed-in person', async () => {
    routes({
      'GET /api/v1/auth/me': () => json(200, session(['STAFF_VIEW'])),
      'GET /api/v1/staff': () => json(200, { items: [member({}), member({ id: 8, name: 'Asha', is_you: true, status: 'ACTIVE' })], total: 2 }),
      'GET /api/v1/staff/invitations': () => json(200, { items: [] }),
    })
    app(<StaffPage />)
    expect(await screen.findByText('Ravi')).toBeInTheDocument()
    expect(screen.getByText('You')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Invite staff' })).not.toBeInTheDocument() // no STAFF_INVITE
  })

  it('a dangerous action asks first, says what will happen, and only then calls the server', async () => {
    const suspend = vi.fn(() => json(200, member({ status: 'SUSPENDED' })))
    routes({
      'GET /api/v1/auth/me': () => json(200, session(['STAFF_VIEW', 'STAFF_SUSPEND', 'STAFF_EDIT'])),
      'GET /api/v1/staff': () => json(200, { items: [member({})], total: 1 }),
      'GET /api/v1/staff/invitations': () => json(200, { items: [] }),
      'GET /api/v1/roles': () => json(200, roles),
      'POST /api/v1/staff/7/suspend': suspend,
    })
    app(<StaffPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Suspend' }))
    expect(screen.getByText('Suspend Ravi?')).toBeInTheDocument()
    expect(screen.getByText(/signed out at once/)).toBeInTheDocument()
    expect(suspend).not.toHaveBeenCalled()
    fireEvent.click(within(screen.getByText('Suspend Ravi?').closest('[role="alert"], [role="status"]') as HTMLElement).getByRole('button', { name: 'Suspend' }))
    await waitFor(() => expect(suspend).toHaveBeenCalledTimes(1))
  })

  it('you cannot suspend or remove yourself from the screen', async () => {
    routes({
      'GET /api/v1/auth/me': () => json(200, session(['STAFF_VIEW', 'STAFF_SUSPEND'])),
      'GET /api/v1/staff': () => json(200, { items: [member({ is_you: true })], total: 1 }),
      'GET /api/v1/staff/invitations': () => json(200, { items: [] }),
    })
    app(<StaffPage />)
    await screen.findByText('Ravi')
    expect(screen.queryByRole('button', { name: 'Suspend' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument()
  })

  it('an invitation shows its link once, says no email was sent, and asks the server for nothing else', async () => {
    const created = vi.fn(() => json(201, { invitation: { id: 1, email: 'new@shop.test', role: 'Cashier', status: 'PENDING', expires_at: soon(60), created_at: soon(0) }, link: 'https://shop.example.com/accept-invitation#token=SECRETTOKEN', delivery: 'not_sent' }))
    routes({
      'GET /api/v1/auth/me': () => json(200, session(['STAFF_VIEW', 'STAFF_INVITE'])),
      'GET /api/v1/staff': () => json(200, { items: [], total: 0 }),
      'GET /api/v1/staff/invitations': () => json(200, { items: [] }),
      'GET /api/v1/roles': () => json(200, roles),
      'POST /api/v1/staff/invitations': created,
    })
    app(<StaffPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Invite staff' }))
    fireEvent.change(await screen.findByLabelText('Email'), { target: { value: 'new@shop.test' } })
    await screen.findByRole('option', { name: 'Cashier' })
    fireEvent.change(screen.getByLabelText('Role'), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create invitation' }))
    expect(await screen.findByText('The invitation is ready.')).toBeInTheDocument()
    expect(screen.getByText(/No email was sent/)).toBeInTheDocument()
    expect(screen.getByLabelText('Invitation link')).toHaveValue('https://shop.example.com/accept-invitation#token=SECRETTOKEN')
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(document.body.textContent).not.toContain('SECRETTOKEN') // gone from the screen once closed
  })
})

describe('accepting an invitation and changing a password', () => {
  it('reads the token from the link fragment, sends it in the body, and removes it from the address bar', async () => {
    window.history.replaceState(null, '', '/accept-invitation#token=' + 'T'.repeat(43))
    const accepted = vi.fn(() => json(200, session(['PRODUCT_VIEW'])))
    routes({
      'GET /api/v1/auth/me': () => json(401, { message: 'x' }),
      'POST /api/v1/auth/invitations/preview': () => json(200, { shop_name: 'Asha Store', email: 'new@shop.test', role_name: 'Cashier', has_account: false, expires_at: soon(60) }),
      'POST /api/v1/auth/invitations/accept': accepted,
    })
    app(<AcceptInvitationPage />)
    expect(await screen.findByText('You are invited to join Asha Store as Cashier.')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Your name'), { target: { value: 'New Person' } })
    fireEvent.change(screen.getByLabelText('Choose a password'), { target: { value: 'a long passphrase here' } })
    fireEvent.click(screen.getByRole('button', { name: 'Accept and sign in' }))
    await waitFor(() => expect(accepted).toHaveBeenCalledTimes(1))
    expect(JSON.parse((accepted.mock.calls[0] as unknown as [RequestInit])[0].body as string)).toEqual({ token: 'T'.repeat(43), password: 'a long passphrase here', full_name: 'New Person' })
    await waitFor(() => expect(window.location.hash).toBe(''))
  })

  it('an invalid or used invitation gets one plain message', async () => {
    window.history.replaceState(null, '', '/accept-invitation#token=' + 'U'.repeat(43))
    routes({ 'GET /api/v1/auth/me': () => json(401, { message: 'x' }), 'POST /api/v1/auth/invitations/preview': () => json(404, { message: 'This invitation is no longer valid.', category: 'not_found' }) })
    app(<AcceptInvitationPage />)
    expect(await screen.findByText(/no longer valid/)).toBeInTheDocument()
  })

  it('the new password must be typed twice the same before it can be sent', async () => {
    routes({ 'GET /api/v1/auth/me': () => json(200, session([])) })
    app(<ChangePasswordPage />)
    fireEvent.change(await screen.findByLabelText('Current password'), { target: { value: 'old old old old' } })
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'a new long passphrase' } })
    fireEvent.change(screen.getByLabelText('Type the new password again'), { target: { value: 'something else' } })
    expect(screen.getByText('The two passwords are different.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })
})
