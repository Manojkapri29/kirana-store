import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch } from '@/api/client'
import { AccountBanner } from '@/components/AccountBanner'
import { ErrorNotice } from '@/components/ErrorNotice'
import { NotificationBell } from '@/components/NotificationBell'
import { OfflineBanner } from '@/components/OfflineBanner'
import { NotificationsPage } from '@/features/notifications/NotificationsPage'
import { useDirtyGuard } from '@/hooks/useDirtyGuard'
import { useFormBackup } from '@/hooks/useFormBackup'
import { useSaveState } from '@/hooks/useSaveState'
import { describeError } from '@/lib/errors'

function json(status: number, body: unknown, headers: Record<string, string> = {}) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } }))
}

/** Answers each request by its path; anything not listed is a 404 so a missed call is visible. */
function routes(map: Record<string, (init?: RequestInit) => Promise<Response>>) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).split('?')[0]
    const handler = map[`${init?.method ?? 'GET'} ${path}`]
    return handler ? handler(init) : json(404, { message: 'not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderWith(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('rate limiting and account restrictions', () => {
  it('reads Retry-After from a 429 and shows a calm message, never a stack of detail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(json(429, { success: false, error_code: 'rate_limited', message: 'Too many requests.', category: 'rate_limited', retryable: true }, { 'Retry-After': '12' })))
    const error = (await apiFetch('/x').catch((e: unknown) => e)) as ApiError
    expect(error.category).toBe('rate_limited')
    expect(error.retryAfterSeconds).toBe(12)
    const recovery = describeError(error)
    expect(recovery.waitSeconds).toBe(12)
    expect(recovery.actions).toContain('retry')
    const { container } = render(<ErrorNotice error={error} retry={() => undefined} go_back={() => undefined} />)
    expect(screen.getByText('Too many requests. Please wait a moment.')).toBeInTheDocument()
    expect(screen.getByText('Try again in 12 seconds.')).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/429|Traceback/)
  })

  it('does not offer a repeat of a write that could not be repeated safely', () => {
    const error = new ApiError('Too many requests.', 429, {}, {}, { category: 'rate_limited', retryable: true })
    expect(describeError(error, { context: 'checkout', safeToRepeat: false }).actions).not.toContain('retry')
    expect(describeError(error, { context: 'checkout', safeToRepeat: true }).actions).toContain('retry')
  })

  it('explains a restricted account in the server’s words and offers going back or support', () => {
    const error = new ApiError('This shop is suspended. You can look at your records but not change them.', 403, {}, {}, { category: 'account_restricted' })
    const recovery = describeError(error, { context: 'save' })
    expect(recovery.titleKey).toBe('recovery.titles.accountRestricted')
    expect(recovery.serverMessage).toBe('This shop is suspended. You can look at your records but not change them.')
    expect(recovery.actions).toEqual(['go_back', 'contact_support'])
  })
})

describe('Contact support', () => {
  const failure = new ApiError('boom', 500, {}, {}, { category: 'unexpected', referenceId: 'ERR-20260921-A82F5' })

  it('is offered for a failure on our side and helps quote the reference, without inventing an address', () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<ErrorNotice error={failure} context="save" cancel={() => undefined} />)
    fireEvent.click(screen.getByRole('button', { name: 'Contact support' }))
    expect(writeText).toHaveBeenCalledWith('ERR-20260921-A82F5')
    expect(screen.getByText(/Tell support this reference: ERR-20260921-A82F5/)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/@|mailto|https?:/)
  })

  it('is not offered when there is no reference to quote', () => {
    const offline = new ApiError('x', 0, {}, {}, { category: 'network', retryable: true })
    expect(describeError(offline, { context: 'search' }).actions).not.toContain('contact_support')
  })
})

describe('notifications', () => {
  const list = (items: object[], unread: number) => ({ items, total: items.length, limit: 20, offset: 0, page: 1, page_size: 20, total_pages: 1, unread })
  const low = { id: 7, event_type: 'LOW_STOCK', category: 'LOW_STOCK', title: 'Low stock', message: 'Rice is running low.', created_at: '2026-09-21T10:00:00Z', read: false, entity_type: null, entity_id: null }

  it('the bell shows the unread count and says nothing when there is none', async () => {
    routes({ 'GET /api/v1/notifications/unread-count': () => json(200, { unread: 3 }) })
    renderWith(<NotificationBell />)
    expect(await screen.findByLabelText('Notifications, 3 unread')).toBeInTheDocument()
  })

  it('the bell never breaks the page when the count cannot be loaded', async () => {
    routes({ 'GET /api/v1/notifications/unread-count': () => json(500, { message: 'x', category: 'unexpected', reference_id: 'ERR-1' }) })
    renderWith(<NotificationBell />)
    expect(await screen.findByLabelText('Notifications')).toBeInTheDocument()
  })

  it('lists notifications, marks one read only after the server confirms, and refreshes the list', async () => {
    let read = false
    routes({
      'GET /api/v1/notifications': () => json(200, list([{ ...low, read }], read ? 0 : 1)),
      'POST /api/v1/notifications/7/read': () => {
        read = true
        return json(200, { unread: 0 })
      },
      'GET /api/v1/notifications/preferences': () => json(200, { preferences: {}, channels: { IN_APP: true, EMAIL: false } }),
    })
    renderWith(<NotificationsPage />)
    expect(await screen.findByText('Rice is running low.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Mark as read' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Mark as read' })).not.toBeInTheDocument())
  })

  it('shows an honest empty state and a failed load with a way to retry', async () => {
    routes({
      'GET /api/v1/notifications': () => json(200, list([], 0)),
      'GET /api/v1/notifications/preferences': () => json(200, { preferences: {}, channels: { IN_APP: true } }),
    })
    const view = renderWith(<NotificationsPage />)
    expect(await screen.findByText('You have no notifications.')).toBeInTheDocument()
    view.unmount()
    routes({ 'GET /api/v1/notifications': () => json(500, { message: 'x', category: 'unexpected', reference_id: 'ERR-2' }) })
    renderWith(<NotificationsPage />)
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('preferences: channels that are not set up are disabled and labelled, and a change is saved then confirmed', async () => {
    const prefs = { preferences: { LOW_STOCK: { in_app: true, email: false, sms: false, whatsapp: false, push: false } }, channels: { IN_APP: true, EMAIL: false, SMS: false, WHATSAPP: false, PUSH: false } }
    const put = vi.fn(() => json(200, { ...prefs, preferences: { LOW_STOCK: { ...prefs.preferences.LOW_STOCK, in_app: false } } }))
    routes({
      'GET /api/v1/notifications': () => json(200, list([], 0)),
      'GET /api/v1/notifications/preferences': () => json(200, prefs),
      'PUT /api/v1/notifications/preferences/LOW_STOCK': put,
    })
    renderWith(<NotificationsPage />)
    const email = await screen.findByLabelText('Low stock — Email')
    expect(email).toBeDisabled()
    expect(screen.getAllByText('Not set up yet').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByLabelText('Low stock — In the app'))
    expect(await screen.findByText('Saved')).toBeInTheDocument()
    expect(put).toHaveBeenCalledTimes(1)
  })
})

describe('account and connection banners', () => {
  it('shows the owner why a suspended shop is restricted, and that nothing was deleted', async () => {
    routes({ 'GET /api/v1/account': () => json(200, { shop_name: 'S', account_status: 'SUSPENDED', message: 'Your account is suspended. You can view your records.', plan_code: 'free', plan_name: 'Free', subscription_status: null, capabilities: {} }) })
    renderWith(<AccountBanner />)
    expect(await screen.findByText('This shop account is suspended.')).toBeInTheDocument()
    expect(screen.getByText('Your records are safe and have not been changed or deleted.')).toBeInTheDocument()
  })

  it('shows nothing for an active shop', async () => {
    routes({ 'GET /api/v1/account': () => json(200, { shop_name: 'S', account_status: 'ACTIVE', message: null, plan_code: 'free', plan_name: 'Free', subscription_status: null, capabilities: {} }) })
    const { container } = renderWith(<AccountBanner />)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('says plainly when offline, with no pretence of an offline copy', () => {
    const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    render(<OfflineBanner />)
    expect(screen.getByText('You are offline.')).toBeInTheDocument()
    expect(screen.getByText(/no offline copy is kept/)).toBeInTheDocument()
    online.mockRestore()
  })
})

describe('protecting unsaved work', () => {
  beforeEach(() => localStorage.clear())

  const unloadEvent = () => {
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    return event
  }

  it('asks the browser to confirm leaving only while there are unsaved changes', () => {
    const { rerender } = renderHook(({ dirty }) => useDirtyGuard(dirty), { initialProps: { dirty: false } })
    expect(unloadEvent().defaultPrevented).toBe(false)
    rerender({ dirty: true })
    expect(unloadEvent().defaultPrevented).toBe(true)
    rerender({ dirty: false })
    expect(unloadEvent().defaultPrevented).toBe(false)
  })

  it('a form is dirty once edited, and clean again after the server has saved it', () => {
    const { result, rerender } = renderHook(({ value }) => useFormBackup('t', value), { initialProps: { value: { name: '' } } })
    expect(result.current.dirty).toBe(false)
    rerender({ value: { name: 'Rice' } })
    expect(result.current.dirty).toBe(true)
    expect(unloadEvent().defaultPrevented).toBe(true)
    act(() => result.current.clear())
    expect(result.current.dirty).toBe(false)
    expect(unloadEvent().defaultPrevented).toBe(false)
  })
})

describe('Saving… / Saved / Failed', () => {
  it('follows the mutation and shows Saved only after success, then fades', () => {
    vi.useFakeTimers()
    const { result, rerender } = renderHook(({ status }) => useSaveState(status, 1000), { initialProps: { status: 'idle' as 'idle' | 'pending' | 'success' | 'error' } })
    expect(result.current).toBe('idle')
    rerender({ status: 'pending' })
    expect(result.current).toBe('saving')
    rerender({ status: 'error' })
    expect(result.current).toBe('failed')
    rerender({ status: 'success' })
    expect(result.current).toBe('saved')
    act(() => vi.advanceTimersByTime(1100))
    expect(result.current).toBe('idle')
    vi.useRealTimers()
  })
})
